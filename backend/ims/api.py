"""Shared API plumbing."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, connection
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from accounts.permissions import PerActionPermissionMixin


def exception_handler(exc, context):
    """Service-layer code raises django.core.exceptions.ValidationError; surface
    it as a 400 like DRF's own validation errors."""
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"detail": " ".join(exc.messages)}
        if getattr(exc, "lines", None):  # StockUnavailable: tell the POS which lines are short
            detail["short_lines"] = exc.lines
        exc = serializers.ValidationError(detail)
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "detail" in response.data:
        if isinstance(response.data["detail"], list):
            response.data["detail"] = " ".join(str(d) for d in response.data["detail"])
    return response


class BranchScopedMixin(PerActionPermissionMixin):
    """Filters the queryset to the active branch and stamps it on create.

    Tenant isolation lives here: every branch-scoped viewset inherits it, and
    tests cover that one branch can't read or write another's rows.
    """

    branch_field = "branch"

    def get_queryset(self):
        qs = super().get_queryset()
        branch = self.request.branch
        if branch is None:
            return qs.none()
        return qs.filter(**{self.branch_field: branch})

    def perform_create(self, serializer):
        serializer.save(**{self.branch_field: self.request.branch})


class OrgScopedMixin:
    """For business-wide tables (catalog, suppliers, customers): list only the
    active business's rows (``request.org``) and stamp it on create. With no
    active business the list is empty and writes are refused."""

    org_field = "org"

    def get_queryset(self):
        qs = super().get_queryset()
        org = getattr(self.request, "org", None)
        if org is None:
            return qs.none()
        return qs.filter(**{self.org_field: org})

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["org"] = getattr(self.request, "org", None)
        return ctx

    def perform_create(self, serializer):
        from rest_framework.exceptions import PermissionDenied

        if getattr(self.request, "org", None) is None:
            raise PermissionDenied("Choose a business first")
        serializer.save(**{self.org_field: self.request.org})


class OrgFieldsMixin:
    """Serializer mixin: related fields named in ``org_fields`` only accept rows
    of the business in ``context["org"]`` (a product can't point at another
    business's category or supplier)."""

    org_fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org = self.context.get("org")
        if org is None:
            return
        for name in self.org_fields:
            field = self.fields.get(name)
            if field is not None and getattr(field, "queryset", None) is not None:
                field.queryset = field.queryset.filter(org=org)


class HealthView(APIView):
    """Liveness + database check for container health probes. No data, no auth."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except DatabaseError:
            return Response({"status": "db-unavailable"}, status=503)
        return Response({"status": "ok"})
