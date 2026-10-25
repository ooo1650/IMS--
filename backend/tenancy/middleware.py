from .models import Branch

BRANCH_HEADER = "HTTP_X_BRANCH_ID"
SESSION_KEY = "active_branch_id"


class TenantMiddleware:
    """Resolves the user's active branch and puts it on ``request.branch``.

    The branch comes from the ``X-Branch-Id`` header (the SPA sends it on every
    call) or falls back to the session. A branch the user has no role at is
    ignored — ``request.branch`` stays None and branch-scoped endpoints deny.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.branch = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_active:
            raw = request.META.get(BRANCH_HEADER) or request.session.get(SESSION_KEY)
            request.branch = resolve_branch(user, raw)
        return self.get_response(request)


def resolve_branch(user, raw_branch_id):
    try:
        branch_id = int(raw_branch_id)
    except (TypeError, ValueError):
        return None
    if branch_id not in user.accessible_branch_ids():
        return None
    return Branch.objects.filter(pk=branch_id, is_active=True).first()
