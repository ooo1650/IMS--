"""Staff list and payment entries. Owners/admins only (``staff.manage``, a
business-wide permission): branch managers never see pay."""
from rest_framework import serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from accounts.permissions import HasOrgPerm
from admin_extras.models import log_action
from admin_extras.views import _upload
from compliance import bs, exports
from ims.api import OrgFieldsMixin, OrgScopedMixin, to_int
from tenancy.models import Branch

from . import services
from .models import StaffMember, StaffPayment

STAFF_PERM = HasOrgPerm("staff.manage")


def _branch(request):
    """?branch= narrowed to this business; blank = all branches."""
    raw = request.query_params.get("branch")
    if raw in (None, ""):
        return None
    branch = Branch.objects.filter(org=request.org, pk=to_int(raw, "branch", min_value=1)).first()
    if branch is None:
        raise ValidationError({"branch": "Not a branch of this business."})
    return branch


def _month(request):
    raw = request.query_params.get("month")
    if raw in (None, ""):
        return services.month_of(timezone.localdate())
    month = services.parse_month(raw)
    if month is None:
        raise ValidationError({"month": "Use a BS month like 2083-08."})
    return month


class WholeId(serializers.PrimaryKeyRelatedField):
    """A related id that must be a whole number: DRF alone would turn 1.5 into 1."""

    def to_internal_value(self, data):
        return super().to_internal_value(to_int(data, self.field_name, min_value=1))


class StaffSerializer(OrgFieldsMixin, serializers.ModelSerializer):
    org_fields = ("branch",)
    serializer_related_field = WholeId
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = StaffMember
        fields = ["id", "branch", "branch_code", "name", "position", "phone", "joined_on", "monthly_salary_paisa",
                  "is_active", "note"]

    def validate_name(self, v):
        if not v.strip():
            raise serializers.ValidationError("Enter a name.")
        return v.strip()


class StaffViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    """No delete: someone who left is marked inactive and keeps their history."""

    queryset = StaffMember.objects.select_related("branch")
    serializer_class = StaffSerializer
    permission_classes = [STAFF_PERM]
    pagination_class = None
    http_method_names = ["get", "post", "patch"]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        log_action(self.request.user, "staff.added", serializer.instance, serializer.instance.branch, self.request)


class PaymentSerializer(serializers.ModelSerializer):
    serializer_related_field = WholeId
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    paid_on_bs = serializers.SerializerMethodField()
    month_label = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)
    month = serializers.CharField(required=False)

    class Meta:
        model = StaffPayment
        fields = ["id", "staff", "staff_name", "branch", "branch_code", "kind", "amount_paisa", "paid_on", "paid_on_bs",
                  "month", "month_label", "method", "note", "created_by_name", "created_at"]
        read_only_fields = ["branch"]

    def get_paid_on_bs(self, p):
        return bs.bs_str(p.paid_on)

    def get_month_label(self, p):
        return services.month_label(p.month)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org = self.context.get("org")
        self.fields["staff"].queryset = StaffMember.objects.filter(org=org) if org else StaffMember.objects.none()

    def validate_month(self, v):
        month = services.parse_month(v)
        if month is None:
            raise serializers.ValidationError("Use a BS month like 2083-08.")
        return month

    def validate(self, data):
        kind = data.get("kind", getattr(self.instance, "kind", None))
        method = data.get("method", getattr(self.instance, "method", ""))
        if kind == StaffPayment.DEDUCTION:
            data["method"] = ""
        elif not method:
            raise serializers.ValidationError({"method": "Choose how it was paid."})
        elif method == StaffPayment.FROM_SALARY and kind != StaffPayment.REPAYMENT:
            raise serializers.ValidationError({"method": "Only an advance repayment can be cut from salary."})
        if self.instance is None and not data.get("month"):
            data["month"] = services.month_of(data["paid_on"])
        return data


class PaymentViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    """Entries can be corrected or removed (mistakes happen); the audit log keeps
    every before/after value. List needs ?staff= or ?month=."""

    queryset = StaffPayment.objects.select_related("staff", "branch", "created_by")
    serializer_class = PaymentSerializer
    permission_classes = [STAFF_PERM]
    pagination_class = None
    org_field = "staff__org"
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            return qs
        p = self.request.query_params
        if p.get("staff") in (None, "") and p.get("month") in (None, ""):
            raise ValidationError({"detail": "Choose a staff member or a month."})
        if p.get("staff") not in (None, ""):
            qs = qs.filter(staff_id=to_int(p["staff"], "staff", min_value=1))
        if p.get("month") not in (None, ""):
            qs = qs.filter(month=_month(self.request))
        if (branch := _branch(self.request)) is not None:
            qs = qs.filter(branch=branch)
        if kind := p.get("kind"):
            if kind not in dict(StaffPayment.KINDS):
                raise ValidationError({"kind": "Unknown type."})
            qs = qs.filter(kind=kind)
        return qs[:2000]

    def perform_create(self, serializer):
        staff = serializer.validated_data["staff"]
        serializer.save(branch=staff.branch, created_by=self.request.user)

    def perform_update(self, serializer):
        staff = serializer.validated_data.get("staff")
        serializer.save(**({"branch": staff.branch} if staff and staff != serializer.instance.staff else {}))


class SummaryView(APIView):
    permission_classes = [STAFF_PERM]

    def get(self, request):
        month = _month(request)
        data = services.month_summary(request.org, month, _branch(request))
        today = services.month_of(timezone.localdate())
        months = services.recent_months()
        if month not in months:
            months = sorted({*months, month}, reverse=True)
        data.update(current_month=today, months=[{"value": m, "label": services.month_label(m)} for m in months],
                    branches=list(Branch.objects.filter(org=request.org, is_active=True).values("id", "code", "name")))
        return Response(data)


class ExportView(APIView):
    permission_classes = [STAFF_PERM]

    def get(self, request):
        fmt = request.query_params.get("export", "xlsx")
        if fmt not in ("xlsx", "csv", "pdf"):
            raise ValidationError({"export": "Choose xlsx, csv or pdf."})
        month = _month(request)
        branch = _branch(request)
        report = services.payments_report(request.org, month, branch)
        log_action(request.user, "staff.exported", branch=branch, request=request, month=month)
        return exports.response(report, fmt, f"staff-payments-{month}{'-' + branch.code if branch else ''}")


class StaffImportView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [STAFF_PERM]

    def post(self, request):
        data, dry_run = _upload(request)
        result = services.import_staff(request.org, data, dry_run=dry_run)
        if not dry_run and result["ok"]:
            log_action(request.user, "import.staff", request=request, creates=result["creates"], updates=result["updates"])
        return Response(result)


