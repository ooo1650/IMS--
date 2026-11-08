"""IRD Central Billing Monitoring System (CBMS) integration (payment.md Step 4).

Endpoints (IRD CBMS API document):
    POST {base}/api/bill        — sales invoice
    POST {base}/api/billreturn  — credit note / sales return
Response codes: 200 ok · 100 credentials mismatch · 101 already exists (bill)
/ bill not found (return) · 102 save exception · 103 unknown · 104 invalid
model · 105 original bill not found (return).

Design: an outbox row (CbmsPost) is written in the same DB transaction as the
document, so nothing is ever lost; ``manage.py cbms_sync`` (cron, every
minute) posts pending rows with exponential backoff. Bills posted later than
the real-time window go with isrealtime=false. A 100 (bad credentials — e.g.
the portal password changed) pauses all posting until the password is
updated in Tax & IRD settings.

Base URL "mock://" uses an in-process double (MockCbmsRecord) for dev, tests
and listing evidence. Test login per the IRD spec: Test_CBMS / PAN 999999999.
"""
import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import bs
from .models import CbmsPost, MockCbmsRecord, get_settings

OK, CRED_MISMATCH, ALREADY_EXISTS, SAVE_ERROR, UNKNOWN, INVALID, NOT_FOUND = (
    "200", "100", "101", "102", "103", "104", "105")
RETRYABLE = {SAVE_ERROR, UNKNOWN, INVALID, "network"}
MAX_BACKOFF = timedelta(hours=1)


def rupees(paisa: int) -> float:
    return round(paisa / 100, 2)


def _money(sale_or_cn, prefix=""):
    g = lambda f: getattr(sale_or_cn, f, 0) or 0  # noqa: E731
    return {
        "taxable_sales_vat": rupees(g("taxable_sales_vat_paisa")),
        "vat": rupees(g("tax_total_paisa") if hasattr(sale_or_cn, "tax_total_paisa") else g("tax_paisa")),
        "excisable_amount": rupees(g("excisable_amount_paisa")),
        "excise": rupees(g("excise_total_paisa") if hasattr(sale_or_cn, "excise_total_paisa") else g("excise_paisa")),
        "taxable_sales_hst": rupees(g("taxable_sales_hst_paisa")),
        "hst": rupees(g("hst_total_paisa")),
        "amount_for_esf": rupees(g("amount_for_esf_paisa")),
        "esf": rupees(g("esf_total_paisa")),
        "export_sales": rupees(g("zero_rated_sales_paisa")),
        "tax_exempted_sales": rupees(g("tax_exempted_sales_paisa")),
    }


def bill_payload(sale):
    org = sale.branch.org
    return {
        "seller_pan": org.pan,
        "buyer_pan": sale.buyer_pan,
        "buyer_name": sale.buyer_name,
        "fiscal_year": bs.cbms_fiscal_year(sale.fiscal_year),
        "invoice_number": sale.receipt_number,
        "invoice_date": sale.issued_bs,
        "total_sales": rupees(sale.total_paisa),
        **_money(sale),
    }


def return_payload(cn, reason=None):
    sale = cn.original_sale
    org = sale.branch.org
    return {
        "seller_pan": org.pan,
        "buyer_pan": sale.buyer_pan,
        "buyer_name": sale.buyer_name,
        "fiscal_year": bs.cbms_fiscal_year(cn.fiscal_year),
        "ref_invoice_number": sale.receipt_number,
        "credit_note_number": cn.number,
        "credit_note_date": cn.issued_bs,
        "reason_for_return": reason or cn.reason,
        "total_sales": rupees(cn.total_paisa),
        "taxable_sales_vat": rupees(cn.taxable_sales_vat_paisa),
        "vat": rupees(cn.tax_paisa),
        "excisable_amount": 0.0,
        "excise": rupees(cn.excise_paisa),
        "taxable_sales_hst": 0.0,
        "hst": 0.0,
        "amount_for_esf": 0.0,
        "esf": 0.0,
        "export_sales": 0.0,
        "tax_exempted_sales": rupees(cn.tax_exempted_sales_paisa),
    }


def cancellation_payload(sale, reason):
    """A cancelled (voided) invoice that CBMS already knows about is reported
    as a full return. ASSUMPTION — confirm with IRD (docs/ird-questions.md)."""
    org = sale.branch.org
    return {
        "seller_pan": org.pan, "buyer_pan": sale.buyer_pan, "buyer_name": sale.buyer_name,
        "fiscal_year": bs.cbms_fiscal_year(sale.fiscal_year),
        "ref_invoice_number": sale.receipt_number,
        "credit_note_number": f"CANCEL-{sale.receipt_number}",
        "credit_note_date": bs.bs_str(bs.local_date(sale.voided_at or timezone.now(), sale.branch.tz)),
        "reason_for_return": f"Cancelled: {reason}",
        "total_sales": rupees(sale.total_paisa), **_money(sale),
    }


def enabled(org):
    """CBMS posting is switched on for this business (each has its own PAN and login)."""
    return get_settings(org).cbms_enabled


def enqueue_bill(sale):
    if not enabled(sale.branch.org):
        return None
    return CbmsPost.objects.get_or_create(key=f"bill:{sale.id}", defaults=dict(
        doc_type=CbmsPost.BILL, branch=sale.branch, sale=sale, document_number=sale.receipt_number,
        issued_at=sale.issued_at or timezone.now(), payload=bill_payload(sale)))[0]


def enqueue_return(cn):
    if not enabled(cn.branch.org):
        return None
    return CbmsPost.objects.get_or_create(key=f"billreturn:cn:{cn.id}", defaults=dict(
        doc_type=CbmsPost.RETURN, branch=cn.branch, credit_note=cn, sale=cn.original_sale,
        document_number=cn.number, issued_at=timezone.now(), payload=return_payload(cn)))[0]


def enqueue_cancellation(sale, reason):
    if not enabled(sale.branch.org) or not CbmsPost.objects.filter(key=f"bill:{sale.id}").exists():
        return None
    return CbmsPost.objects.get_or_create(key=f"billreturn:cancel:{sale.id}", defaults=dict(
        doc_type=CbmsPost.RETURN, branch=sale.branch, sale=sale, document_number=f"CANCEL-{sale.receipt_number}",
        issued_at=timezone.now(), payload=cancellation_payload(sale, reason)))[0]


# --- transport ----------------------------------------------------------------

def _mock_post(endpoint, body):
    """In-process double: duplicate bills -> 101, returns for unknown bills -> 105."""
    number = body.get("invoice_number") or body.get("credit_note_number")
    if not body.get("username") or body.get("password") == "wrong":  # test hook for code 100
        return CRED_MISMATCH
    if endpoint == "bill":
        if MockCbmsRecord.objects.filter(endpoint="bill", document_number=number).exists():
            return ALREADY_EXISTS
    else:
        if not MockCbmsRecord.objects.filter(endpoint="bill", document_number=body["ref_invoice_number"]).exists():
            return NOT_FOUND
        if MockCbmsRecord.objects.filter(endpoint="billreturn", document_number=number).exists():
            return ALREADY_EXISTS
    MockCbmsRecord.objects.create(endpoint=endpoint, document_number=number,
                                  payload={k: v for k, v in body.items() if k != "password"})
    return OK


def send(base_url, endpoint, body, timeout=15) -> str:
    """POST and return the CBMS response code as a string ('network' on transport errors)."""
    if base_url.startswith("mock://"):
        return _mock_post(endpoint, body)
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/{endpoint}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode().strip().strip('"')
    except (urllib.error.URLError, TimeoutError, OSError):
        return "network"


def post_one(post: CbmsPost, cfg=None, now=None) -> CbmsPost:
    cfg = cfg or get_settings(post.branch.org)
    now = now or timezone.now()
    realtime = (now - post.issued_at) <= timedelta(minutes=cfg.cbms_realtime_window_minutes)
    body = {"username": cfg.cbms_username, "password": cfg.cbms_password, **post.payload,
            "isrealtime": realtime, "datetimeClient": timezone.localtime(now).isoformat()}
    code = send(cfg.cbms_base_url, post.doc_type, body)
    post.attempts += 1
    post.last_code = code
    post.isrealtime = realtime
    if code == OK or (code == ALREADY_EXISTS):
        post.status, post.synced_at, post.last_error = CbmsPost.SYNCED, now, ""
    elif code == CRED_MISMATCH:
        cfg.cbms_paused_reason = "CBMS rejected the credentials (code 100). Update the CBMS password."
        cfg.save(update_fields=["cbms_paused_reason"])
        post.last_error = cfg.cbms_paused_reason
        post.next_attempt_at = now + timedelta(minutes=5)
    elif code == NOT_FOUND:
        # Original bill not at CBMS yet: wait for it to sync, then retry.
        post.last_error = "Original bill not found at CBMS (105); will retry after the bill syncs"
        post.next_attempt_at = now + timedelta(minutes=2)
    else:
        backoff = min(timedelta(minutes=2 ** min(post.attempts, 6)), MAX_BACKOFF)
        post.next_attempt_at = now + backoff
        post.last_error = f"CBMS response {code}"
        if post.attempts >= 20:
            post.status = CbmsPost.FAILED
    post.save()
    return post


def sync_pending(limit=200, now=None, force=False, org=None):
    """Post due rows, bills before returns (a return needs its bill first).
    Every business posts with its own settings (PAN, CBMS login); a business
    whose credentials were rejected is paused without holding up the others."""
    from tenancy.models import Organization

    orgs = [org] if org is not None else list(Organization.objects.all())
    done = []
    for o in orgs:
        done += _sync_org(o, limit, now, force)
    return done


def _sync_org(org, limit, now, force):
    cfg = get_settings(org)
    if not cfg.cbms_enabled:
        return []
    if cfg.cbms_paused_reason and not force:
        return []
    now = now or timezone.now()
    qs = CbmsPost.objects.filter(status=CbmsPost.PENDING, branch__org=org)
    if not force:
        qs = qs.filter(next_attempt_at__lte=now)
    done = []
    for post in qs.order_by("doc_type", "issued_at")[:limit]:  # "bill" sorts before "billreturn"
        with transaction.atomic():
            post = CbmsPost.objects.select_for_update(skip_locked=True).filter(pk=post.pk).first()
            if post is None or post.status != CbmsPost.PENDING:
                continue
            post_one(post, cfg, now)
        done.append(post)
        if post.last_code == CRED_MISMATCH:
            break
    return done


def status_summary(org, branches=None):
    from django.db.models import Count

    qs = CbmsPost.objects.filter(branch__org=org)
    if branches is not None:
        qs = qs.filter(branch__in=branches)
    counts = dict(qs.values_list("status").annotate(n=Count("id")))
    oldest = qs.filter(status=CbmsPost.PENDING).order_by("issued_at").first()
    cfg = get_settings(org)
    return {
        "enabled": cfg.cbms_enabled, "paused_reason": cfg.cbms_paused_reason,
        "pending": counts.get(CbmsPost.PENDING, 0), "synced": counts.get(CbmsPost.SYNCED, 0),
        "failed": counts.get(CbmsPost.FAILED, 0),
        "oldest_pending_at": oldest.issued_at if oldest else None,
    }
