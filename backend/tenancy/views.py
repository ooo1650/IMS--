from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated

from accounts.permissions import HasOrgPerm
from admin_extras.models import log_action

from .models import Branch, Register


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "org", "name", "code", "timezone", "address", "is_active"]


class RegisterSerializer(serializers.ModelSerializer):
    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = Register
        fields = ["id", "branch", "branch_code", "code", "name", "printer_driver", "printer_config", "is_active"]


class BranchViewSet(viewsets.ModelViewSet):
    """Read: branches the user can access. Write: org admins (branches.manage)."""

    serializer_class = BranchSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "put"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasOrgPerm("branches.manage")()]

    def get_queryset(self):
        return Branch.objects.filter(id__in=self.request.user.accessible_branch_ids())

    @action(detail=False)
    def directory(self, request):
        """All active branches (id, code, name) — e.g. transfer destinations."""
        return Response(list(Branch.objects.filter(is_active=True).values("id", "code", "name")))

    def perform_create(self, serializer):
        log_action(self.request.user, "branch.created", serializer.save(), request=self.request)

    def perform_update(self, serializer):
        branch = serializer.save()
        log_action(self.request.user, "branch.updated", branch, branch, self.request)


class RegisterViewSet(viewsets.ModelViewSet):
    serializer_class = RegisterSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "put"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasOrgPerm("branches.manage")()]

    def get_queryset(self):
        qs = Register.objects.select_related("branch").filter(branch_id__in=self.request.user.accessible_branch_ids())
        if branch := self.request.query_params.get("branch"):
            qs = qs.filter(branch_id=branch)
        elif self.request.branch and not self.request.user.org_role():
            qs = qs.filter(branch=self.request.branch)
        return qs
