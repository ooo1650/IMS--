from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_extras.models import log_action
from tenancy.middleware import SESSION_KEY, resolve_branch

from .models import User
from .permissions import HasOrgPerm
from .roles import BRANCH_ONLY_ROLES, ORG_ONLY_ROLES, ROLE_CHOICES, permissions_for
from .serializers import UserSerializer, me_payload


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET once on app load so the SPA has a csrftoken cookie to echo back."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = authenticate(request, username=request.data.get("username"), password=request.data.get("password"))
        if user is None or not user.is_active:
            log_action(None, "auth.login_failed", request=request, username=request.data.get("username", ""))
            return Response({"detail": "Invalid username or password"}, status=status.HTTP_400_BAD_REQUEST)
        login(request, user)
        branches = me_payload(user, None)["branches"]
        if len(branches) == 1:
            request.session[SESSION_KEY] = branches[0]["id"]
        log_action(user, "auth.login", request=request)
        return Response(me_payload(user, resolve_branch(user, request.session.get(SESSION_KEY))))


class LogoutView(APIView):
    def post(self, request):
        log_action(request.user, "auth.logout", request=request)
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    def get(self, request):
        return Response(me_payload(request.user, request.branch))


class RolesView(APIView):
    """Role -> permission map from roles.py, so the access screen never drifts from it."""

    def get(self, request):
        def scope(role):
            return "org" if role in ORG_ONLY_ROLES else "branch" if role in BRANCH_ONLY_ROLES else "any"

        return Response([
            {"role": role, "label": label, "scope": scope(role), "permissions": sorted(permissions_for(role))}
            for role, label in ROLE_CHOICES
        ])


class ActiveBranchView(APIView):
    def post(self, request):
        branch = resolve_branch(request.user, request.data.get("branch_id"))
        if branch is None:
            return Response({"detail": "You have no role at that branch"}, status=status.HTTP_403_FORBIDDEN)
        request.session[SESSION_KEY] = branch.id
        return Response(me_payload(request.user, branch))


class UserViewSet(viewsets.ModelViewSet):
    """Users are deactivated, never deleted (§20)."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasOrgPerm("users.manage")]
    http_method_names = ["get", "post", "patch", "put"]
    pagination_class = None

    def get_queryset(self):
        return User.objects.prefetch_related("branch_roles__branch").order_by("username")

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request.user, "user.created", user, request=self.request)

    def perform_update(self, serializer):
        user = serializer.save()
        log_action(self.request.user, "user.updated", user, request=self.request,
                   is_active=user.is_active)
