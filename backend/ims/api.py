"""Shared API plumbing."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
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


def paisa_field(**kw):
    return serializers.IntegerField(min_value=0, **kw)
