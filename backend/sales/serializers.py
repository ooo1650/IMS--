import re

from rest_framework import serializers

from ims.api import MAX_INT, MAX_PAISA, MAX_QTY

from .models import CashierShift, CreditNote, CreditNoteLine, Customer, DebitNote, HeldCart, Payment, Sale, SaleLine
from .pricing import AMOUNT, NONE, PERCENT
from .services import Cart, CartLine

DISCOUNT_CHOICES = [NONE, AMOUNT, PERCENT]


class CartLineIn(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1, max_value=MAX_INT)
    qty = serializers.IntegerField(min_value=1, max_value=MAX_QTY)
    discount_type = serializers.ChoiceField(DISCOUNT_CHOICES, default=NONE)
    discount_value = serializers.IntegerField(min_value=0, max_value=MAX_PAISA, default=0)
    discount_reason = serializers.CharField(required=False, allow_blank=True, default="")
    # Offline sync only.
    unit_price_paisa = serializers.IntegerField(min_value=0, max_value=MAX_PAISA, required=False)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False)
    tax_inclusive = serializers.BooleanField(required=False)


class CartIn(serializers.Serializer):
    lines = CartLineIn(many=True)
    sale_discount_type = serializers.ChoiceField(DISCOUNT_CHOICES, default=NONE)
    sale_discount_value = serializers.IntegerField(min_value=0, max_value=MAX_PAISA, default=0)
    sale_discount_reason = serializers.CharField(required=False, allow_blank=True, default="")
    customer_id = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=MAX_INT)
    buyer_name = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    buyer_address = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
    buyer_pan = serializers.CharField(required=False, allow_blank=True, default="", max_length=9)
    approver_username = serializers.CharField(required=False, allow_blank=True, default="")
    approver_password = serializers.CharField(required=False, allow_blank=True, default="", write_only=True)

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("Cart is empty")
        return lines

    def validate(self, data):
        if data["sale_discount_type"] != NONE and data["sale_discount_value"] and not data["sale_discount_reason"]:
            raise serializers.ValidationError({"sale_discount_reason": "A discount needs a reason"})
        for l in data["lines"]:
            if l["discount_type"] != NONE and l["discount_value"] and not l.get("discount_reason"):
                raise serializers.ValidationError({"lines": "Each line discount needs a reason"})
        return data

    def to_cart(self):
        d = self.validated_data
        return Cart(
            lines=[CartLine(**l) for l in d["lines"]],
            sale_discount_type=d["sale_discount_type"], sale_discount_value=d["sale_discount_value"],
            sale_discount_reason=d["sale_discount_reason"], customer_id=d.get("customer_id"),
            buyer_name=d["buyer_name"], buyer_address=d["buyer_address"], buyer_pan=d["buyer_pan"],
            approver_username=d["approver_username"], approver_password=d["approver_password"],
        )


class CashCheckoutIn(CartIn):
    register_id = serializers.IntegerField(min_value=1, max_value=MAX_INT)
    idempotency_key = serializers.CharField(max_length=64)
    tendered_paisa = serializers.IntegerField(min_value=0, max_value=MAX_PAISA, default=0)
    method = serializers.ChoiceField(["cash", "card", "wallet", "bank"], default="cash")
    payment_ref = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)


class FonepayInitiateIn(CartIn):
    register_id = serializers.IntegerField(min_value=1, max_value=MAX_INT)
    idempotency_key = serializers.CharField(max_length=64)


class OfflineSyncIn(CashCheckoutIn):
    receipt_seq = serializers.IntegerField(min_value=1, max_value=MAX_INT, required=False, allow_null=True)
    provisional_ref = serializers.CharField(required=False, allow_blank=True, default="", max_length=40)
    fiscal_year = serializers.CharField(max_length=9)
    created_at = serializers.DateTimeField()
    client_total_paisa = serializers.IntegerField(required=False, min_value=0, max_value=MAX_PAISA)
    offline_discount_approved = serializers.BooleanField(default=False)


class SaleLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    returned_qty = serializers.SerializerMethodField()

    class Meta:
        model = SaleLine
        fields = ["id", "product", "sku", "product_name", "qty", "unit_price_paisa", "discount_type",
                  "discount_value", "discount_reason", "line_discount_paisa", "sale_discount_share_paisa",
                  "line_total_paisa", "tax_rate", "tax_inclusive", "tax_amount_paisa", "tax_kind", "hs_code",
                  "excise_paisa", "levy_type", "levy_paisa", "returned_qty"]

    def get_returned_qty(self, line):
        return sum(r.qty for r in line.returns.all())


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "method", "amount_paisa", "tendered_paisa", "change_paisa", "ref", "fonepay_prn",
                  "fonepay_trx_id", "fonepay_paid_paisa", "status", "created_at", "expires_at"]


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    register_code = serializers.CharField(source="register.code", read_only=True, default=None)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default=None)
    print_count = serializers.IntegerField(source="receipt.print_count", read_only=True, default=0)
    credit_notes = serializers.SerializerMethodField()
    debit_notes = serializers.SerializerMethodField()
    cbms_status = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ["id", "branch", "branch_code", "register", "register_code", "cashier_name", "customer",
                  "customer_name", "status", "invoice_type", "buyer_name", "buyer_address", "buyer_pan",
                  "payment_method", "subtotal_paisa", "discount_total_paisa", "tax_total_paisa",
                  "excise_total_paisa", "hst_total_paisa", "esf_total_paisa", "digital_vat_discount_paisa",
                  "round_adjust_paisa", "total_paisa", "sale_discount_type", "sale_discount_value",
                  "sale_discount_reason", "opened_at", "paid_at", "transaction_at", "transaction_bs", "issued_at",
                  "issued_bs", "voided_at", "void_reason", "offline", "offline_created_at", "provisional_ref",
                  "stock_conflict", "conflict_resolution", "needs_review", "receipt_number", "receipt_seq",
                  "fiscal_year", "payment_ref", "expires_at", "print_count", "lines", "payments", "credit_notes",
                  "debit_notes", "cbms_status"]

    def get_credit_notes(self, sale):
        return [{"id": c.id, "number": c.number, "total_paisa": c.total_paisa, "issued_bs": c.issued_bs}
                for c in sale.credit_notes.all()]

    def get_debit_notes(self, sale):
        return [{"id": d.id, "number": d.number, "total_paisa": d.total_paisa, "issued_bs": d.issued_bs}
                for d in sale.debit_notes.all()]

    def get_cbms_status(self, sale):
        post = next(iter(sale.cbms_posts.filter(doc_type="bill")), None)
        return {"status": post.status, "code": post.last_code} if post else None


class SaleListSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    register_code = serializers.CharField(source="register.code", read_only=True, default=None)

    class Meta:
        model = Sale
        fields = ["id", "receipt_number", "status", "invoice_type", "buyer_name", "payment_method", "total_paisa",
                  "opened_at", "paid_at", "issued_bs", "cashier_name", "register_code", "offline", "provisional_ref",
                  "stock_conflict", "conflict_resolution", "needs_review", "print_count"]

    print_count = serializers.IntegerField(source="receipt.print_count", read_only=True, default=0)


class ShiftSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    register_code = serializers.CharField(source="register.code", read_only=True)

    class Meta:
        model = CashierShift
        fields = ["id", "register", "register_code", "cashier_name", "status", "opened_at", "opening_cash_paisa",
                  "closed_at", "counted_cash_paisa", "expected_cash_paisa", "variance_paisa", "closed_offline"]


class CreditNoteLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="original_sale_line.product_name", read_only=True)

    class Meta:
        model = CreditNoteLine
        fields = ["id", "original_sale_line", "product_name", "qty", "unit_price_paisa", "line_total_paisa",
                  "tax_paisa"]


class CreditNoteSerializer(serializers.ModelSerializer):
    lines = CreditNoteLineSerializer(many=True, read_only=True)
    original_receipt = serializers.CharField(source="original_sale.receipt_number", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True, default=None)

    class Meta:
        model = CreditNote
        fields = ["id", "number", "at", "issued_bs", "original_sale", "original_receipt", "total_paisa",
                  "taxable_paisa", "tax_paisa", "refund_method", "reason", "approved_by_name", "lines"]


class DebitNoteSerializer(serializers.ModelSerializer):
    original_receipt = serializers.CharField(source="original_sale.receipt_number", read_only=True)

    class Meta:
        model = DebitNote
        fields = ["id", "number", "at", "issued_bs", "original_sale", "original_receipt", "taxable_paisa", "tax_rate",
                  "tax_paisa", "total_paisa", "reason"]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "branch", "name", "phone", "email", "address", "tax_id", "created_at"]
        read_only_fields = ["created_at"]

    def validate_phone(self, value):
        """Digits with the usual separators: "98-4123 4567", "+977 1 4412345"."""
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if value and (re.search(r"[^\d\s+()\-]", value) or not 7 <= len(digits) <= 15):
            raise serializers.ValidationError("Enter a phone number: 7 to 15 digits (spaces, + and - are fine).")
        return value

    def validate_tax_id(self, value):
        value = (value or "").strip()
        if value and not (value.isdigit() and len(value) == 9):
            raise serializers.ValidationError("A PAN is 9 digits.")
        return value


class HeldCartSerializer(serializers.ModelSerializer):
    class Meta:
        model = HeldCart
        fields = ["id", "register", "label", "cart", "created_at"]
        read_only_fields = ["created_at"]
