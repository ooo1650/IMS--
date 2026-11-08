from .models import Branch, Organization

BRANCH_HEADER = "HTTP_X_BRANCH_ID"
ORG_HEADER = "HTTP_X_ORG_ID"
SESSION_KEY = "active_branch_id"
ORG_SESSION_KEY = "active_org_id"


class TenantMiddleware:
    """Resolves the user's active branch and business: ``request.branch`` and
    ``request.org``.

    The branch comes from the ``X-Branch-Id`` header (the SPA sends it on every
    call) or falls back to the session. A branch the user has no role at is
    ignored — ``request.branch`` stays None and branch-scoped endpoints deny.

    The business is the active branch's business. Without a branch it comes
    from ``X-Org-Id`` / the session, or is the user's only business. Every
    business-wide query filters on ``request.org``, so one business never sees
    another's catalog, sales or settings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.branch = None
        request.org = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_active:
            raw = request.META.get(BRANCH_HEADER) or request.session.get(SESSION_KEY)
            request.branch = resolve_branch(user, raw)
            if request.branch is not None:
                request.org = request.branch.org
            else:
                request.org = resolve_org(user, request.META.get(ORG_HEADER) or request.session.get(ORG_SESSION_KEY))
        return self.get_response(request)


def resolve_branch(user, raw_branch_id):
    try:
        branch_id = int(raw_branch_id)
    except (TypeError, ValueError):
        return None
    if branch_id not in user.accessible_branch_ids():
        return None
    return Branch.objects.filter(pk=branch_id, is_active=True).first()


def resolve_org(user, raw_org_id):
    """The requested business if the user has a role in it, else their only one."""
    ids = user.org_ids()
    try:
        wanted = int(raw_org_id)
    except (TypeError, ValueError):
        wanted = None
    if wanted not in ids:
        wanted = next(iter(ids)) if len(ids) == 1 else None
    if wanted is None:
        return None
    return Organization.objects.filter(pk=wanted, is_active=True).first()
