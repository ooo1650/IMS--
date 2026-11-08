from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


def HasBranchPerm(*perms):
    """DRF permission: the user holds every one of ``perms`` at the active branch.

    Use per view: ``permission_classes = [HasBranchPerm("pos.sell")]``. Requests
    without a valid active branch are denied (tenant-denied path).
    """

    class _HasBranchPerm(BasePermission):
        message = "You do not have permission for this action at the active branch."

        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            branch = getattr(request._request, "branch", None)
            if branch is None:
                self.message = "No active branch selected, or you have no role at it."
                return False
            return all(user.has_branch_perm(p, branch) for p in perms)

    _HasBranchPerm.__name__ = f"HasBranchPerm({','.join(perms)})"
    return _HasBranchPerm


def HasOrgPerm(*perms):
    """DRF permission: the user holds ``perms`` through a business-wide role in
    the active business (``request.org``)."""

    class _HasOrgPerm(BasePermission):
        message = "This action needs a business-wide role in the active business."

        def has_permission(self, request, view):
            user = request.user
            org = getattr(request._request, "org", None)
            return bool(user and user.is_authenticated and all(user.has_org_perm(p, org) for p in perms))

    _HasOrgPerm.__name__ = f"HasOrgPerm({','.join(perms)})"
    return _HasOrgPerm


class PerActionPermissionMixin:
    """ViewSet mixin: ``action_perms = {"list": ("inventory.view",), ...}`` with a
    ``default_perms`` fallback. Keeps permission rules next to the viewset."""

    action_perms = {}
    default_perms = ()

    def get_permissions(self):
        perms = self.action_perms.get(self.action, self.default_perms)
        return [HasBranchPerm(*perms)()]


def can(request, perm, org=False):
    """Safe for anonymous users. org=True checks business-wide roles in the
    active business only."""
    user = request.user
    if not (user and user.is_authenticated):
        return False
    if org:
        return user.has_org_perm(perm, getattr(request, "org", None))
    return user.has_branch_perm(perm, getattr(request, "branch", None))


def require_branch_perm(request, perm):
    if not request.user.has_branch_perm(perm, request.branch):
        raise PermissionDenied(f"Missing permission {perm}")


def verify_approver(branch, username, password, perm):
    """Manager override at the register: a manager types their credentials on
    the cashier's screen. Returns the approving user or raises PermissionDenied."""
    from django.contrib.auth import authenticate

    if not username or not password:
        raise PermissionDenied("Manager approval required")
    approver = authenticate(username=username, password=password)
    if approver is None or not approver.has_branch_perm(perm, branch):
        raise PermissionDenied("Approver credentials invalid or lack permission")
    return approver


class IsPlatformAdmin(BasePermission):
    """The operator console: platform admins only (accounts.User.is_platform_admin)."""

    message = "Platform admins only."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.is_platform_admin)
