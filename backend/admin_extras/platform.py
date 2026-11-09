"""Operator console API (/api/v1/platform/...): platform admins only.

Everything an operator used to do with shell commands or seed scripts:
create and edit businesses (each separately registered: its own PAN,
catalog, invoices, reports), their branches and registers, their admin
accounts, per-business rules, tax and FonePay settings, deployment-wide
system settings, and the maintenance jobs. nginx exposes these endpoints
only on the console's server block; IsPlatformAdmin guards them here.
"""
import io

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.utils.text import slugify
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import otp
from accounts.models import OneTimeCode, User, UserBranchRole
from accounts.permissions import IsPlatformAdmin
from accounts.roles import BRANCH_ONLY_ROLES, ORG_ONLY_ROLES, ROLE_CHOICES
from catalog.models import Product
from compliance.models import get_settings
from compliance.views import SettingsSerializer
from ims.pagination import DefaultCursorPagination
from tenancy.models import Branch, BusinessRules, Organization, Register, rules_for
from tenancy.views import BranchSerializer, RegisterSerializer

from .models import AuditLog, SystemSettings, log_action
from .system import system_settings


class PlatformView(APIView):
    permission_classes = [IsPlatformAdmin]


def _org(pk):
    org = Organization.objects.filter(pk=pk).first()
    if org is None:
        raise Http404
    return org


# --- Businesses -----------------------------------------------------------------

class BusinessSerializer(serializers.ModelSerializer):
    branches = serializers.IntegerField(read_only=True, source="n_branches", default=0)
    users = serializers.IntegerField(read_only=True, source="n_users", default=0)
    products = serializers.IntegerField(read_only=True, source="n_products", default=0)

    class Meta:
        model = Organization
        fields = ["id", "name", "name_np", "code", "trade", "pan", "address", "phone", "email", "vat_registered",
                  "business_type", "is_active", "branches", "users", "products"]

    def validate_pan(self, v):
        if v and not (v.isdigit() and len(v) == 9):
            raise serializers.ValidationError("PAN must be 9 digits")
        if v and Organization.objects.filter(pan=v).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError("Another business already has this PAN")
        return v

    def validate_code(self, v):
        v = slugify(v or "")[:40]
        return v or None


class NewBusinessSerializer(BusinessSerializer):
    """Create a business and, optionally, its first branch and admin in one go."""

    first_branch_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    first_branch_code = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=16)
    admin_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    admin_email = serializers.EmailField(write_only=True, required=False, allow_blank=True)
    admin_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta(BusinessSerializer.Meta):
        fields = BusinessSerializer.Meta.fields + ["first_branch_name", "first_branch_code", "admin_username",
                                                   "admin_email", "admin_name"]

    def validate(self, data):
        if bool(data.get("first_branch_name")) != bool(data.get("first_branch_code")):
            raise serializers.ValidationError({"first_branch_code": "Give the first branch both a name and a code"})
        code = (data.get("first_branch_code") or "").upper()
        if code and Branch.objects.filter(code=code).exists():
            raise serializers.ValidationError({"first_branch_code": "That branch code is taken (codes start every invoice number)"})
        if data.get("admin_username"):
            if not data.get("admin_email"):
                raise serializers.ValidationError({"admin_email": "The admin needs an email for the setup and sign-in codes"})
            if User.objects.filter(username__iexact=data["admin_username"]).exists():
                raise serializers.ValidationError({"admin_username": "That username is taken"})
            if User.objects.filter(email__iexact=data["admin_email"]).exists():
                raise serializers.ValidationError({"admin_email": "Another account uses that email"})
        return data


def _unique_code(name):
    """'Everest Footwear Pvt. Ltd.' -> 'everest-footwear' (company-form words dropped)."""
    words = [w for w in slugify(name).split("-") if w not in {"pvt", "private", "ltd", "limited", "co", "inc", "pl"}]
    base = "-".join(words)[:32] or "business"
    code, n = base, 2
    while Organization.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    return code


def _businesses():
    return Organization.objects.annotate(
        n_branches=Count("branches", distinct=True),
        n_users=Count("user_roles__user", distinct=True),
        n_products=Count("products", distinct=True),
    ).order_by("name")


class BusinessListView(PlatformView):
    def get(self, request):
        return Response(BusinessSerializer(_businesses(), many=True).data)

    def post(self, request):
        s = NewBusinessSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        branch_name, branch_code = d.pop("first_branch_name", ""), (d.pop("first_branch_code", "") or "").upper()
        admin = {k: d.pop(f"admin_{k}", "") for k in ("username", "email", "name")}
        with transaction.atomic():
            d["code"] = d.get("code") or _unique_code(d["name"])
            org = Organization.objects.create(**d)
            rules_for(org)
            get_settings(org)
            branch = None
            if branch_name:
                branch = Branch.objects.create(org=org, name=branch_name, code=branch_code)
                Register.objects.create(branch=branch, code="R1", name="Till 1")
            user = None
            if admin["username"]:
                first, _, last = admin["name"].partition(" ")
                user = User(username=admin["username"], email=admin["email"].lower(), first_name=first, last_name=last)
                user.set_unusable_password()  # they choose it from the emailed setup code
                user.save()
                UserBranchRole.objects.create(user=user, org=org, role="OrgAdmin")
            log_action(request.user, "platform.business_created", org, branch, request, code=org.code,
                       first_branch=branch_code or None, admin=admin["username"] or None)
        if user is not None:
            otp.issue(user, OneTimeCode.RESET)  # "reset" code doubles as the account setup code
        return Response(BusinessSerializer(_businesses().get(pk=org.pk)).data, status=status.HTTP_201_CREATED)


class BusinessDetailView(PlatformView):
    def get(self, request, pk):
        return Response(BusinessSerializer(_businesses().filter(pk=pk).first() or _org(pk)).data)

    def patch(self, request, pk):
        org = _org(pk)
        s = BusinessSerializer(org, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.business_updated", org, request=request, fields=sorted(request.data))
        return Response(BusinessSerializer(_businesses().get(pk=org.pk)).data)


class RulesSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessRules
        fields = ["cash_rounding_paisa", "cashier_max_discount_bp", "return_approval_threshold_paisa",
                  "stocktake_variance_threshold", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_cash_rounding_paisa(self, v):
        if v not in (1, 50, 100, 500, 1000):
            raise serializers.ValidationError("Use 1 (no rounding), 50, 100 (nearest rupee), 500 or 1000")
        return v

    def validate_cashier_max_discount_bp(self, v):
        if v > 10000:
            raise serializers.ValidationError("At most 100%")
        return v


class RulesView(PlatformView):
    def get(self, request, pk):
        return Response(RulesSerializer(rules_for(_org(pk))).data)

    def patch(self, request, pk):
        org = _org(pk)
        s = RulesSerializer(rules_for(org), data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.rules_updated", org, request=request, **{k: v for k, v in s.validated_data.items()})
        return Response(s.data)


class TaxSettingsView(PlatformView):
    """The business's IRD settings (invoice series, limits, CBMS login): the same
    serializer the business's own Tax & IRD screen uses."""

    def get(self, request, pk):
        return Response(SettingsSerializer(get_settings(_org(pk))).data)

    def patch(self, request, pk):
        org = _org(pk)
        s = SettingsSerializer(get_settings(org), data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.tax_settings_updated", org, request=request,
                   fields=sorted(k for k in request.data if k != "cbms_password"))
        return Response(SettingsSerializer(get_settings(org)).data)


class FonepayView(PlatformView):
    def get(self, request, pk):
        from sales.views import FonepayMerchantSerializer, fonepay_settings_payload, merchant_rows

        org = _org(pk)
        return Response({**fonepay_settings_payload(org),
                         "results": FonepayMerchantSerializer(merchant_rows(org), many=True).data})

    def patch(self, request, pk):
        from sales.views import save_fonepay_settings

        return Response(save_fonepay_settings(request, _org(pk)))


class FonepayMerchantView(PlatformView):
    def patch(self, request, pk, branch_pk):
        from sales.views import FonepayMerchantSerializer, save_merchant

        return Response(FonepayMerchantSerializer(save_merchant(request, _org(pk), branch_pk, partial=True)).data)


# --- Branches and registers -----------------------------------------------------------

class BranchesView(PlatformView):
    def get(self, request, pk):
        org = _org(pk)
        branches = Branch.objects.filter(org=org).prefetch_related("registers").order_by("code")
        return Response([{**BranchSerializer(b).data,
                          "registers": RegisterSerializer(b.registers.all(), many=True).data} for b in branches])

    def post(self, request, pk):
        org = _org(pk)
        s = BranchSerializer(data={**request.data, "code": str(request.data.get("code", "")).upper()})
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            branch = s.save(org=org)
            if request.data.get("with_register", True):
                Register.objects.create(branch=branch, code="R1", name="Till 1")
        log_action(request.user, "platform.branch_created", branch, branch, request)
        return Response(BranchSerializer(branch).data, status=status.HTTP_201_CREATED)


class BranchDetailView(PlatformView):
    def patch(self, request, branch_pk):
        branch = Branch.objects.filter(pk=branch_pk).first()
        if branch is None:
            raise Http404
        s = BranchSerializer(branch, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.branch_updated", branch, branch, request, fields=sorted(request.data))
        return Response(s.data)


class RegistersView(PlatformView):
    def post(self, request, branch_pk):
        branch = Branch.objects.filter(pk=branch_pk).first()
        if branch is None:
            raise Http404
        s = RegisterSerializer(data={**request.data, "branch": branch.pk, "code": str(request.data.get("code", "")).upper()})
        s.is_valid(raise_exception=True)
        reg = s.save()
        log_action(request.user, "platform.register_created", reg, branch, request)
        return Response(s.data, status=status.HTTP_201_CREATED)


class RegisterDetailView(PlatformView):
    def patch(self, request, register_pk):
        reg = Register.objects.select_related("branch").filter(pk=register_pk).first()
        if reg is None:
            raise Http404
        s = RegisterSerializer(reg, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.register_updated", reg, reg.branch, request, fields=sorted(request.data))
        return Response(s.data)


# --- Users ---------------------------------------------------------------------------

class PlatformRoleSerializer(serializers.ModelSerializer):
    org_name = serializers.CharField(source="org.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True, default=None)
    role = serializers.ChoiceField(choices=ROLE_CHOICES)

    class Meta:
        model = UserBranchRole
        fields = ["org", "org_name", "branch", "branch_code", "role"]


class PlatformUserSerializer(serializers.ModelSerializer):
    """Any account, with roles across every business. Platform admins are listed
    separately and can't be given business roles."""

    roles = PlatformRoleSerializer(source="branch_roles", many=True, required=False)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    send_setup_code = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "email_verified", "otp_enabled", "is_active",
                  "is_platform_admin", "last_login", "roles", "password", "send_setup_code"]
        read_only_fields = ["email_verified", "last_login", "is_platform_admin"]

    def validate_email(self, email):
        email = email.strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError("Another account uses this email")
        return email

    def validate_password(self, password):
        if password:
            try:
                validate_password(password, self.instance)
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.messages)
        return password

    def validate_roles(self, roles):
        seen = set()
        for r in roles:
            org, branch, role = r["org"], r.get("branch"), r["role"]
            if branch is not None and branch.org_id != org.pk:
                raise serializers.ValidationError(f"Branch {branch.code} isn't part of {org.name}")
            if branch is None and role in BRANCH_ONLY_ROLES:
                raise serializers.ValidationError(f"{role} must be given a branch")
            if branch is not None and role in ORG_ONLY_ROLES:
                raise serializers.ValidationError(f"{role} applies to a whole business, not a branch")
            key = (org.pk, branch.pk if branch else None)
            if key in seen:
                raise serializers.ValidationError("One role per branch, and one business-wide role per business")
            seen.add(key)
        return roles

    def validate(self, data):
        if self.instance is not None and self.instance.is_platform_admin and data.get("branch_roles"):
            raise serializers.ValidationError({"roles": "Platform admins don't hold business roles"})
        if self.instance is None and not data.get("password") and not data.get("send_setup_code"):
            raise serializers.ValidationError({"password": "Set a password, or email a setup code"})
        if data.get("send_setup_code") and not (data.get("email") or getattr(self.instance, "email", "")):
            raise serializers.ValidationError({"email": "A setup code needs an email address"})
        return data

    @transaction.atomic
    def create(self, data):
        roles = data.pop("branch_roles", [])
        password = data.pop("password", "")
        data.pop("send_setup_code", None)
        user = User(**data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        self._set_roles(user, roles)
        return user

    @transaction.atomic
    def update(self, user, data):
        roles = data.pop("branch_roles", None)
        password = data.pop("password", "")
        data.pop("send_setup_code", None)
        if "email" in data and data["email"] != user.email:
            user.email_verified = False
        for k, v in data.items():
            setattr(user, k, v)
        if password:
            user.set_password(password)
            user.session_epoch += 1
        user.save()
        if roles is not None:
            self._set_roles(user, roles)
        return user

    @staticmethod
    def _set_roles(user, roles):
        user.branch_roles.all().delete()
        for r in roles:
            UserBranchRole(user=user, org=r["org"], branch=r.get("branch"), role=r["role"]).save()


def _users():
    return User.objects.prefetch_related("branch_roles__org", "branch_roles__branch").order_by("username")


class UsersView(PlatformView):
    def get(self, request):
        qs = _users()
        if q := request.query_params.get("q"):
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                           | Q(last_name__icontains=q))
        if org := request.query_params.get("business"):
            qs = qs.filter(branch_roles__org_id=org).distinct()
        return Response(PlatformUserSerializer(qs[:500], many=True).data)

    def post(self, request):
        s = PlatformUserSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        log_action(request.user, "platform.user_created", user, request=request,
                   roles=[f"{r.role}@{r.branch.code if r.branch else r.org.code}" for r in user.branch_roles.all()])
        if s.validated_data.get("send_setup_code"):
            otp.issue(user, OneTimeCode.RESET)
        return Response(PlatformUserSerializer(_users().get(pk=user.pk)).data, status=status.HTTP_201_CREATED)


class UserDetailView(PlatformView):
    def get(self, request, user_pk):
        user = _users().filter(pk=user_pk).first()
        if user is None:
            raise Http404
        return Response(PlatformUserSerializer(user).data)

    def patch(self, request, user_pk):
        user = _users().filter(pk=user_pk).first()
        if user is None:
            raise Http404
        if user.pk == request.user.pk and request.data.get("is_active") is False:
            return Response({"detail": "You can't deactivate your own account"}, status=400)
        s = PlatformUserSerializer(user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        log_action(request.user, "platform.user_updated", user, request=request,
                   fields=sorted(k for k in request.data if k != "password"))
        if s.validated_data.get("send_setup_code"):
            otp.issue(user, OneTimeCode.RESET)
        return Response(PlatformUserSerializer(_users().get(pk=user.pk)).data)


class UserActionView(PlatformView):
    """POST platform/users/<id>/send-reset | sign-out"""

    def post(self, request, user_pk, action):
        user = User.objects.filter(pk=user_pk).first()
        if user is None:
            raise Http404
        if action == "send-reset":
            if not user.email:
                return Response({"detail": "This account has no email address"}, status=400)
            if wait := otp.can_send(user, OneTimeCode.RESET):
                from accounts.views import wait_response

                return wait_response(wait)
            otp.issue(user, OneTimeCode.RESET)
            log_action(request.user, "platform.user_reset_sent", user, request=request)
            return Response({"detail": f"Reset code sent to {otp.mask_email(user.email)}"})
        if action == "sign-out":
            User.objects.filter(pk=user.pk).update(session_epoch=F("session_epoch") + 1)
            log_action(request.user, "platform.user_signed_out", user, request=request)
            return Response({"detail": "Signed out of every device"})
        raise Http404


# --- System settings (formerly environment variables) --------------------------------------

class SystemSettingsSerializer(serializers.ModelSerializer):
    smtp_password = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    clear_smtp_password = serializers.BooleanField(write_only=True, required=False, default=False)
    smtp_password_set = serializers.SerializerMethodField()
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True, default=None)

    class Meta:
        model = SystemSettings
        fields = ["group_name", "otp_required_roles", "login_window_minutes", "login_max_per_username",
                  "login_max_per_ip", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "clear_smtp_password",
                  "smtp_password_set", "smtp_use_tls", "from_email", "updated_at", "updated_by_name"]
        read_only_fields = ["updated_at"]

    def get_smtp_password_set(self, obj):
        return bool(obj.smtp_password_enc)

    def validate_otp_required_roles(self, roles):
        valid = {r for r, _ in ROLE_CHOICES}
        if not isinstance(roles, list) or any(r not in valid for r in roles):
            raise serializers.ValidationError(f"Choose from {', '.join(sorted(valid))}")
        return roles

    def validate(self, data):
        for k in ("login_window_minutes", "login_max_per_username", "login_max_per_ip"):
            if k in data and data[k] < 1:
                raise serializers.ValidationError({k: "Must be at least 1"})
        return data

    def update(self, obj, data):
        password, clear = data.pop("smtp_password", ""), data.pop("clear_smtp_password", False)
        for k, v in data.items():
            setattr(obj, k, v)
        if clear:
            obj.smtp_password_enc = ""
        elif password:
            obj.smtp_password = password
        obj.save()
        return obj


class SystemSettingsView(PlatformView):
    def get(self, request):
        return Response(SystemSettingsSerializer(system_settings()).data)

    def patch(self, request):
        obj = system_settings()
        s = SystemSettingsSerializer(obj, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        obj = s.save(updated_by=request.user)
        log_action(request.user, "platform.system_settings_updated", obj, request=request,
                   fields=sorted(k for k in request.data if k != "smtp_password"))
        return Response(SystemSettingsSerializer(obj).data)


class TestEmailView(PlatformView):
    """Send a test message with the saved SMTP settings, synchronously, and
    report the server's answer (the sign-in code path hides errors in the log)."""

    def post(self, request):
        from django.core.mail import send_mail

        from .mail import from_address

        to = str(request.data.get("to") or request.user.email or "").strip()
        if "@" not in to:
            return Response({"detail": "Enter an address to send the test to"}, status=400)
        s = system_settings()
        if not s.smtp_host:
            return Response({"detail": "No mail server is set: codes are written to the backend log"}, status=400)
        # Reach the server first, briefly: a blocked port drops packets silently,
        # and waiting out the SMTP timeout would outlast the proxy's.
        import socket

        try:
            socket.create_connection((s.smtp_host, s.smtp_port), timeout=6).close()
        except OSError as e:
            log_action(request.user, "platform.test_email_failed", request=request, to=to, error=str(e)[:200])
            return Response({"ok": False, "detail": (
                f"Can't reach {s.smtp_host} on port {s.smtp_port} ({e or type(e).__name__}). Some hosts block "
                "outgoing mail ports 25, 465 and 587 (Render's free plan does); if your mail provider offers "
                "port 2525, use that.")}, status=400)
        try:
            send_mail("IMS test email", "Mail from IMS is working.", from_address(), [to])
        except Exception as e:  # noqa: BLE001 — show the operator exactly what the server said
            log_action(request.user, "platform.test_email_failed", request=request, to=to, error=str(e)[:200])
            # 400, not 502: the client treats 502 as "server unreachable" and hides the detail.
            return Response({"ok": False, "detail": f"The mail server refused: {type(e).__name__}: {e}"[:300]},
                            status=400)
        log_action(request.user, "platform.test_email_sent", request=request, to=to)
        return Response({"ok": True, "detail": f"Sent to {to}"})


# --- Jobs (formerly management commands) ------------------------------------------------

JOBS = {
    "cbms_sync": ("Post pending bills and credit notes to IRD CBMS", "cbms_sync", ["--force"]),
    "reconcile_stock": ("Check every stock figure against its movement ledger", "reconcile_stock", []),
    "expire_open_sales": ("Expire unpaid FonePay QR sales past their time limit", "expire_open_sales", []),
    "reconcile_fonepay": ("Compare yesterday's FonePay settlement with recorded payments", "reconcile_fonepay", []),
    "refresh_auditor_views": ("Rebuild the read-only views given to auditors (PostgreSQL)", "refresh_auditor_views", []),
}


class JobsView(PlatformView):
    def get(self, request):
        last = {a.data.get("job"): a for a in AuditLog.objects.filter(action="platform.job_run").order_by("at")}
        return Response([{"name": k, "label": label, "last_run_at": last[k].at if k in last else None,
                          "last_ok": last[k].data.get("ok") if k in last else None}
                         for k, (label, _, _) in JOBS.items()])


class JobRunView(PlatformView):
    def post(self, request, name):
        if name not in JOBS:
            raise Http404
        _, command, args = JOBS[name]
        out = io.StringIO()
        ok = True
        try:
            call_command(command, *args, stdout=out, stderr=out)
        except Exception as e:  # noqa: BLE001 — a failed job is reported, not a 500
            ok = False
            out.write(f"\n{type(e).__name__}: {e}")
        text = out.getvalue()[-20000:]
        log_action(request.user, "platform.job_run", request=request, job=name, ok=ok, output=text[-500:])
        return Response({"ok": ok, "output": text})


class DemoSeedView(PlatformView):
    """Load the demo businesses into an EMPTY database. Only where the server
    allows it (IMS_ALLOW_DEMO_SEED), never on a live install."""

    def post(self, request):
        if not settings.IMS_ALLOW_DEMO_SEED:
            return Response({"detail": "Demo data is switched off on this server (IMS_ALLOW_DEMO_SEED)"}, status=403)
        out = io.StringIO()
        try:
            call_command("seed_demo", stdout=out)
        except Exception as e:  # noqa: BLE001
            return Response({"ok": False, "output": f"{type(e).__name__}: {e}"}, status=400)
        log_action(request.user, "platform.demo_seeded", request=request)
        return Response({"ok": True, "output": out.getvalue()})


# --- Audit log -------------------------------------------------------------------------

class PlatformAuditSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.username", default=None)
    branch_code = serializers.CharField(source="branch.code", default=None)
    business = serializers.CharField(source="branch.org.name", default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "at", "user_name", "business", "branch_code", "action", "object_type", "object_id", "data", "ip"]


class AuditView(PlatformView):
    def get(self, request):
        qs = AuditLog.objects.select_related("user", "branch__org")
        p = request.query_params
        if a := p.get("action"):
            qs = qs.filter(action__startswith=a)
        if u := p.get("user"):
            qs = qs.filter(user__username__icontains=u)
        if b := p.get("business"):
            qs = qs.filter(branch__org_id=b)
        pager = DefaultCursorPagination()
        page = pager.paginate_queryset(qs, request, view=self)
        return pager.get_paginated_response(PlatformAuditSerializer(page, many=True).data)


class OverviewView(PlatformView):
    """Console home: one line per business and the deployment's health."""

    def get(self, request):
        from compliance import cbms

        rows = []
        for org in _businesses():
            s = cbms.status_summary(org)
            rows.append({**BusinessSerializer(org).data, "cbms": {k: s[k] for k in ("enabled", "pending", "failed", "paused_reason")}})
        mail = system_settings()
        return Response({
            "businesses": rows,
            "mail_configured": bool(mail.smtp_host),
            "platform_admins": User.objects.filter(is_platform_admin=True, is_active=True).count(),
            "test_login": settings.IMS_TEST_LOGIN,
            "demo_seed_allowed": settings.IMS_ALLOW_DEMO_SEED,
            "products_total": Product.objects.count(),
        })
