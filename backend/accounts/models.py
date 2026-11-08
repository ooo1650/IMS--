from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.crypto import salted_hmac

from .roles import BRANCH_ONLY_ROLES, ORG_ONLY_ROLES, ROLE_CHOICES, permissions_for


class User(AbstractUser):
    """Users are deactivated (is_active=False), never deleted — sales reference them.

    Sign-in is by username or email (accounts/backends.py). ``email`` is
    unique ignoring case and is where sign-in and reset codes go."""

    email_verified = models.BooleanField(default=False)
    # Ask for an emailed code at every sign-in. Some roles always need it
    # (settings.IMS_2FA_REQUIRED_ROLES) whatever this says.
    otp_enabled = models.BooleanField(default=False)
    # Bumped to sign the user out everywhere: it is part of the session hash.
    session_epoch = models.PositiveIntegerField(default=0)
    # Operator account for the console (create businesses, branches, admins,
    # run jobs). Holds no business roles and always needs an emailed code.
    is_platform_admin = models.BooleanField(default=False)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), condition=~Q(email=""), name="uniq_user_email_ci"),
        ]

    def get_session_auth_hash(self):
        """Django checks this on every request; changing the password or the
        epoch invalidates every existing session of this user."""
        return salted_hmac("ims.session", f"{self.password}:{self.session_epoch}", algorithm="sha256").hexdigest()

    def otp_required(self):
        """True when this user must enter an emailed code to sign in."""
        from admin_extras.system import system_settings

        required = set(system_settings().otp_required_roles)
        return self.otp_enabled or self.is_platform_admin or any(r.role in required for r in self.branch_roles.all())

    def roles(self):
        return list(self.branch_roles.select_related("branch"))

    def org_ids(self):
        """Businesses this user has any role in."""
        return {r.org_id for r in self.branch_roles.all()}

    def org_role(self, org=None):
        """The user's business-wide role in ``org`` (or in their first business)."""
        org_id = getattr(org, "pk", org)
        return next((r.role for r in self.branch_roles.all()
                     if r.branch_id is None and (org_id is None or r.org_id == org_id)), None)

    def has_branch_perm(self, perm, branch):
        if not self.is_active or branch is None:
            return False
        perms = set()
        for r in self.branch_roles.all():
            # A business-wide role counts only inside its own business.
            if (r.branch_id is None and r.org_id == branch.org_id) or r.branch_id == branch.id:
                perms |= permissions_for(r.role)
        return perm in perms

    def has_org_perm(self, perm, org):
        """``perm`` through a business-wide role in ``org`` (None = no business = no)."""
        org_id = getattr(org, "pk", org)
        return bool(self.is_active and org_id) and any(
            perm in permissions_for(r.role) for r in self.branch_roles.all()
            if r.branch_id is None and r.org_id == org_id
        )

    def accessible_branch_ids(self, org=None):
        """Branches the user can open: every branch of a business where they hold
        a business-wide role, plus branches they are assigned to. ``org`` narrows
        it to one business."""
        from tenancy.models import Branch

        roles = list(self.branch_roles.all())
        org_wide = {r.org_id for r in roles if r.branch_id is None}
        ids = set(Branch.objects.filter(org_id__in=org_wide).values_list("id", flat=True)) if org_wide else set()
        ids |= {r.branch_id for r in roles if r.branch_id is not None}
        if org is not None:
            org_id = getattr(org, "pk", org)
            ids &= set(Branch.objects.filter(org_id=org_id).values_list("id", flat=True))
        return ids


class UserBranchRole(models.Model):
    """A user's role in one business: at one branch, or across the whole
    business when branch is NULL (§7). A user can hold roles in several businesses."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="branch_roles")
    org = models.ForeignKey("tenancy.Organization", on_delete=models.CASCADE, related_name="user_roles")
    branch = models.ForeignKey(
        "tenancy.Branch", null=True, blank=True, on_delete=models.CASCADE, related_name="user_roles"
    )
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "branch"], name="uniq_user_branch_role"),
            # NULLs are distinct in a unique index, so guard org-role uniqueness separately.
            models.UniqueConstraint(
                fields=["user", "org"], condition=Q(branch__isnull=True), name="uniq_user_org_role"
            ),
        ]

    def clean(self):
        if self.branch_id is None and self.role in BRANCH_ONLY_ROLES:
            raise ValidationError(f"{self.role} must be assigned to a branch")
        if self.branch_id is not None and self.role in ORG_ONLY_ROLES:
            raise ValidationError(f"{self.role} is org-scoped and cannot have a branch")

    def save(self, *args, **kwargs):
        # A branch role always belongs to that branch's business.
        if self.branch_id is not None:
            from tenancy.models import Branch

            self.org_id = Branch.objects.filter(pk=self.branch_id).values_list("org_id", flat=True).first()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} @ {self.branch or 'ORG'}: {self.role}"


class OneTimeCode(models.Model):
    """A 6-digit code sent by email (accounts/otp.py). Only a keyed hash is
    stored; a code works once, until it expires or runs out of attempts."""

    LOGIN, RESET, EMAIL = "login", "reset", "email"
    PURPOSES = [(LOGIN, "Sign-in"), (RESET, "Password reset"), (EMAIL, "Email verification")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=8, choices=PURPOSES)
    code_hash = models.CharField(max_length=64)
    sent_to = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "created_at"])]
