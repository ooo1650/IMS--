from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasBranchPerm, HasOrgPerm, can, require_branch_perm
from admin_extras.models import log_action
from ims.api import BranchScopedMixin
from tenancy.models import Branch, Register, rules_for

from compliance import documents
from compliance.models import get_settings

from . import fonepay, services
from .models import (
    CashierShift, CreditNote, Customer, DebitNote, FonepayMerchant, FonepaySettings, HeldCart, Payment, PrintLog, Sale,
    SaleLine,
)
from .numbering import fiscal_year_for, local_today, next_number, receipt_gaps, series_mode
from .serializers import (
    CashCheckoutIn, CreditNoteSerializer, CustomerSerializer, DebitNoteSerializer, FonepayInitiateIn,
    HeldCartSerializer, OfflineSyncIn, SaleListSerializer, SaleSerializer, ShiftSerializer,
)


def get_register(request, register_id):
    try:
        return Register.objects.select_related("branch").get(pk=register_id, branch=request.branch, is_active=True)
    except Register.DoesNotExist:
        raise PermissionDenied("Register does not belong to the active branch")


def sale_detail_qs():
    return Sale.objects.select_related("branch", "register", "cashier", "customer", "receipt").prefetch_related(
        Prefetch("lines", SaleLine.objects.select_related("product").prefetch_related("returns")), "payments",
        "credit_notes", "debit_notes", "cbms_posts",
    )


def sale_response(sale, created=True, **extra):
    data = SaleSerializer(sale_detail_qs().get(pk=sale.pk)).data
    data.update(extra)
    return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PosConfigView(APIView):
    """Everything the register needs to run, incl. its receipt-series position
    (the PWA keeps a local copy for offline numbering, §9.10)."""

    permission_classes = [HasBranchPerm("pos.sell")]

    def get(self, request):
        branch = request.branch
        registers = Register.objects.filter(branch=branch, is_active=True)
        fy = fiscal_year_for(local_today(branch))
        shifts = {s.register_id: s for s in CashierShift.objects.filter(branch=branch, status=CashierShift.OPEN)}
        org = branch.org
        cfg = get_settings(org)
        rules = rules_for(org)
        return Response({
            "branch": {"id": branch.id, "code": branch.code, "name": branch.name, "address": branch.address,
                       "timezone": branch.timezone},
            "seller": {"name": org.name, "name_np": org.name_np, "pan": org.pan},
            "fiscal_year": fy,
            "invoice_series": series_mode(branch),
            "abbreviated_invoice_limit_paisa": cfg.abbreviated_invoice_limit_paisa,
            "delivery_copy_limit_paisa": cfg.delivery_copy_limit_paisa,
            "digital_vat_discount_bp": cfg.digital_vat_discount_bp if cfg.digital_vat_discount_enabled else 0,
            "digital_vat_discount_methods": (cfg.digital_vat_discount_methods or ["fonepay", "card", "wallet", "bank"])
            if cfg.digital_vat_discount_enabled else [],
            "cash_rounding_paisa": rules.cash_rounding_paisa,
            "cashier_max_discount_bp": rules.cashier_max_discount_bp,
            "return_approval_threshold_paisa": rules.return_approval_threshold_paisa,
            "offline_login_max_age_hours": settings.IMS["OFFLINE_LOGIN_MAX_AGE_HOURS"],
            "fonepay_backend": fonepay.branch_backend(branch),
            "registers": [
                {
                    "id": r.id, "code": r.code, "name": r.name, "printer_driver": r.printer_driver,
                    "printer_config": r.printer_config, "next_receipt_number": next_number(branch, r, fy),
                    "open_shift": ShiftSerializer(shifts[r.id]).data if r.id in shifts else None,
                }
                for r in registers
            ],
        })


class ShiftViewSet(BranchScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ShiftSerializer
    queryset = CashierShift.objects.select_related("cashier", "register")
    default_perms = ("shift.manage",)
    action_perms = {"list": ("reports.view",)}

    def get_permissions(self):
        if self.action == "list" and can(self.request, "shift.manage"):
            return [HasBranchPerm("shift.manage")()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.has_branch_perm("reports.view", self.request.branch):
            qs = qs.filter(cashier=self.request.user)
        return qs

    @action(detail=False, methods=["post"])
    def open(self, request):
        register = get_register(request, request.data.get("register_id"))
        shift = services.open_shift(request.branch, register, request.user,
                                    int(request.data.get("opening_cash_paisa", 0)), request)
        return Response(ShiftSerializer(shift).data, status=201)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        shift = self.get_object()
        if shift.cashier_id != request.user.id:
            require_branch_perm(request, "reports.view")  # a manager may close someone else's shift
        shift = services.close_shift(shift, request.user, int(request.data["counted_cash_paisa"]),
                                     offline=bool(request.data.get("offline")), request=request)
        return Response(ShiftSerializer(shift).data)

    @action(detail=True)
    def summary(self, request, pk=None):
        shift = self.get_object()
        return Response({**ShiftSerializer(shift).data, "expected_cash_paisa": services.expected_cash(shift)})


class CheckoutCashView(APIView):
    permission_classes = [HasBranchPerm("pos.sell")]

    def post(self, request):
        s = CashCheckoutIn(data=request.data)
        s.is_valid(raise_exception=True)
        register = get_register(request, s.validated_data["register_id"])
        sale, created = services.checkout_cash(
            branch=request.branch, register=register, cashier=request.user, cart=s.to_cart(),
            tendered_paisa=s.validated_data["tendered_paisa"], idempotency_key=s.validated_data["idempotency_key"],
            method=s.validated_data["method"], payment_ref=s.validated_data["payment_ref"], request=request,
        )
        return sale_response(sale, created)


class FonepayInitiateView(APIView):
    permission_classes = [HasBranchPerm("pos.sell")]

    def post(self, request):
        s = FonepayInitiateIn(data=request.data)
        s.is_valid(raise_exception=True)
        register = get_register(request, s.validated_data["register_id"])
        sale, created = services.initiate_fonepay(
            branch=request.branch, register=register, cashier=request.user, cart=s.to_cart(),
            idempotency_key=s.validated_data["idempotency_key"], request=request,
        )
        payment = sale.payments.get(method=Payment.FONEPAY)
        qr = getattr(sale, "_qr_payload", None)
        return sale_response(sale, created, qr_payload=qr, prn=payment.fonepay_prn)


class PaymentStatusView(APIView):
    """Polled by the SPA every few seconds; each call also polls FonePay by PRN."""

    permission_classes = [HasBranchPerm("pos.sell")]

    def get(self, request, pk):
        sale = Sale.objects.for_branch(request.branch).filter(pk=pk).first()
        if sale is None:
            raise Http404
        sale = services.refresh_fonepay_status(sale, request.user)
        payment = sale.payments.filter(method=Payment.FONEPAY).first()
        return Response({
            "sale_id": sale.id, "status": sale.status, "payment_status": payment.status if payment else None,
            "receipt_number": sale.receipt_number, "stock_conflict": sale.stock_conflict,
            "sale": SaleSerializer(sale_detail_qs().get(pk=sale.pk)).data if sale.status != Sale.OPEN else None,
        })


class FonepayCallbackView(APIView):
    """Callback path, if FonePay supports one. We verify the signature, then
    re-check the status with FonePay rather than trusting the payload."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        prn = str(request.data.get("prn", ""))
        message = ",".join(str(request.data.get(k, "")) for k in ("prn", "status", "amount", "trx_id"))
        # Each branch has its own merchant, so the signing key depends on whose payment this is.
        payment = Payment.objects.filter(fonepay_prn=prn).select_related("sale__branch").first() if prn else None
        secret = fonepay.callback_secret(payment.sale.branch if payment else None)
        if not fonepay.verify_signature(message, request.data.get("signature", ""), secret):
            log_action(None, "fonepay.callback_bad_signature", request=request, prn=prn)
            return Response({"detail": "bad signature"}, status=400)
        if payment is None:
            return Response({"detail": "unknown prn"}, status=404)
        sale = services.refresh_fonepay_status(payment.sale)
        return Response({"ok": True, "status": sale.status})


class FonepayMockPayView(APIView):
    """DEV ONLY (mock backend): simulate the customer paying the QR."""

    permission_classes = [HasBranchPerm("pos.sell")]

    def post(self, request, prn):
        if request.branch is None or fonepay.server_mode(request.branch.org) != "mock":
            raise Http404
        payment = Payment.objects.filter(fonepay_prn=prn, sale__branch=request.branch).first()
        if payment is None:
            raise Http404
        paid = request.data.get("paid_paisa")
        fonepay.MockFonepayGateway().simulate_payment(prn, int(paid) if paid else None,
                                                      fail=bool(request.data.get("fail")))
        return Response({"ok": True})


class OfflineSyncView(APIView):
    """Queued offline cash sales, one per request, each with its idempotency key."""

    permission_classes = [HasBranchPerm("pos.sell")]

    def post(self, request):
        s = OfflineSyncIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        register = get_register(request, d["register_id"])
        sale, created = services.sync_offline_sale(
            branch=request.branch, register=register, cashier=request.user, cart=s.to_cart(),
            tendered_paisa=d["tendered_paisa"], idempotency_key=d["idempotency_key"],
            receipt_seq=d.get("receipt_seq"), fiscal_year=d["fiscal_year"], created_at=d["created_at"],
            client_total_paisa=d.get("client_total_paisa"), offline_discount_approved=d["offline_discount_approved"],
            provisional_ref=d["provisional_ref"], request=request,
        )
        gaps = receipt_gaps(request.branch, register, d["fiscal_year"]) if series_mode(request.branch) == "register" else []
        return sale_response(sale, created, receipt_gaps=gaps)


class SaleViewSet(BranchScopedMixin, viewsets.ReadOnlyModelViewSet):
    default_perms = ("reports.view",)
    action_perms = {"retrieve": ("pos.sell",), "receipt": ("pos.sell",), "lookup": ("pos.sell",),
                    "print_invoice": ("pos.sell",), "void": ("pos.void",), "resolve_conflict": ("conflicts.resolve",),
                    "returns": ("returns.create",), "debit_note": ("pos.void",)}

    def get_permissions(self):
        # Cashiers can view their own sales list (for reprints/returns).
        if self.action == "list" and can(self.request, "pos.sell"):
            return [HasBranchPerm("pos.sell")()]
        return super().get_permissions()

    def get_serializer_class(self):
        return SaleListSerializer if self.action == "list" else SaleSerializer

    def get_queryset(self):
        base = Sale.objects.for_branch(self.request.branch)
        if self.action != "list":
            return sale_detail_qs().filter(pk__in=base.values("pk"))
        qs = base.select_related("cashier", "register")
        p = self.request.query_params
        if st := p.get("status"):
            qs = qs.filter(status=st)
        if p.get("conflicts") == "1":
            qs = qs.filter(stock_conflict=True, conflict_resolution="")
        if p.get("review") == "1":
            qs = qs.filter(Q(needs_review=True) | Q(status=Sale.PAYMENT_ORPHAN))
        if p.get("offline") == "1":
            qs = qs.filter(offline=True)
        if q := p.get("q"):
            qs = qs.filter(Q(receipt_number__icontains=q) | Q(buyer_name__icontains=q) | Q(buyer_pan=q))
        if p.get("unprinted") == "1":
            qs = qs.exclude(receipt_number="").filter(Q(receipt__isnull=True) | Q(receipt__print_count=0))
        if p.get("cancelled") == "1":
            qs = qs.filter(status=Sale.VOIDED).exclude(receipt_number="")
        qs = qs.select_related("receipt")
        if not self.request.user.has_branch_perm("reports.view", self.request.branch):
            qs = qs.filter(cashier=self.request.user)
        return qs

    @action(detail=False)
    def lookup(self, request):
        """Find a sale by receipt number (for returns)."""
        number = request.query_params.get("receipt", "").strip()
        sale = sale_detail_qs().filter(branch=request.branch, receipt_number__iexact=number).first()
        if sale is None:
            raise Http404
        return Response(SaleSerializer(sale).data)

    @action(detail=True)
    def receipt(self, request, pk=None):
        """Document data for display only (no print is recorded)."""
        sale = self.get_object()
        if not sale.receipt_number:
            raise serializers.ValidationError({"detail": "Sale has no invoice yet"})
        return Response(documents.invoice_context(sale, None))

    @action(detail=True, methods=["post"], url_path="print")
    def print_invoice(self, request, pk=None):
        """Print once: the first print is the original; later prints are stamped
        'Copy of Original' with their copy number. kind=delivery prints a
        delivery copy (goods over the limit must travel with the invoice)."""
        sale = self.get_object()
        if not sale.receipt_number:
            raise serializers.ValidationError({"detail": "Sale has no invoice yet"})
        log = services.record_print("invoice", sale, request.user, request.data.get("kind", PrintLog.ORIGINAL))
        ctx = documents.invoice_context(sale, log)
        return Response({"kind": log.kind, "copy_no": log.copy_no, "document": ctx,
                         "html": documents.render_html(ctx)})

    @action(detail=True, methods=["post"], url_path="debit-note")
    def debit_note(self, request, pk=None):
        dn = services.create_debit_note(
            sale=self.get_object(), by=request.user, taxable_paisa=int(request.data.get("taxable_paisa") or 0),
            reason=request.data.get("reason", ""), tax_rate=request.data.get("tax_rate"), request=request)
        return Response(DebitNoteSerializer(dn).data, status=201)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        sale = services.void_sale(self.get_object(), request.user, request.data.get("reason", ""), request)
        return sale_response(sale, created=False)

    @action(detail=True, methods=["post"], url_path="resolve-conflict")
    def resolve_conflict(self, request, pk=None):
        sale = services.resolve_conflict(self.get_object(), request.user, request.data.get("resolution"),
                                         request.data.get("reason", ""), request)
        return sale_response(sale, created=False)

    @action(detail=True, methods=["post"])
    def returns(self, request, pk=None):
        sale = self.get_object()
        register = get_register(request, request.data.get("register_id"))
        items = [(l["sale_line_id"], int(l["qty"])) for l in request.data.get("lines", []) if int(l.get("qty", 0))]
        if not items:
            raise serializers.ValidationError({"detail": "Choose at least one item to return"})
        cn = services.create_return(
            sale=sale, register=register, cashier=request.user, items=items, reason=request.data.get("reason", ""),
            refund_method=request.data.get("refund_method", "cash"),
            approver_username=request.data.get("approver_username", ""),
            approver_password=request.data.get("approver_password", ""), request=request,
        )
        return Response(CreditNoteSerializer(cn).data, status=201)


class CreditNoteViewSet(BranchScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = CreditNoteSerializer
    queryset = CreditNote.objects.select_related("original_sale", "approved_by").prefetch_related(
        "lines__original_sale_line")
    default_perms = ("returns.create",)

    @action(detail=True, methods=["post"], url_path="print")
    def print_note(self, request, pk=None):
        cn = self.get_object()
        log = services.record_print("credit_note", cn, request.user)
        ctx = documents.credit_note_context(cn, log)
        return Response({"kind": log.kind, "copy_no": log.copy_no, "document": ctx,
                         "html": documents.render_html(ctx)})


class DebitNoteViewSet(BranchScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DebitNoteSerializer
    queryset = DebitNote.objects.select_related("original_sale")
    default_perms = ("reports.view",)

    @action(detail=True, methods=["post"], url_path="print")
    def print_note(self, request, pk=None):
        dn = self.get_object()
        log = services.record_print("debit_note", dn, request.user)
        ctx = documents.debit_note_context(dn, log)
        return Response({"kind": log.kind, "copy_no": log.copy_no, "document": ctx,
                         "html": documents.render_html(ctx)})


class CustomerViewSet(BranchScopedMixin, viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    default_perms = ("pos.sell",)
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        if self.request.branch is None:
            return Customer.objects.none()
        # This branch's customers and the business-wide ones; never another business's.
        qs = Customer.objects.filter(org_id=self.request.branch.org_id).filter(
            Q(branch=self.request.branch) | Q(branch__isnull=True))
        if q := self.request.query_params.get("q"):
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
        return qs

    def perform_create(self, serializer):
        serializer.save(org=self.request.branch.org,
                        branch=self.request.branch if not self.request.data.get("org_wide") else None)


class HeldCartViewSet(BranchScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    serializer_class = HeldCartSerializer
    queryset = HeldCart.objects.all()
    default_perms = ("pos.sell",)
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        if reg := self.request.query_params.get("register"):
            qs = qs.filter(register_id=reg)
        return qs

    def perform_create(self, serializer):
        get_register(self.request, serializer.validated_data["register"].id)
        serializer.save(branch=self.request.branch, cashier=self.request.user)


class ReceiptGapsView(APIView):
    permission_classes = [HasBranchPerm("reports.view")]

    def get(self, request, register_id):
        register = get_register(request, register_id)
        fy = request.query_params.get("fiscal_year") or fiscal_year_for(local_today(request.branch))
        return Response({"register": register.code, "fiscal_year": fy,
                         "gaps": receipt_gaps(request.branch, register, fy)})


class FonepayMerchantSerializer(serializers.ModelSerializer):
    """Secrets are write-only: blank keeps the stored value, ``clear_*`` removes it."""

    branch_code = serializers.CharField(source="branch.code", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=256,
                                     trim_whitespace=False)
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=512,
                                   trim_whitespace=False)
    clear_password = serializers.BooleanField(write_only=True, required=False, default=False)
    clear_secret = serializers.BooleanField(write_only=True, required=False, default=False)
    password_set = serializers.SerializerMethodField()
    secret_set = serializers.SerializerMethodField()
    missing = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True, default=None)

    class Meta:
        model = FonepayMerchant
        fields = ["branch", "branch_code", "branch_name", "enabled", "merchant_code", "username", "password",
                  "secret", "clear_password", "clear_secret", "password_set", "secret_set", "missing", "status",
                  "updated_at", "updated_by_name"]
        read_only_fields = ["branch", "updated_at"]

    def get_password_set(self, obj):
        return bool(obj.password_enc)

    def get_secret_set(self, obj):
        return bool(obj.secret_enc)

    def get_missing(self, obj):
        return obj.missing()

    def get_status(self, obj):
        """What the till sees at this branch: disabled / mock / http."""
        return fonepay.branch_backend(obj.branch)

    def validate_merchant_code(self, v):
        v = v.strip()
        if v and not v.replace("-", "").replace("_", "").isalnum():
            raise serializers.ValidationError("Use letters, digits, - and _ only.")
        return v

    def validate_username(self, v):
        return v.strip()

    def validate(self, data):
        m = self.instance
        merged = FonepayMerchant(
            merchant_code=data.get("merchant_code", m.merchant_code if m else ""),
            username=data.get("username", m.username if m else ""),
            password_enc="" if data.get("clear_password") else ("x" if data.get("password") or (m and m.password_enc) else ""),
            secret_enc="" if data.get("clear_secret") else ("x" if data.get("secret") or (m and m.secret_enc) else ""),
        )
        enabled = data.get("enabled", m.enabled if m else False)
        org = self.context.get("org")
        if enabled and fonepay.server_mode(org) == "http" and merged.missing():
            raise serializers.ValidationError(
                {"enabled": f"Fill in {', '.join(merged.missing())} before switching FonePay on."})
        return data

    def save_to(self, merchant, user):
        d = dict(self.validated_data)
        password, secret = d.pop("password", ""), d.pop("secret", "")
        clear_password, clear_secret = d.pop("clear_password", False), d.pop("clear_secret", False)
        for k, v in d.items():
            setattr(merchant, k, v)
        changed = []
        if clear_password:
            merchant.password_enc, changed = "", changed + ["password cleared"]
        elif password:
            merchant.password, changed = password, changed + ["password"]
        if clear_secret:
            merchant.secret_enc, changed = "", changed + ["secret cleared"]
        elif secret:
            merchant.secret, changed = secret, changed + ["secret"]
        merchant.updated_by = user
        merchant.save()
        return merchant, changed


# --- FonePay per business. The helpers take the business explicitly so the IMS
# admin screen (request.org) and the operator console (any business) share them.

def merchant_rows(org):
    branches = Branch.objects.filter(org=org).order_by("name")
    saved = {m.branch_id: m for m in FonepayMerchant.objects.filter(branch__org=org).select_related("branch", "updated_by")}
    return [saved.get(b.id) or FonepayMerchant(branch=b) for b in branches]


def merchant_for_branch(org, branch_pk):
    branch = Branch.objects.filter(org=org, pk=branch_pk).first()
    if branch is None:
        raise Http404
    return FonepayMerchant.objects.select_related("branch", "updated_by").filter(branch=branch).first() \
        or FonepayMerchant(branch=branch)


def save_merchant(request, org, branch_pk, partial):
    with transaction.atomic():
        merchant = merchant_for_branch(org, branch_pk)
        s = FonepayMerchantSerializer(merchant if merchant.pk and not merchant._state.adding else None,
                                      data=request.data, partial=partial, context={"org": org})
        s.is_valid(raise_exception=True)
        merchant, changed = s.save_to(merchant, request.user)
        if changed:  # the audit trail masks secrets, so record that they changed
            log_action(request.user, "fonepay.credentials_changed", merchant, merchant.branch, request, fields=changed)
    return merchant


def fonepay_settings_payload(org):
    mode, base_url = fonepay.server_settings(org)
    row = FonepaySettings.objects.select_related("updated_by").filter(org=org).first()
    return {
        "backend": mode, "base_url": base_url, "base_url_set": bool(base_url),
        "mock_allowed": settings.FONEPAY_MOCK_ALLOWED, "allowed_hosts": settings.FONEPAY_ALLOWED_HOSTS,
        "updated_at": row.updated_at if row else None,
        "updated_by_name": row.updated_by.username if row and row.updated_by else None,
    }


class FonepaySettingsIn(serializers.Serializer):
    mode = serializers.ChoiceField([c for c, _ in FonepaySettings.MODES], required=False)
    base_url = serializers.CharField(required=False, allow_blank=True, max_length=200, trim_whitespace=True)

    def validate_base_url(self, v):
        if v and not fonepay.allowed_api_url(v):
            raise serializers.ValidationError(
                f"Use an https address on {', '.join(settings.FONEPAY_ALLOWED_HOSTS)} "
                "(the server's IMS_FONEPAY_HOSTS setting).")
        return v.rstrip("/")

    def validate(self, data):
        current = FonepaySettings.objects.filter(org=self.context["org"]).first()
        mode = data.get("mode", current.mode if current else FonepaySettings.DISABLED)
        base_url = data.get("base_url", current.base_url if current else "")
        if mode == FonepaySettings.MOCK and not settings.FONEPAY_MOCK_ALLOWED:
            raise serializers.ValidationError(
                {"mode": "Demo mode lets anyone at the till mark a QR as paid, so this server doesn't allow it "
                         "(IMS_ALLOW_FONEPAY_MOCK)."})
        if mode == FonepaySettings.LIVE and not base_url:
            raise serializers.ValidationError({"base_url": "Enter the FonePay API address before going live."})
        return data


def save_fonepay_settings(request, org):
    s = FonepaySettingsIn(data=request.data, context={"org": org})
    s.is_valid(raise_exception=True)
    with transaction.atomic():
        row, _ = FonepaySettings.objects.select_for_update().get_or_create(org=org)
        for k, v in s.validated_data.items():
            setattr(row, k, v)
        row.updated_by = request.user
        row.save()
    return fonepay_settings_payload(org)


class FonepayMerchantViewSet(viewsets.ViewSet):
    """Business admins: one FonePay merchant account per branch of the active business.

    GET  fonepay-merchants/        every branch, with its merchant (or blanks)
    GET  fonepay-merchants/<branch>/
    PUT/PATCH fonepay-merchants/<branch>/   create or update (partial)
    """

    def get_permissions(self):
        return [HasOrgPerm("branches.manage")()]

    def list(self, request):
        return Response({**fonepay_settings_payload(request.org),
                         "results": FonepayMerchantSerializer(merchant_rows(request.org), many=True).data})

    def retrieve(self, request, pk=None):
        return Response(FonepayMerchantSerializer(merchant_for_branch(request.org, pk)).data)

    def update(self, request, pk=None, partial=False):
        return Response(FonepayMerchantSerializer(save_merchant(request, request.org, pk, partial)).data)

    def partial_update(self, request, pk=None):
        return self.update(request, pk, partial=True)


class FonepaySettingsView(APIView):
    """Business admins: FonePay on/off/demo and its API address for the active business.

    GET fonepay-settings · PATCH fonepay-settings {mode?, base_url?}
    """

    permission_classes = [HasOrgPerm("branches.manage")]

    def get(self, request):
        return Response(fonepay_settings_payload(request.org))

    def patch(self, request):
        return Response(save_fonepay_settings(request, request.org))
