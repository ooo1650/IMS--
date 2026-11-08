from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_extras.models import AuditLog, log_action
from ims.net import client_ip
from tenancy.middleware import ORG_SESSION_KEY, SESSION_KEY, resolve_branch, resolve_org

from . import otp
from .models import OneTimeCode, User
from .permissions import HasOrgPerm
from .roles import BRANCH_ONLY_ROLES, ORG_ONLY_ROLES, ROLE_CHOICES, permissions_for
from .serializers import UserSerializer, me_payload


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET once on app load so the SPA has a csrftoken cookie to echo back."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})


class BrandView(APIView):
    """Public: what the sign-in page shows before anyone signs in. Only the
    group name (or the single business's name, printed on every receipt) and
    today's date in BS and AD, in Nepal time. Nothing about users or settings."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from compliance import bs
        from tenancy.models import Organization

        from admin_extras.system import system_settings

        # One business: its own name. Several: the group name set in the console.
        orgs = list(Organization.objects.filter(is_active=True).order_by("id")[:2])
        only = orgs[0] if len(orgs) == 1 else None
        group = system_settings().group_name
        today = timezone.localdate()
        d = bs.to_bs(today)
        return Response({
            "name": group or (only.name if only else ""),
            "name_np": only.name_np if only and not group else "",
            "today_ad": today.isoformat(),
            "today_bs": f"{d.year:04d}.{d.month:02d}.{d.day:02d}",
            "today_bs_text": f"{d.day} {bs.month_name(d.month)} {d.year}",
        })


def login_locked_out(username, ip):
    """Lockout from recent failed passwords and sign-in codes in the audit log:
    shared by every gunicorn worker and container, and survives restarts."""
    from admin_extras.system import system_settings

    cfg = system_settings()  # limits are edited in the operator console
    recent = AuditLog.objects.filter(action__in=["auth.login_failed", "auth.otp_failed"],
                                     at__gte=timezone.now() - timedelta(minutes=cfg.login_window_minutes))
    by_name = recent.filter(data__username=username).count() if username else 0
    by_ip = recent.filter(ip=ip).count() if ip else 0
    return by_name >= cfg.login_max_per_username or by_ip >= cfg.login_max_per_ip


LOCKED = "Too many failed sign-ins. Wait 15 minutes or ask an admin."
PENDING_LOGIN = "pending_2fa"  # session: password checked, waiting for the emailed code
PENDING_EMAIL = "pending_email"  # session: new address waiting for its confirmation code


def _locked_response(request, key):
    log_action(None, "auth.login_locked", request=request, username=key)
    return Response({"detail": LOCKED}, status=status.HTTP_429_TOO_MANY_REQUESTS)


def _login_key(ident):
    """One lockout counter per account, whether it's tried by username or by email."""
    ident = ident.strip().lower()
    field = "email__iexact" if "@" in ident else "username__iexact"
    username = User.objects.filter(**{field: ident}).values_list("username", flat=True).first()
    return (username or ident).lower()


def _finish_login(request, user):
    login(request, user)  # rotates the session key
    branches = me_payload(user, None)["branches"]
    if len(branches) == 1:
        request.session[SESSION_KEY] = branches[0]["id"]
    log_action(user, "auth.login", request=request)
    branch = resolve_branch(user, request.session.get(SESSION_KEY))
    return Response(me_payload(user, branch, branch.org if branch else resolve_org(user, request.session.get(ORG_SESSION_KEY))))


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": detail}, status=code)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """Step 1: email or username + password. Users who need a code get
    ``{"otp_required": true}`` and finish at auth/login/verify."""

    permission_classes = [AllowAny]

    def post(self, request):
        ident = str(request.data.get("username") or request.data.get("email") or "")[:254].strip()
        key = _login_key(ident)
        if login_locked_out(key, client_ip(request)):
            return _locked_response(request, key)
        user = authenticate(request, username=ident, password=request.data.get("password"))
        if user is None or not user.is_active:
            log_action(None, "auth.login_failed", request=request, username=key)
            return _bad("Invalid email, username or password")
        request.session.pop(PENDING_LOGIN, None)
        if not user.otp_required():
            return _finish_login(request, user)
        if not user.email:
            log_action(user, "auth.login_blocked", request=request, reason="no email for sign-in code")
            return _bad("Your account needs a sign-in code, but it has no email address. Ask an administrator to add one.")
        request.session[PENDING_LOGIN] = {"uid": user.id, "at": timezone.now().timestamp()}
        live = OneTimeCode.objects.filter(user=user, purpose=OneTimeCode.LOGIN, used_at__isnull=True,
                                          expires_at__gt=timezone.now()).exists()
        wait = otp.can_send(user, OneTimeCode.LOGIN)
        if wait is None:
            otp.issue(user, OneTimeCode.LOGIN)
            log_action(user, "auth.otp_sent", request=request, purpose="login")
        elif not live:
            return _bad(wait, status.HTTP_429_TOO_MANY_REQUESTS)
        return Response({"otp_required": True, "sent_to": otp.mask_email(user.email)})


def _pending_user(request):
    p = request.session.get(PENDING_LOGIN)
    if not p or timezone.now().timestamp() - p["at"] > otp.LIFETIME[OneTimeCode.LOGIN].total_seconds():
        return None
    return User.objects.filter(pk=p["uid"], is_active=True).first()


@method_decorator(csrf_protect, name="dispatch")
class LoginVerifyView(APIView):
    """Step 2: the 6-digit code from the email."""

    permission_classes = [AllowAny]

    def post(self, request):
        user = _pending_user(request)
        if user is None:
            return _bad("Your sign-in expired. Enter your password again.")
        key = user.username.lower()
        if login_locked_out(key, client_ip(request)):
            return _locked_response(request, key)
        with transaction.atomic():
            ok = otp.verify(user, OneTimeCode.LOGIN, request.data.get("code", ""))
        if not ok:
            log_action(user, "auth.otp_failed", request=request, username=key, purpose="login")
            return _bad("That code is wrong or has expired.")
        request.session.pop(PENDING_LOGIN, None)
        if not user.email_verified:
            User.objects.filter(pk=user.pk).update(email_verified=True)
        return _finish_login(request, user)


@method_decorator(csrf_protect, name="dispatch")
class LoginResendView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = _pending_user(request)
        if user is None:
            return _bad("Your sign-in expired. Enter your password again.")
        if wait := otp.can_send(user, OneTimeCode.LOGIN):
            return _bad(wait, status.HTTP_429_TOO_MANY_REQUESTS)
        otp.issue(user, OneTimeCode.LOGIN)
        log_action(user, "auth.otp_sent", request=request, purpose="login", resend=True)
        return Response({"otp_required": True, "sent_to": otp.mask_email(user.email)})


FORGOT_REPLY = "If an account uses that email, a reset code is on its way. It expires in 15 minutes."


@method_decorator(csrf_protect, name="dispatch")
class ForgotPasswordView(APIView):
    """Same reply whether or not the email exists, so it can't be used to find accounts."""

    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip()[:254]
        ip = client_ip(request)
        hour_ago = timezone.now() - timedelta(hours=1)
        if ip and AuditLog.objects.filter(action="auth.reset_requested", ip=ip, at__gte=hour_ago).count() >= 20:
            return _bad("Too many reset requests from this network. Try again later.", status.HTTP_429_TOO_MANY_REQUESTS)
        user = User.objects.filter(email__iexact=email, is_active=True).first() if "@" in email else None
        sent = bool(user and otp.can_send(user, OneTimeCode.RESET) is None)
        if sent:
            otp.issue(user, OneTimeCode.RESET)
        log_action(user, "auth.reset_requested", request=request, email=email.lower(), sent=sent)
        return Response({"detail": FORGOT_REPLY})


@method_decorator(csrf_protect, name="dispatch")
class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        password = str(request.data.get("password", ""))
        user = User.objects.filter(email__iexact=email, is_active=True).first() if "@" in email else None
        validate_password(password, user)  # before the code is spent, so a weak password costs no retry
        with transaction.atomic():
            if user is None or not otp.verify(user, OneTimeCode.RESET, request.data.get("code", "")):
                log_action(user, "auth.reset_failed", request=request, email=email.lower())
                return _bad("That code is wrong or has expired.")
            user.set_password(password)
            user.session_epoch += 1  # every old session ends
            user.email_verified = True
            user.save(update_fields=["password", "session_epoch", "email_verified"])
        log_action(user, "auth.password_reset", request=request)
        return Response({"detail": "Password changed. Sign in with your new password."})


@method_decorator(csrf_protect, name="dispatch")
class SignupView(APIView):
    """Create your account: for people an operator or business admin invited.
    There is no open sign-up (strangers can't create accounts on a POS): the
    invite email's setup code proves the address, then the person sets their
    name and password and is signed in. Wrong codes count toward the sign-in
    lockout, and a code dies after 5 wrong tries."""

    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).strip()[:254]
        key = _login_key(email)
        if login_locked_out(key, client_ip(request)):
            return _locked_response(request, key)
        password = str(request.data.get("password", ""))
        user = User.objects.filter(email__iexact=email, is_active=True).first() if "@" in email else None
        validate_password(password, user)  # before the code is spent
        with transaction.atomic():
            if user is None or not otp.verify(user, OneTimeCode.RESET, request.data.get("code", "")):
                # Same reply whether or not the address has an account.
                log_action(user, "auth.otp_failed", request=request, username=key, purpose="signup")
                return _bad("That code is wrong or has expired. Ask whoever invited you to send a new one.")
            first = str(request.data.get("first_name", "")).strip()[:150]
            last = str(request.data.get("last_name", "")).strip()[:150]
            if first:
                user.first_name = first
            if last:
                user.last_name = last
            user.set_password(password)
            user.session_epoch += 1
            user.email_verified = True
            user.save(update_fields=["first_name", "last_name", "password", "session_epoch", "email_verified"])
        log_action(user, "auth.signup", request=request)
        request.session.pop(PENDING_LOGIN, None)
        # The emailed code just proved the address, so no second code is asked for.
        return _finish_login(request, user)


def _test_login_allowed(request):
    """IMS_TEST_LOGIN on, and the caller is on a private or loopback network."""
    import ipaddress

    if not settings.IMS_TEST_LOGIN:
        return False
    try:
        ip = ipaddress.ip_address(client_ip(request) or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


@method_decorator(csrf_protect, name="dispatch")
class TestLoginView(APIView):
    """TESTING PHASE ONLY. GET lists active users; POST {username} signs in as
    that user without a password or emailed code. 404 unless IMS_TEST_LOGIN is
    on and the request comes from a private network, so a live server never
    reveals that this exists."""

    permission_classes = [AllowAny]

    def get(self, request):
        if not _test_login_allowed(request):
            return Response(status=status.HTTP_404_NOT_FOUND)
        users = []
        for u in User.objects.filter(is_active=True).prefetch_related("branch_roles__branch").order_by("username"):
            roles = [f"{r.role} @ {r.branch.code if r.branch else 'org'}" for r in u.branch_roles.all()]
            users.append({"username": u.username, "name": u.get_full_name() or u.username, "roles": roles})
        return Response({"users": users})

    def post(self, request):
        if not _test_login_allowed(request):
            return Response(status=status.HTTP_404_NOT_FOUND)
        user = User.objects.filter(username=str(request.data.get("username", "")), is_active=True).first()
        if user is None:
            return _bad("No active user with that username.")
        request.session.pop(PENDING_LOGIN, None)
        log_action(user, "auth.test_login", request=request, username=user.username)
        return _finish_login(request, user)


class LogoutView(APIView):
    def post(self, request):
        log_action(request.user, "auth.logout", request=request)
        logout(request)
        return Response({"ok": True})


def _check_password(user, raw):
    if not user.check_password(str(raw or "")):
        raise serializers.ValidationError({"detail": "Your current password is wrong."})


class ChangePasswordView(APIView):
    """Signed-in change. Other sessions end; this one stays signed in."""

    def post(self, request):
        user = request.user
        _check_password(user, request.data.get("current_password"))
        new = str(request.data.get("new_password", ""))
        validate_password(new, user)
        user.set_password(new)
        user.session_epoch += 1
        user.save(update_fields=["password", "session_epoch"])
        update_session_auth_hash(request, user)
        log_action(user, "auth.password_changed", request=request)
        return Response({"ok": True})


class EmailChangeView(APIView):
    """Step 1 of changing your email: a code goes to the NEW address."""

    def post(self, request):
        user = request.user
        _check_password(user, request.data.get("password"))
        email = str(request.data.get("email", "")).strip().lower()
        validate_email(email)
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            return _bad("Another account already uses that email.")
        if wait := otp.can_send(user, OneTimeCode.EMAIL):
            return _bad(wait, status.HTTP_429_TOO_MANY_REQUESTS)
        otp.issue(user, OneTimeCode.EMAIL, email=email)
        request.session[PENDING_EMAIL] = email
        return Response({"sent_to": otp.mask_email(email)})


class EmailVerifyView(APIView):
    def post(self, request):
        user = request.user
        email = request.session.get(PENDING_EMAIL)
        if not email:
            return _bad("Start the email change again.")
        try:
            with transaction.atomic():
                if not otp.verify(user, OneTimeCode.EMAIL, request.data.get("code", ""), email=email):
                    return _bad("That code is wrong or has expired.")
                old = user.email
                user.email, user.email_verified = email, True
                user.save(update_fields=["email", "email_verified"])
        except IntegrityError:
            return _bad("Another account already uses that email.")
        request.session.pop(PENDING_EMAIL, None)
        log_action(user, "auth.email_changed", request=request, old=old, new=email)
        return Response(me_payload(user, request.branch, request.org))


class TwoFactorView(APIView):
    """Turn your own sign-in code on or off (not off if your role requires it)."""

    def post(self, request):
        user = request.user
        _check_password(user, request.data.get("password"))
        enabled = bool(request.data.get("enabled"))
        if enabled and not user.email:
            return _bad("Add an email address first.")
        user.otp_enabled = enabled
        if not enabled and user.otp_required():
            return _bad("Your role requires a sign-in code.")
        user.save(update_fields=["otp_enabled"])
        log_action(user, "auth.2fa_enabled" if enabled else "auth.2fa_disabled", request=request)
        return Response(me_payload(user, request.branch, request.org))


class SignOutEverywhereView(APIView):
    """End every other session of this user (lost phone, shared till)."""

    def post(self, request):
        user = request.user
        user.session_epoch += 1
        user.save(update_fields=["session_epoch"])
        update_session_auth_hash(request, user)
        log_action(user, "auth.sessions_revoked", request=request)
        return Response({"ok": True})


class MeView(APIView):
    def get(self, request):
        return Response(me_payload(request.user, request.branch, request.org))


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
        request.session[ORG_SESSION_KEY] = branch.org_id
        return Response(me_payload(request.user, branch))


class ActiveOrgView(APIView):
    """Switch business (the sidebar switcher). Clears the active branch when it
    belongs to another business."""

    def post(self, request):
        org = resolve_org(request.user, request.data.get("org_id"))
        if org is None or org.pk != _int(request.data.get("org_id")):
            return Response({"detail": "You have no role in that business"}, status=status.HTTP_403_FORBIDDEN)
        request.session[ORG_SESSION_KEY] = org.pk
        branch = resolve_branch(request.user, request.session.get(SESSION_KEY))
        if branch is not None and branch.org_id != org.pk:
            request.session.pop(SESSION_KEY, None)
            branch = None
        return Response(me_payload(request.user, branch, org))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class UserViewSet(viewsets.ModelViewSet):
    """Users are deactivated, never deleted (§20)."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, HasOrgPerm("users.manage")]
    http_method_names = ["get", "post", "patch", "put"]
    pagination_class = None

    def get_queryset(self):
        # People with any role in the active business (platform admins never show here).
        return (User.objects.filter(branch_roles__org=self.request.org, is_platform_admin=False).distinct()
                .prefetch_related("branch_roles__branch").order_by("username"))

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "org": self.request.org}

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request.user, "user.created", user, request=self.request)

    def perform_update(self, serializer):
        user = serializer.save()
        log_action(self.request.user, "user.updated", user, request=self.request,
                   is_active=user.is_active)

    @action(detail=True, methods=["post"], url_path="send-reset")
    def send_reset(self, request, pk=None):
        """Email the user a password reset code (they finish on the sign-in page)."""
        user = self.get_object()
        if not user.email:
            return _bad("This user has no email address.")
        if wait := otp.can_send(user, OneTimeCode.RESET):
            return _bad(wait, status.HTTP_429_TOO_MANY_REQUESTS)
        otp.issue(user, OneTimeCode.RESET)
        log_action(request.user, "user.reset_sent", user, request=request)
        return Response({"detail": f"Reset code sent to {otp.mask_email(user.email)}."})

    @action(detail=True, methods=["post"], url_path="sign-out")
    def sign_out(self, request, pk=None):
        """End every session of this user, e.g. a lost or stolen device."""
        user = self.get_object()
        User.objects.filter(pk=user.pk).update(session_epoch=F("session_epoch") + 1)
        log_action(request.user, "user.sessions_revoked", user, request=request)
        return Response({"ok": True})
