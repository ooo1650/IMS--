from django.http import HttpResponse, Http404
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasBranchPerm, HasOrgPerm, can

from . import imports
from .models import AuditLog, log_action


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.username", read_only=True, default=None)
    branch_code = serializers.CharField(source="branch.code", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "at", "user_name", "branch_code", "action", "object_type", "object_id", "data", "ip"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Business auditors see their business (its branches, plus branch-less
    entries by its users); branch managers see their branch."""

    serializer_class = AuditLogSerializer

    def get_permissions(self):
        if can(self.request, "audit.view", org=True):
            return [IsAuthenticated()]
        return [HasBranchPerm("audit.view")()]

    def get_queryset(self):
        from django.db.models import Q

        from accounts.models import UserBranchRole

        qs = AuditLog.objects.select_related("user", "branch")
        if not can(self.request, "audit.view", org=True):
            qs = qs.filter(branch=self.request.branch)
        else:
            org = self.request.org
            users = UserBranchRole.objects.filter(org=org).values("user_id")
            qs = qs.filter(Q(branch__org=org) | Q(branch__isnull=True, user_id__in=users))
            if b := self.request.query_params.get("branch"):
                qs = qs.filter(branch_id=b)
        if a := self.request.query_params.get("action"):
            qs = qs.filter(action__startswith=a)
        if u := self.request.query_params.get("user"):
            qs = qs.filter(user__username__icontains=u)
        return qs


class ImportTemplateView(APIView):
    def get(self, request, kind):
        if kind not in imports.TEMPLATES:
            raise Http404
        resp = HttpResponse(imports.TEMPLATES[kind], content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{kind}-template.csv"'
        return resp


def _upload(request):
    f = request.FILES.get("file")
    if f is None:
        raise serializers.ValidationError({"file": "Upload a CSV file"})
    if f.size > 10 * 1024 * 1024:
        raise serializers.ValidationError({"file": "File too large (10MB max)"})
    return f.read(), request.data.get("dry_run", "true").lower() != "false"


class ProductImportView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [HasOrgPerm("catalog.manage")]

    def post(self, request):
        data, dry_run = _upload(request)
        result = imports.import_products(request.org, data, dry_run=dry_run)
        if not dry_run and result["ok"]:
            log_action(request.user, "import.products", request=request, creates=result["creates"],
                       updates=result["updates"])
        return Response(result)


class OpeningStockImportView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [HasBranchPerm("import.run")]

    def post(self, request):
        if request.branch is None:
            raise PermissionDenied("Select a branch")
        data, dry_run = _upload(request)
        result = imports.import_opening_stock(request.branch, data, request.user, dry_run=dry_run)
        if not dry_run and result["ok"]:
            log_action(request.user, "import.opening_stock", branch=request.branch, request=request,
                       imports=result["imports"], skips=result["skips"])
        return Response(result)
