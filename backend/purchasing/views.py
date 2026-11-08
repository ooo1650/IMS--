from django.db import transaction
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import HasBranchPerm, can
from admin_extras.models import log_action
from ims.api import BranchScopedMixin

from . import services
from .models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine, PurchaseReturn


class POLineSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "product", "sku", "name", "qty_ordered", "qty_received", "unit_cost_paisa"]
        read_only_fields = ["qty_received"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    by_name = serializers.CharField(source="by.username", read_only=True)
    lines = serializers.SerializerMethodField()

    class Meta:
        model = GoodsReceipt
        fields = ["id", "received_at", "received_bs", "by_name", "supplier_invoice_ref", "supplier_invoice_date",
                  "supplier_invoice_bs", "is_import", "taxable_paisa", "exempt_paisa", "vat_paisa",
                  "claimable_vat_paisa", "lines"]

    def get_lines(self, gr):
        return [{"po_line": l.po_line_id, "product_id": l.po_line.product_id, "product": l.po_line.product.name,
                 "qty": l.qty, "unit_cost_paisa": l.unit_cost_paisa, "vat_rate": str(l.vat_rate),
                 "vat_paisa": l.vat_paisa} for l in gr.lines.select_related("po_line__product")]


class POSerializer(serializers.ModelSerializer):
    lines = POLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    receipts = GoodsReceiptSerializer(many=True, read_only=True)
    total_paisa = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ["id", "supplier", "supplier_name", "status", "created_at", "ordered_at", "expected_at",
                  "received_at", "note", "lines", "receipts", "total_paisa"]
        read_only_fields = ["status", "ordered_at", "received_at"]

    def get_total_paisa(self, po):
        return sum(l.qty_ordered * l.unit_cost_paisa for l in po.lines.all())

    def _org_id(self):
        return self.context["request"].branch.org_id

    def validate_supplier(self, supplier):
        if supplier is not None and supplier.org_id != self._org_id():
            raise serializers.ValidationError("Unknown supplier")
        return supplier

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("Add at least one line")
        if any(l["product"].org_id != self._org_id() for l in lines):
            raise serializers.ValidationError("Unknown product")
        if any(l["qty_ordered"] <= 0 or l["unit_cost_paisa"] < 0 for l in lines):
            raise serializers.ValidationError("Quantities must be positive and costs non-negative")
        return lines

    @transaction.atomic
    def create(self, data):
        lines = data.pop("lines")
        po = PurchaseOrder.objects.create(**data)
        PurchaseOrderLine.objects.bulk_create(
            [PurchaseOrderLine(po=po, **{k: v for k, v in l.items() if k != "id"}) for l in lines])
        return po

    @transaction.atomic
    def update(self, po, data):
        if po.status != PurchaseOrder.DRAFT:
            raise serializers.ValidationError({"detail": "Only draft POs can be edited"})
        lines = data.pop("lines", None)
        for k, v in data.items():
            setattr(po, k, v)
        po.save()
        if lines is not None:
            po.lines.all().delete()
            PurchaseOrderLine.objects.bulk_create(
                [PurchaseOrderLine(po=po, **{k: v for k, v in l.items() if k != "id"}) for l in lines])
        return po


class PurchaseOrderViewSet(BranchScopedMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                           mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = POSerializer
    queryset = PurchaseOrder.objects.select_related("supplier").prefetch_related("lines__product", "receipts")
    default_perms = ("purchasing.manage",)
    action_perms = {"list": ("reports.view",), "retrieve": ("reports.view",)}
    http_method_names = ["get", "post", "put", "patch"]

    def get_permissions(self):
        if self.action in ("list", "retrieve") and can(self.request, "purchasing.manage"):
            return [HasBranchPerm("purchasing.manage")()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if st := self.request.query_params.get("status"):
            qs = qs.filter(status__in=st.split(","))
        return qs

    def perform_create(self, serializer):
        po = serializer.save(branch=self.request.branch, created_by=self.request.user)
        log_action(self.request.user, "po.created", po, self.request.branch, self.request)

    def _detail(self, po):
        return Response(POSerializer(self.get_queryset().get(pk=po.pk)).data)

    @action(detail=True, methods=["post"])
    def order(self, request, pk=None):
        po = self.get_object()
        services.mark_ordered(po, request.user, request)
        return self._detail(po)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        po = self.get_object()
        services.cancel_po(po, request.user, request)
        return self._detail(po)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        po = self.get_object()
        lines = [(l["po_line"], int(l.get("qty") or 0), l.get("unit_cost_paisa"))
                 for l in request.data.get("lines", [])]
        services.receive_goods(po, request.user, lines, request.data.get("supplier_invoice_ref", ""),
                               request.data.get("supplier_invoice_date") or None,
                               bool(request.data.get("is_import")), request)
        return self._detail(po)


class PurchaseReturnSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    lines = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseReturn
        fields = ["id", "number", "at", "issued_bs", "supplier", "supplier_name", "goods_receipt", "supplier_note_ref",
                  "reason", "taxable_paisa", "vat_paisa", "claimable_vat_paisa", "lines"]

    def get_lines(self, pr):
        return [{"product": l.product.name, "sku": l.product.sku, "qty": l.qty, "unit_cost_paisa": l.unit_cost_paisa,
                 "vat_paisa": l.vat_paisa} for l in pr.lines.select_related("product")]


class PurchaseReturnViewSet(BranchScopedMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                            mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PurchaseReturnSerializer
    queryset = PurchaseReturn.objects.select_related("supplier").prefetch_related("lines__product")
    default_perms = ("purchasing.manage",)

    def create(self, request):
        from catalog.models import Supplier

        supplier = Supplier.objects.filter(pk=request.data.get("supplier"), org_id=request.branch.org_id).first()
        if supplier is None:
            raise serializers.ValidationError({"supplier": "Unknown supplier"})
        gr = None
        if request.data.get("goods_receipt"):
            gr = GoodsReceipt.objects.get(pk=request.data["goods_receipt"], po__branch=request.branch)
        pr = services.create_purchase_return(
            branch=request.branch, supplier=supplier, by=request.user, goods_receipt=gr,
            lines=[(l["product"], l["qty"], l.get("unit_cost_paisa")) for l in request.data.get("lines", [])],
            reason=request.data.get("reason", ""), supplier_note_ref=request.data.get("supplier_note_ref", ""),
            request=request)
        return Response(PurchaseReturnSerializer(pr).data, status=201)
