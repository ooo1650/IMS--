"""Gapless, per-fiscal-year document numbers (§9.4; payment.md Step 2).

Series mode (ComplianceSettings.invoice_series):
  * "branch"   — one chronological series per branch + fiscal year (IRD ask):
                 KTM-2083-84-000001, CN-KTM-2083-84-000001
  * "register" — one series per register (lets offline tills number locally):
                 KTM-R1-2083-84-000001

Numbers are allocated by locking the sequence row inside the caller's
transaction, so a failed checkout rolls back and burns nothing.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone

from compliance import bs
from compliance.models import ComplianceSettings, get_settings

from .models import DocumentSequence

PREFIX = {DocumentSequence.INVOICE: "", DocumentSequence.CREDIT_NOTE: "CN-", DocumentSequence.DEBIT_NOTE: "DN-",
          DocumentSequence.PURCHASE_RETURN: "PR-"}


def fiscal_year_for(d) -> str:
    """BS fiscal year label for an AD date: '2083-84' (Shrawan 1 – Asar end)."""
    return bs.fiscal_year_label(d)


def local_today(branch):
    return timezone.localtime(timezone.now(), branch.tz).date()


def series_mode():
    return get_settings().invoice_series


def series_for(branch, register, doc_type):
    """(register_key, series label) for a document. Debit notes and purchase
    returns are always issued at branch level."""
    if (series_mode() == ComplianceSettings.REGISTER and register is not None
            and doc_type in (DocumentSequence.INVOICE, DocumentSequence.CREDIT_NOTE)):
        return register.id, register.code
    return 0, ""


def format_number(doc_type, branch, series, fiscal_year, n):
    middle = f"{series}-" if series else ""
    return f"{PREFIX[doc_type]}{branch.code}-{middle}{fiscal_year}-{n:06d}"


def _locked(branch, register_key, fiscal_year, doc_type):
    DocumentSequence.objects.get_or_create(branch=branch, register_key=register_key, fiscal_year=fiscal_year,
                                           doc_type=doc_type)
    return DocumentSequence.objects.select_for_update().get(
        branch=branch, register_key=register_key, fiscal_year=fiscal_year, doc_type=doc_type)


def allocate(doc_type, branch, register, fiscal_year):
    """-> (n, series, formatted number)."""
    key, series = series_for(branch, register, doc_type)
    seq = _locked(branch, key, fiscal_year, doc_type)
    n = seq.next_number
    seq.next_number = n + 1
    seq.save(update_fields=["next_number"])
    return n, series, format_number(doc_type, branch, series, fiscal_year, n)


def claim_offline(branch, register, fiscal_year, n):
    """Register-series mode only: an offline till used number ``n`` from its
    local copy of the series; advance the server past it (§9.10)."""
    if series_mode() != ComplianceSettings.REGISTER:
        raise ValidationError("Offline numbers can only be claimed in per-register series mode")
    if n < 1:
        raise ValidationError("Invalid offline receipt number")
    seq = _locked(branch, register.id, fiscal_year, DocumentSequence.INVOICE)
    if n >= seq.next_number:
        seq.next_number = n + 1
        seq.save(update_fields=["next_number"])
    return n, register.code, format_number(DocumentSequence.INVOICE, branch, register.code, fiscal_year, n)


def next_number(branch, register, fiscal_year, doc_type=DocumentSequence.INVOICE):
    key, _ = series_for(branch, register, doc_type)
    seq = DocumentSequence.objects.filter(branch=branch, register_key=key, fiscal_year=fiscal_year,
                                          doc_type=doc_type).first()
    return seq.next_number if seq else 1


def receipt_gaps(branch, register, fiscal_year):
    """Numbers below the series high-water mark with no invoice on the server —
    e.g. an offline sale that never synced. Reconcile against paper receipts."""
    from .models import Sale

    key, series = series_for(branch, register, DocumentSequence.INVOICE)
    high = next_number(branch, register, fiscal_year)
    used = set(Sale.objects.filter(branch=branch, fiscal_year=fiscal_year, series=series,
                                   receipt_seq__isnull=False).values_list("receipt_seq", flat=True))
    return [n for n in range(1, high) if n not in used]
