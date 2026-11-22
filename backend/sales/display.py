"""A tablet as a till's customer display.

BroadcastChannel can't leave the till's browser, so a separate device gets the
screen through the server. The tablet has no login and no session: a one-time
code from POS → Devices gives it a token that can read that one till's customer
screen and nothing else. A new pairing replaces it; Disconnect revokes it.
"""
import hashlib
import secrets
from datetime import timedelta

from django.db.models import F
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasBranchPerm
from admin_extras.models import log_action
from ims.api import MAX_QTY, to_int

from .models import RegisterDisplay
from .views import get_register

# No 0/O or 1/I/L: the code may be copied by hand. 31^8 is about 8.5e11.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_TTL = timedelta(minutes=10)
# The till repeats a bill every 20 s (browsers slow a hidden tab to about a
# minute). Older than this, the till is closed or cut off: show the welcome
# screen rather than the last customer's bill or a total that may have changed.
STALE = timedelta(seconds=90)
SEEN_EVERY = timedelta(seconds=15)
ONLINE = timedelta(seconds=40)
MAX_LINES = 100  # the till sends only the newest


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _normalise(code):
    return "".join(c for c in str(code or "")[:40].upper() if c.isalnum())


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def _register(request, raw):
    return get_register(request, to_int(raw, "register_id", min_value=1))


class LineIn(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    qty = serializers.IntegerField(min_value=1, max_value=MAX_QTY)
    amount = serializers.CharField(max_length=32)


class ScreenIn(serializers.Serializer):
    """What the customer sees. Amounts are already formatted; nothing is calculated from them."""

    mode = serializers.ChoiceField(["idle", "cart", "pay", "done"])
    store = serializers.CharField(max_length=255, allow_blank=True)
    lines = LineIn(many=True)
    subtotal = serializers.CharField(max_length=32, required=False)
    discount = serializers.CharField(max_length=32, required=False)
    tax = serializers.CharField(max_length=32, required=False)
    total = serializers.CharField(max_length=32, allow_blank=True)
    change = serializers.CharField(max_length=32, required=False)
    # Only an inline PNG: never an address the tablet would go and fetch.
    qr = serializers.RegexField(r"^data:image/png;base64,[A-Za-z0-9+/=]+\Z", max_length=30_000, required=False)

    def validate_lines(self, lines):
        if len(lines) > MAX_LINES:
            raise serializers.ValidationError(f"At most {MAX_LINES} lines.")
        return lines


class _TillView(APIView):
    permission_classes = [HasBranchPerm("pos.sell")]


class TillDisplayView(_TillView):
    def get(self, request):
        register = _register(request, request.query_params.get("register_id"))
        row = RegisterDisplay.objects.filter(register=register).defer("state").first()
        now = timezone.now()
        return _no_store(Response({
            "paired": bool(row and row.token_hash),
            "online": bool(row and row.token_hash and row.last_seen_at and now - row.last_seen_at < ONLINE),
            "code_pending": bool(row and row.code_hash and row.code_expires_at > now),
        }))


class TillDisplayPairView(_TillView):
    def post(self, request):
        register = _register(request, request.data.get("register_id"))
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        row, _ = RegisterDisplay.objects.get_or_create(register=register)
        # A tablet already paired keeps working until the new code is used.
        RegisterDisplay.objects.filter(pk=row.pk).update(
            code_hash=_hash(code), code_expires_at=timezone.now() + CODE_TTL)
        log_action(request.user, "display.code_created", register, request.branch, request)
        return _no_store(Response({"code": f"{code[:4]}-{code[4:]}", "expires_in": int(CODE_TTL.total_seconds())},
                                  status=status.HTTP_201_CREATED))


class TillDisplayStateView(_TillView):
    def post(self, request):
        register = _register(request, request.data.get("register_id"))
        s = ScreenIn(data=request.data.get("state"))
        s.is_valid(raise_exception=True)
        state = {**s.validated_data, "lines": [dict(line) for line in s.validated_data["lines"]]}
        row = RegisterDisplay.objects.filter(register=register, token_hash__isnull=False).only("state").first()
        if row is None:
            return Response({"paired": False})
        now = timezone.now()
        fields = {"sent_at": now}
        if row.state != state:
            fields.update(state=state, seq=F("seq") + 1, changed_at=now)
        # update(), never save(): a save could bring back a token revoked meanwhile.
        RegisterDisplay.objects.filter(pk=row.pk).update(**fields)
        return Response({"paired": True})


class TillDisplayDisconnectView(_TillView):
    def post(self, request):
        register = _register(request, request.data.get("register_id"))
        changed = RegisterDisplay.objects.filter(register=register).update(
            token_hash=None, code_hash=None, code_expires_at=None, paired_at=None, last_seen_at=None, state={})
        if changed:
            log_action(request.user, "display.disconnected", register, request.branch, request)
        return Response({"ok": True})


class _TabletView(APIView):
    # No session, so no CSRF to check: the code or token in the request is the only credential.
    authentication_classes = []
    permission_classes = [AllowAny]

    @staticmethod
    def displays():
        return RegisterDisplay.objects.select_related("register__branch__org").filter(
            register__is_active=True, register__branch__is_active=True, register__branch__org__is_active=True)


class TabletClaimView(_TabletView):
    def post(self, request):
        code = _normalise(request.data.get("code") if isinstance(request.data, dict) else "")
        now = timezone.now()
        row = self.displays().filter(code_hash=_hash(code), code_expires_at__gt=now).first() if len(code) == 8 else None
        token = secrets.token_urlsafe(32)
        # Conditional update: of two tablets using one code, only the first gets the till.
        if row is None or not RegisterDisplay.objects.filter(pk=row.pk, code_hash=row.code_hash).update(
            code_hash=None, code_expires_at=None, token_hash=_hash(token), paired_at=now, last_seen_at=now,
            state={}, seq=F("seq") + 1, changed_at=now, sent_at=None,
        ):
            return _no_store(Response(
                {"detail": "That code is wrong, used or expired. On the till press POS → Devices → Connect a tablet "
                           "for a new one.", "code": "display_code_invalid"}, status=status.HTTP_400_BAD_REQUEST))
        register = row.register
        log_action(None, "display.paired", register, register.branch, request)
        return _no_store(Response({"token": token, "store": register.branch.org.name, "till": register.name}))


class TabletStateView(_TabletView):
    def get(self, request):
        token = request.headers.get("X-Display-Token", "")
        row = self.displays().filter(token_hash=_hash(token)).first() if token else None
        if row is None:
            return _no_store(Response(
                {"detail": "This tablet isn't connected to a till any more. Connect it again from POS → Devices.",
                 "code": "display_unpaired"}, status=status.HTTP_401_UNAUTHORIZED))
        now = timezone.now()
        if not row.last_seen_at or now - row.last_seen_at >= SEEN_EVERY:
            RegisterDisplay.objects.filter(pk=row.pk).update(last_seen_at=now)
        state = row.state or {"mode": "idle"}
        live = state["mode"] == "idle" or bool(row.sent_at and now - row.sent_at < STALE)
        version = f"{row.seq}" if live else f"{row.seq}-stale"
        body = {"version": version, "quiet_seconds": int((now - row.changed_at).total_seconds()) if row.changed_at else 0}
        if request.query_params.get("version") != version:  # unchanged = a tiny reply
            register = row.register
            if not live or state["mode"] == "idle":
                state = {"mode": "idle", "store": register.branch.org.name, "lines": [], "total": ""}
            body.update(state=state, till=register.name)
        return _no_store(Response(body))
