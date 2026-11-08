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
        # (action, at): login lockout counts recent auth.login_failed rows.
        indexes = [models.Index(fields=["branch", "at"]), models.Index(fields=["action", "at"])]
        ordering = ["-at"]


def log_action(user, action, obj=None, branch=None, request=None, changes=None, **data):
    from django.utils import timezone

    from compliance import bs

    from ims.net import client_ip

    from .audit import current

    ctx = current()
    if user is None and ctx:
        user = ctx.get("user")
    ip = None
    if request is not None:
        ip = client_ip(request)
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


def _default_otp_roles():
    return ["SuperAdmin", "OrgAdmin", "BranchManager"]


class SystemSettings(models.Model):
    """Deployment-wide settings edited in the operator console (one row, pk=1).
    These used to be environment variables; secrets and security guard rails
    that must not be editable by an account stay in the environment."""

    # Shown on the sign-in page above every business, e.g. the owner's group name.
    group_name = models.CharField(max_length=200, blank=True)
    # Roles that must enter an emailed code at every sign-in.
    otp_required_roles = models.JSONField(default=_default_otp_roles, blank=True)
    # Failed sign-ins (from the audit log) before a lockout.
    login_window_minutes = models.PositiveIntegerField(default=15)
    login_max_per_username = models.PositiveIntegerField(default=5)
    login_max_per_ip = models.PositiveIntegerField(default=20)
    # Outgoing email (sign-in and reset codes). No host = codes go to the server log.
    smtp_host = models.CharField(max_length=200, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=200, blank=True)
    smtp_password_enc = models.TextField(blank=True)
    smtp_use_tls = models.BooleanField(default=True)
    from_email = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    @property
    def smtp_password(self):
        from compliance import crypto

        return crypto.decrypt(self.smtp_password_enc)

    @smtp_password.setter
    def smtp_password(self, value):
        from compliance import crypto

        self.smtp_password_enc = crypto.encrypt(value)
