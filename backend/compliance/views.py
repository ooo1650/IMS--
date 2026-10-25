from django.db import transaction
from django.http import Http404, HttpResponse
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasOrgPerm, can
from admin_extras.models import log_action
from tenancy.models import Branch

from . import cbms, documents, exports, reports
from .models import CbmsPost, ComplianceSettings, TaxCategory, TaxCategoryRate, get_settings


# --- Settings ----------------------------------------------------------------

class SettingsSerializer(serializers.ModelSerializer):
    org_name = serializers.CharField(source="org.name")
    org_name_np = serializers.CharField(source="org.name_np", allow_blank=True)
    org_pan = serializers.CharField(source="org.pan", allow_blank=True, max_length=9)
    org_address = serializers.CharField(source="org.address", allow_blank=True)
    org_phone = serializers.CharField(source="org.phone", allow_blank=True)
    org_email = serializers.EmailField(source="org.email", allow_blank=True)
    org_vat_registered = serializers.BooleanField(source="org.vat_registered")
    org_business_type = serializers.ChoiceField(source="org.business_type", choices=["goods", "services", "mixed"])
    cbms_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    cbms_password_set = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceSettings
        exclude = ["id", "org", "cbms_password_enc"]
        read_only_fields = ["cbms_paused_reason", "updated_at"]

    def get_cbms_password_set(self, obj):
        return bool(obj.cbms_password_enc)

    def validate_org_pan(self, v):
        if v and not (v.isdigit() and len(v) == 9):
            raise serializers.ValidationError("PAN must be 9 digits")
        return v

    @transaction.atomic
    def update(self, obj, data):
        org_data = data.pop("org", {})
        password = data.pop("cbms_password", None)
        for k, v in org_data.items():
            setattr(obj.org, k, v)
        obj.org.save()
        for k, v in data.items():
            setattr(obj, k, v)
        if password:
            obj.cbms_password = password
            obj.cbms_paused_reason = ""  # new credentials: resume posting
        if data.get("cbms_username") is not None and "cbms_username" in data:
            obj.cbms_paused_reason = ""
        obj.save()
        return obj


class SettingsView(APIView):
    permission_classes = [HasOrgPerm("branches.manage")]

    def get(self, request):
        return Response(SettingsSerializer(get_settings()).data)

    def patch(self, request):
        s = SettingsSerializer(get_settings(), data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        obj = s.save()
        log_action(request.user, "compliance.settings_updated", obj, request=request,
                   fields=sorted(k for k in request.data if k != "cbms_password"))
        return Response(SettingsSerializer(obj).data)


# --- Tax categories ------------------------------------------------------------

class RateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxCategoryRate
        fields = ["id", "effective_from", "vat_rate", "levy_rate", "note"]


class TaxCategorySerializer(serializers.ModelSerializer):
    rates = RateSerializer(many=True, read_only=True)
    current_vat_rate = serializers.SerializerMethodField()
    current_levy_rate = serializers.SerializerMethodField()

    class Meta:
        model = TaxCategory
        fields = ["id", "code", "name", "kind", "levy_type", "is_active", "rates", "current_vat_rate",
                  "current_levy_rate"]

    def get_current_vat_rate(self, c):
        r = c.rate_on()
        return str(r.vat_rate) if r else None

    def get_current_levy_rate(self, c):
        r = c.rate_on()
        return str(r.levy_rate) if r else None


class TaxCategoryViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                         viewsets.GenericViewSet):
    """Rates are effective-dated rows: a budget change adds a rate with a new
    effective_from instead of editing history."""

    queryset = TaxCategory.objects.prefetch_related("rates")
    serializer_class = TaxCategorySerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [HasOrgPerm("catalog.manage")()]

    @action(detail=True, methods=["post"])
    def rates(self, request, pk=None):
        cat = self.get_object()
        s = RateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(category=cat)
        log_action(request.user, "compliance.rate_added", cat, request=request, **{k: str(v) for k, v in
                                                                                   s.validated_data.items()})
        return Response(TaxCategorySerializer(TaxCategory.objects.prefetch_related("rates").get(pk=cat.pk)).data)


# --- CBMS --------------------------------------------------------------------------

def _report_branches(request):
    """Org-level report users see all branches by default (or ?branch=<id>);
    branch users see only their active branch."""
    wanted = request.query_params.get("branch")
    org_ok = can(request, "reports.view", org=True) or can(request, "dashboard.view", org=True)
    if org_ok:
        if wanted and wanted != "all":
            return list(Branch.objects.filter(pk=wanted)), False
        return list(Branch.objects.all()), True
    if request.branch and can(request, "reports.view"):
        return [request.branch], False
    raise PermissionDenied("You need reports access at the active branch")


class CbmsPostSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code")

    class Meta:
        model = CbmsPost
        fields = ["id", "doc_type", "document_number", "branch_code", "issued_at", "status", "attempts", "last_code",
                  "last_error", "isrealtime", "synced_at", "next_attempt_at", "payload"]


class CbmsPostViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CbmsPostSerializer

    def get_queryset(self):
        branches, _ = _report_branches(self.request)
        qs = CbmsPost.objects.filter(branch__in=branches).select_related("branch")
        if st := self.request.query_params.get("status"):
            qs = qs.filter(status=st)
        return qs

    @action(detail=False)
    def status(self, request):
        branches, _ = _report_branches(request)
        return Response(cbms.status_summary(branches))

    @action(detail=False, methods=["post"], url_path="sync-now")
    def sync_now(self, request):
        if not can(request, "branches.manage", org=True):
            raise PermissionDenied("Org admin only")
        done = cbms.sync_pending(force=True)
        log_action(request.user, "cbms.sync_now", request=request, posted=len(done))
        return Response({"posted": len(done), **cbms.status_summary(_report_branches(request)[0])})

    @action(detail=False, methods=["post"], url_path="retry-failed")
    def retry_failed(self, request):
        if not can(request, "branches.manage", org=True):
            raise PermissionDenied("Org admin only")
        n = CbmsPost.objects.filter(status=CbmsPost.FAILED).update(status=CbmsPost.PENDING, attempts=0)
        log_action(request.user, "cbms.retry_failed", request=request, count=n)
        return Response({"requeued": n, **cbms.status_summary(_report_branches(request)[0])})


# --- Reports ---------------------------------------------------------------------

class ComplianceReportView(APIView):
    def get(self, request, name):
        fn = reports.REPORTS.get(name)
        if fn is None:
            raise Http404
        branches, org_wide = _report_branches(request)
        if name == "audit-trail" and not (can(request, "audit.view", org=True) or can(request, "audit.view")):
            raise PermissionDenied("Audit trail needs audit.view")
        period = reports.Period(request.query_params.get("fy"), request.query_params.get("month"))
        report = fn(period, branches, org_wide) if name == "audit-trail" else fn(period, branches)
        # ?export= (not ?format=, which DRF reserves for content negotiation)
        fmt = request.query_params.get("export", "json")
        if fmt in exports.RENDERERS:
            log_action(request.user, "report.exported", request=request, report=name, format=fmt,
                       period=period.label)
            stem = f"{name}-{period.fy}" + (f"-m{period.month:02d}" if period.month else "")
            return exports.response(report, fmt, stem)
        report = dict(report)
        report["columns"] = [{"key": k, "label": l, "kind": kind} for k, l, kind in report["columns"]]
        return Response(report)


class AccountantExportView(APIView):
    """Zip of the books an accountant needs for the VAT return (Excel + XML)."""

    def get(self, request):
        branches, _ = _report_branches(request)
        period = reports.Period(request.query_params.get("fy"), request.query_params.get("month"))
        pack = {name: reports.REPORTS[name](period, branches) for name in reports.ACCOUNTANT_PACK}
        log_action(request.user, "report.accountant_export", request=request, period=period.label)
        resp = HttpResponse(exports.zip_reports(pack), content_type="application/zip")
        stem = f"accountant-{period.fy}" + (f"-m{period.month:02d}" if period.month else "")
        resp["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
        return resp


class TurnoverView(APIView):
    def get(self, request):
        branches, _ = _report_branches(request)
        return Response(reports.turnover(branches))


class FiscalYearsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(reports.fiscal_years())


class DocumentView(APIView):
    """Preview (watermarked) or PDF export of a tax document. Printing goes
    through POST .../print/ so the print-once rule is recorded."""

    def get(self, request, doc_type, pk):
        from sales.models import CreditNote, DebitNote, Sale

        model, builder = {"invoice": (Sale, documents.invoice_context),
                          "credit_note": (CreditNote, documents.credit_note_context),
                          "debit_note": (DebitNote, documents.debit_note_context)}.get(doc_type, (None, None))
        if model is None:
            raise Http404
        branches, _ = _report_branches(request)
        obj = model.objects.filter(pk=pk, branch__in=branches).first()
        if obj is None or (doc_type == "invoice" and not obj.receipt_number):
            raise Http404
        ctx = builder(obj, None)
        if request.query_params.get("export") == "pdf":
            resp = HttpResponse(documents.render_pdf(ctx), content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="{ctx["number"]}.pdf"'
            return resp
        return HttpResponse(documents.render_html(ctx))
