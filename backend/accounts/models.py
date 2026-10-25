from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .roles import BRANCH_ONLY_ROLES, ORG_ONLY_ROLES, ROLE_CHOICES, permissions_for


class User(AbstractUser):
    """Users are deactivated (is_active=False), never deleted — sales reference them."""

    def roles(self):
        return list(self.branch_roles.select_related("branch"))

    def org_role(self):
        return next((r.role for r in self.branch_roles.all() if r.branch_id is None), None)

    def role_at(self, branch):
        """The role that applies at ``branch``. A branch role wins over an org
        role so an OrgAdmin can be given a narrower day-to-day role somewhere."""
        roles = list(self.branch_roles.all())
        if branch is not None:
            for r in roles:
                if r.branch_id == branch.id:
                    return r.role
        for r in roles:
            if r.branch_id is None:
                return r.role
        return None

    def has_branch_perm(self, perm, branch):
        if not self.is_active:
            return False
        roles = list(self.branch_roles.all())
        perms = set()
        for r in roles:
            if r.branch_id is None or (branch is not None and r.branch_id == branch.id):
                perms |= permissions_for(r.role)
        return perm in perms

    def has_org_perm(self, perm):
        return self.is_active and any(
            perm in permissions_for(r.role) for r in self.branch_roles.all() if r.branch_id is None
        )

    def accessible_branch_ids(self):
        from tenancy.models import Branch

        roles = list(self.branch_roles.all())
        if any(r.branch_id is None for r in roles):
            return set(Branch.objects.values_list("id", flat=True))
        return {r.branch_id for r in roles}


class UserBranchRole(models.Model):
    """A user's role at one branch. branch=NULL means org-scoped (§7)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="branch_roles")
    branch = models.ForeignKey(
        "tenancy.Branch", null=True, blank=True, on_delete=models.CASCADE, related_name="user_roles"
    )
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "branch"], name="uniq_user_branch_role"),
            # NULLs are distinct in a unique index, so guard org-role uniqueness separately.
            models.UniqueConstraint(
                fields=["user"], condition=Q(branch__isnull=True), name="uniq_user_org_role"
            ),
        ]

    def clean(self):
        if self.branch_id is None and self.role in BRANCH_ONLY_ROLES:
            raise ValidationError(f"{self.role} must be assigned to a branch")
        if self.branch_id is not None and self.role in ORG_ONLY_ROLES:
            raise ValidationError(f"{self.role} is org-scoped and cannot have a branch")

    def __str__(self):
        return f"{self.user} @ {self.branch or 'ORG'}: {self.role}"
