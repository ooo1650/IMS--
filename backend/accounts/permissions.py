from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


def HasBranchPerm(*perms):
    """DRF permission: the user holds every one of ``perms`` at the active branch.

    Use per view: ``permission_classes = [HasBranchPerm("pos.sell")]``. Requests
    without a valid active branch are denied (tenant-denied path).
    """

    class _HasBranchPerm(BasePermission):
        message = "Your role at this branch doesn't allow this. Ask a manager or administrator if you need it."

        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            branch = getattr(request._request, "branch", None)
            if branch is None:
                self.message = "Choose a branch first (the branch picker in the sidebar). If yours isn't listed, ask an administrator for access."
                return False
            return all(user.has_branch_perm(p, branch) for p in perms)

    _HasBranchPerm.__name__ = f"HasBranchPerm({','.join(perms)})"
    return _HasBranchPerm


def HasOrgPerm(*perms):
    """DRF permission: the user holds ``perms`` through a business-wide role in
    the active business (``request.org``)."""

    class _HasOrgPerm(BasePermission):
        message = "This needs a business-wide role (owner or admin) in this business. Ask an administrator if you need it."

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


class ApprovalDenied(PermissionDenied):
    """Wrong manager credentials at the register. ``failed_login_key`` is the
    account to charge with a failed sign-in; ``ims.api.exception_handler``
    writes that audit row after the checkout transaction has rolled back (a row
    written inside it would vanish and never count toward the lockout)."""

    def __init__(self, detail, failed_login_key=None):
        super().__init__(detail)
        self.failed_login_key = failed_login_key


def verify_approver(branch, username, password, perm, request=None):
    """Manager override at the register: a manager types their credentials on
    the cashier's screen. Returns the approving user or raises ApprovalDenied.

    Shares the sign-in lockout: wrong passwords here count as failed sign-ins
    for that manager, so a register can't be used to guess a password."""
    from django.contrib.auth import authenticate

    from accounts.views import _login_key, login_locked_out
    from ims.net import client_ip

    if not username or not password:
        raise ApprovalDenied("Manager approval required")
    key = _login_key(str(username)[:254])
    if locked := login_locked_out(key, client_ip(request) if request is not None else None):
        from accounts.otp import wait_text

        raise ApprovalDenied(f"Too many wrong passwords for this manager. Try again in {wait_text(locked)}.")
    approver = authenticate(request, username=username, password=password)
    if approver is None or not approver.is_active:
        raise ApprovalDenied("Approver credentials invalid or lack permission", failed_login_key=key)
    if not approver.has_branch_perm(perm, branch):
        raise ApprovalDenied("Approver credentials invalid or lack permission")
    return approver


class IsPlatformAdmin(BasePermission):
    """The operator console: platform admins only (accounts.User.is_platform_admin)."""

    message = "Platform admins only."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.is_platform_admin)
