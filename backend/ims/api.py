"""Shared API plumbing."""
import decimal
import logging
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, DataError, connection
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from accounts.permissions import PerActionPermissionMixin

log = logging.getLogger("ims.api")


def exception_handler(exc, context):
    """Service-layer code raises django.core.exceptions.ValidationError; surface
    it as a 400 like DRF's own validation errors."""
    _record_failed_approval(exc, context)
    if isinstance(exc, (OverflowError, DataError, decimal.InvalidOperation)):
        # A number the field validation should have caught (too large for the
        # database, not a valid decimal). Tell the user plainly; log it so the
        # missing validation gets fixed.
        log.warning("number rejected late on %s: %r", getattr(context.get("request"), "path", "?"), exc)
        exc = serializers.ValidationError({"detail": "One of the numbers entered is too large or isn't valid. "
                                                     "Check the amounts and quantities and try again."})
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"detail": " ".join(exc.messages)}
        if getattr(exc, "lines", None):  # StockUnavailable: tell the POS which lines are short
            detail["short_lines"] = exc.lines
        exc = serializers.ValidationError(detail)
    response = drf_exception_handler(exc, context)
    if response is not None and response.status_code == 400:
        response.data = plain_errors(response.data)
    from rest_framework.exceptions import NotAuthenticated

    if isinstance(exc, NotAuthenticated) and response is not None:
        response.data = {"detail": "You're signed out. Sign in again to continue.", "code": "not_authenticated"}
    if response is not None and response.status_code == 403 and str(getattr(exc, "detail", "")).startswith("CSRF Failed"):
        from .errors import SESSION_EXPIRED

        response.data = {"detail": SESSION_EXPIRED, "code": "csrf"}
    if response is not None and isinstance(response.data, dict) and "detail" in response.data:
        if isinstance(response.data["detail"], list):
            response.data["detail"] = " ".join(str(d) for d in response.data["detail"])
    return response


def _record_failed_approval(exc, context):
    """A wrong manager password at the register (accounts.permissions.ApprovalDenied)
    counts as a failed sign-in for that manager. Logged here, after the
    checkout's transaction has rolled back, so the row survives."""
    key = getattr(exc, "failed_login_key", None)
    if not key:
        return
    from admin_extras.models import log_action

    log_action(None, "auth.login_failed", request=context.get("request"), username=key, via="approval")


_MISSING = object()

# Upper bounds for typed-in numbers: far above any real shop figure, far below
# the 64-bit database limit, so a fat-fingered "1000000000000000000000" is a
# clear 400 instead of a database overflow (500).
MAX_PAISA = 10 ** 13  # Rs 100 billion
MAX_QTY = 10_000_000
MAX_INT = 10 ** 15


def to_int(value, field, default=_MISSING, *, min_value=None, max_value=MAX_INT):
    """Whole number from request input, or a 400 naming the field (never a 500).

    Accepts 12, "12", " 12 " and other scripts' digits ("१२"); refuses 1.5,
    "1.5", "1,000", "1e3", true/false and text, and anything outside
    [min_value, max_value]."""
    if value in (None, "") and default is not _MISSING:
        return default
    try:
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError
        number = int(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        raise serializers.ValidationError({field: "Enter a whole number (no decimals or commas)."})
    if min_value is not None and number < min_value:
        raise serializers.ValidationError(
            {field: "Can't be negative." if min_value == 0 else f"Must be at least {min_value:,}."})
    if max_value is not None and number > max_value:
        raise serializers.ValidationError({field: f"That's too large (at most {max_value:,})."})
    return number


def lines_of(request, key="lines"):
    """The list of line dicts in a request body; a 400 if it isn't one."""
    lines = request.data.get(key, [])
    if not isinstance(lines, list) or not all(isinstance(l, dict) for l in lines):
        raise serializers.ValidationError({key: "Expected a list of lines."})
    return lines


# DRF's stock wording -> what a cashier understands. Field names are turned into
# labels by the app (frontend/src/lib/api.ts), so these stay short.
_PLAIN = [
    (re.compile(r"^A valid integer is required\.$"), "Enter a whole number (no decimals or commas)."),
    (re.compile(r"^A valid number is required\.$"), "Enter a number."),
    (re.compile(r"^Ensure this value is greater than or equal to 0\.$"), "Can't be negative."),
    (re.compile(r"^Ensure this value is greater than or equal to (\S+)\.$"), "Must be at least {0}."),
    (re.compile(r"^Ensure this value is less than or equal to (\S+)\.$"), "Must be at most {0}."),
    (re.compile(r"^Ensure that there are no more than (\d+) decimal places?\.$"), "Use at most {0} decimal places."),
    (re.compile(r"^Ensure that there are no more than (\d+) digits (in total|before the decimal point)\.$"),
     "That number is too large."),
    (re.compile(r"^String value too large\.$"), "That number is too large."),
    (re.compile(r"^This field (is required|may not be null|may not be blank)\.$"), "Required."),
    (re.compile(r"^Invalid pk .* - object does not exist\.$"),
     "Not found: it may have been deleted, archived, or belong to another branch or business."),
    (re.compile(r"^Incorrect type\. Expected pk value.*$"), "Choose an item from the list."),
    (re.compile(r"^Ensure this field has no more than (\d+) characters\.$"), "Too long: at most {0} characters."),
    (re.compile(r"^Ensure this field has at least (\d+) characters\.$"), "Too short: at least {0} characters."),
]


def _plain_number(raw):
    try:
        n = float(raw)
    except ValueError:
        return raw
    return f"{int(n):,}" if n.is_integer() else raw


def plain_errors(data):
    """Rewrite DRF's validation wording into plain sentences, keeping the shape
    (field keys, nested lines) and the ErrorDetail codes."""
    from rest_framework.exceptions import ErrorDetail

    if isinstance(data, dict):
        return {k: plain_errors(v) for k, v in data.items()}
    if isinstance(data, list):
        return [plain_errors(v) for v in data]
    if isinstance(data, str):
        for pattern, text in _PLAIN:
            if m := pattern.match(str(data)):
                new = text.format(*(_plain_number(g) for g in m.groups()))
                return ErrorDetail(new, code=getattr(data, "code", None)) if isinstance(data, ErrorDetail) else new
    return data


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
        except DatabaseError as e:
            # The public reply stays vague; the reason (wrong password, unreachable
            # host, missing role…) goes to the server log, e.g. Render → Logs.
            log.error("health: database unavailable: %s: %s", type(e).__name__, str(e).strip()[:300])
            from .errors import DB_DOWN

            return Response({"status": "db-unavailable", "detail": DB_DOWN, "code": "db_unavailable"}, status=503)
        return Response({"status": "ok"})
