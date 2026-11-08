"""Generic before/after audit of every write to audited models (payment.md
Step 2: "An audit log records every write: user, timestamp, and before/after
values").

AuditContextMiddleware puts the acting user/branch/IP in a contextvar; model
signals diff the row against its stored version and append an AuditLog row.
Business events (sale paid, invoice printed, …) are also logged explicitly by
the services via ``log_action``.
"""
import contextvars
import datetime
import decimal

from django.db.models.signals import post_delete, post_save, pre_save

from ims.net import client_ip

_ctx = contextvars.ContextVar("audit_ctx", default=None)

# Fields never written to the audit log.
REDACT = {"password", "cbms_password_enc", "password_enc", "secret_enc"}
SKIP = {"updated_at", "last_login"}


def current():
    return _ctx.get()


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _ctx.set({
            "user": user if (user is not None and user.is_authenticated) else None,
            "branch": getattr(request, "branch", None),
            "ip": client_ip(request),
        })
        try:
            return self.get_response(request)
        finally:
            _ctx.reset(token)


def _plain(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    return v


def _snapshot(instance):
    out = {}
    for f in instance._meta.concrete_fields:
        if f.name in SKIP:
            continue
        out[f.name] = "***" if f.name in REDACT else _plain(getattr(instance, f.attname))
    return out


def _pre_save(sender, instance, **kwargs):
    if instance.pk and not instance._state.adding:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._audit_before = _snapshot(old) if old is not None else None
    else:
        instance._audit_before = None


def _post_save(sender, instance, created, **kwargs):
    from .models import log_action

    after = _snapshot(instance)
    before = getattr(instance, "_audit_before", None)
    if created or before is None:
        changes = {k: [None, v] for k, v in after.items() if v not in (None, "", 0, False)}
        action = "created"
    else:
        changes = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
        if not changes:
            return
        action = "updated"
    ctx = current() or {}
    branch = getattr(instance, "branch", None) if hasattr(instance, "branch_id") else None
    log_action(ctx.get("user"), f"{sender._meta.model_name}.{action}", instance, branch=branch or ctx.get("branch"),
               changes=changes)


def _post_delete(sender, instance, **kwargs):
    from .models import log_action

    ctx = current() or {}
    log_action(ctx.get("user"), f"{sender._meta.model_name}.deleted", instance, branch=ctx.get("branch"),
               changes={k: [v, None] for k, v in _snapshot(instance).items()})


AUDITED = [
    "accounts.User", "accounts.UserBranchRole", "tenancy.Organization", "tenancy.Branch", "tenancy.Register",
    "catalog.Category", "catalog.Supplier", "catalog.Product", "catalog.Barcode", "catalog.BranchPrice",
    "inventory.InventoryItem", "inventory.StockTransfer", "inventory.Stocktake",
    "sales.Sale", "sales.CreditNote", "sales.DebitNote", "sales.Customer", "sales.CashierShift",
    "sales.FonepayMerchant", "sales.FonepaySettings",
    "purchasing.PurchaseOrder", "purchasing.GoodsReceipt", "purchasing.PurchaseReturn",
    "compliance.ComplianceSettings", "compliance.TaxCategory", "compliance.TaxCategoryRate",
]


def connect():
    from django.apps import apps

    for label in AUDITED:
        model = apps.get_model(label)
        pre_save.connect(_pre_save, sender=model, dispatch_uid=f"audit-pre-{label}")
        post_save.connect(_post_save, sender=model, dispatch_uid=f"audit-post-{label}")
        post_delete.connect(_post_delete, sender=model, dispatch_uid=f"audit-del-{label}")
