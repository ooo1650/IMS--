from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated

from accounts.permissions import HasOrgPerm
from admin_extras.models import log_action
from ims.api import OrgFieldsMixin

from .models import branch_colors, Branch, Register


class BranchSerializer(serializers.ModelSerializer):
    # ``color`` is what an admin chose ("" = automatic); ``color_shown`` is what the app uses.
    color_shown = serializers.SerializerMethodField()

    class Meta:
        model = Branch
        fields = ["id", "org", "name", "code", "timezone", "address", "is_active", "color", "color_shown"]
        read_only_fields = ["org"]  # a branch belongs to the active business; it never moves

    def validate_color(self, v):
        return v or None  # "" from the form = automatic

    def to_representation(self, b):
        data = super().to_representation(b)
        data["color"] = data["color"] or ""
        return data

    def get_color_shown(self, b):
        cache = self.context.setdefault("_colors", {})
        if b.org_id not in cache:
            cache[b.org_id] = branch_colors(b.org_id)
        return cache[b.org_id].get(b.id, "grey")


class RegisterSerializer(OrgFieldsMixin, serializers.ModelSerializer):
    org_fields = ("branch",)  # registers only on the active business's branches

    branch_code = serializers.CharField(source="branch.code", read_only=True)

    class Meta:
        model = Register
        fields = ["id", "branch", "branch_code", "code", "name", "printer_driver", "printer_config", "is_active"]


class BranchViewSet(viewsets.ModelViewSet):
    """Branches of the active business that the user can access. Write: that
    business's admins (branches.manage)."""

    serializer_class = BranchSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "put"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasOrgPerm("branches.manage")()]

    def get_queryset(self):
        return Branch.objects.filter(id__in=self.request.user.accessible_branch_ids(self.request.org))

    @action(detail=False)
    def directory(self, request):
        """Active branches of the active business (id, code, name), e.g. transfer destinations."""
        org = getattr(request, "org", None)
        return Response(list(Branch.objects.filter(org=org, is_active=True).values("id", "code", "name")))

    def perform_create(self, serializer):
        if self.request.org is None:
            raise PermissionDenied("Choose a business first")
        log_action(self.request.user, "branch.created", serializer.save(org=self.request.org), request=self.request)

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

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "org": self.request.org}

    def get_queryset(self):
        qs = Register.objects.select_related("branch").filter(
            branch_id__in=self.request.user.accessible_branch_ids(self.request.org))
        if branch := self.request.query_params.get("branch"):
            qs = qs.filter(branch_id=branch)
        elif self.request.branch and not self.request.user.org_role(self.request.org):
            qs = qs.filter(branch=self.request.branch)
        return qs
