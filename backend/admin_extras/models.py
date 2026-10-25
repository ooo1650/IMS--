from django.conf import settings
from django.db import models

from compliance.appendonly import AppendOnlyModel


class AuditLog(AppendOnlyModel):
    """Who did what, where, when — with before/after values (§20, §21,
    payment.md Step 2). Append-only."""

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    at_bs = models.CharField(max_length=10, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    branch = models.ForeignKey("tenancy.Branch", null=True, blank=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=64, db_index=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    data = models.JSONField(default=dict, blank=True)
    # Field-level changes: {"field": [before, after]} (created: before = null).
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["branch", "at"])]
        ordering = ["-at"]


def log_action(user, action, obj=None, branch=None, request=None, changes=None, **data):
    from django.utils import timezone

    from compliance import bs

    from .audit import current

    ctx = current()
    if user is None and ctx:
        user = ctx.get("user")
    ip = None
    if request is not None:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
            "REMOTE_ADDR"
        )
    elif ctx:
        ip = ctx.get("ip")
    if branch is None and ctx:
        branch = ctx.get("branch")
    return AuditLog.objects.create(
        user=user if (user is not None and user.is_authenticated) else None,
        branch=branch,
        at_bs=bs.bs_str(timezone.localdate()),
        action=action,
        object_type=type(obj).__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        data=data,
        changes=changes or {},
        ip=ip or None,
    )
