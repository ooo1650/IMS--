from django.db import transaction
from django.db.models import F
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.permissions import PerActionPermissionMixin
from admin_extras.models import log_action
from catalog.models import Category
from reporting.queries import stock_summary
from ims.api import BranchScopedMixin

from . import services
from .models import InventoryItem, StockMovement, StockTransfer, StockTransferLine, Stocktake, StocktakeLine


class InventoryItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)
    unit = serializers.CharField(source="product.unit", read_only=True)
    category = serializers.CharField(source="product.category.name", read_only=True, default=None)
    value_paisa = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = ["id", "product", "sku", "name", "unit", "category", "quantity_on_hand", "reorder_point",
                  "reorder_qty", "weighted_avg_cost_paisa", "allow_negative", "value_paisa"]
        read_only_fields = ["product", "quantity_on_hand", "weighted_avg_cost_paisa"]

    def get_value_paisa(self, item):
        return max(item.quantity_on_hand, 0) * item.weighted_avg_cost_paisa


class MovementSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)
    by_name = serializers.CharField(source="by.username", read_only=True, default=None)

    class Meta:
        model = StockMovement
        fields = ["id", "product", "sku", "name", "type", "qty", "unit_cost_paisa", "ref", "ref_type", "at",
                  "by_name", "reason"]


class InventoryItemViewSet(BranchScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = InventoryItemSerializer
    queryset = InventoryItem.objects.select_related("product", "product__category")
    action_perms = {"list": ("inventory.view",), "retrieve": ("inventory.view",), "summary": ("inventory.view",)}
    default_perms = ("inventory.adjust",)
    http_method_names = ["get", "patch", "post"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if q := p.get("q"):
            qs = qs.filter(product__name__icontains=q) | qs.filter(product__sku__icontains=q)
        if p.get("low") == "1":
            qs = qs.filter(quantity_on_hand__lte=F("reorder_point"))
        if p.get("negative") == "1":
            qs = qs.filter(quantity_on_hand__lt=0)
        if p.get("out") == "1":
            qs = qs.filter(quantity_on_hand__lte=0)
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        items = InventoryItem.objects.filter(branch=request.branch)
        return Response({"items": items.count(), **stock_summary(items)})

    def perform_update(self, serializer):
        item = serializer.save()
        log_action(self.request.user, "inventory.settings", item, item.branch, self.request,
                   reorder_point=item.reorder_point, allow_negative=item.allow_negative)

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        product_id = int(request.data["product"])
        delta = int(request.data["qty"])
        reason = request.data.get("reason", "")
        with transaction.atomic():
            item = services.adjust_stock(request.branch, product_id, delta, request.user, reason)
        log_action(request.user, "inventory.adjusted", item, request.branch, request, qty=delta, reason=reason)
        return Response(InventoryItemSerializer(InventoryItem.objects.get(pk=item.pk)).data)


class MovementViewSet(BranchScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = MovementSerializer
    queryset = StockMovement.objects.select_related("product", "by")
    default_perms = ("inventory.view",)

    def get_queryset(self):
        qs = super().get_queryset()
        if product := self.request.query_params.get("product"):
            qs = qs.filter(product_id=product)
        if mtype := self.request.query_params.get("type"):
            qs = qs.filter(type=mtype)
        return qs


# --- Transfers ---------------------------------------------------------------

class TransferLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockTransferLine
        fields = ["id", "product", "sku", "name", "qty_sent", "qty_received", "unit_cost_paisa", "variance_reason"]
        read_only_fields = ["qty_received", "unit_cost_paisa", "variance_reason"]


class TransferSerializer(serializers.ModelSerializer):
    lines = TransferLineSerializer(many=True)
    from_branch_code = serializers.CharField(source="from_branch.code", read_only=True)
    to_branch_code = serializers.CharField(source="to_branch.code", read_only=True)
    by_name = serializers.CharField(source="by.username", read_only=True)

    class Meta:
        model = StockTransfer
        fields = ["id", "from_branch", "from_branch_code", "to_branch", "to_branch_code", "status", "started_at",
                  "by_name", "dispatched_at", "received_at", "note", "lines"]
        read_only_fields = ["from_branch", "status", "dispatched_at", "received_at"]

    def validate_to_branch(self, branch):
        if branch == self.context["request"].branch:
            raise serializers.ValidationError("Cannot transfer to the same branch")
        if not branch.is_active:
            raise serializers.ValidationError("Destination branch is inactive")
        return branch

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("Add at least one line")
        if any(l["qty_sent"] <= 0 for l in lines):
            raise serializers.ValidationError("Quantities must be positive")
        return lines

    @transaction.atomic
    def create(self, data):
        lines = data.pop("lines")
        transfer = StockTransfer.objects.create(**data)
        StockTransferLine.objects.bulk_create([StockTransferLine(transfer=transfer, **l) for l in lines])
        return transfer


class TransferViewSet(PerActionPermissionMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                      mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Visible to both the sending and receiving branch (§6, §9.9)."""

    serializer_class = TransferSerializer
    default_perms = ("transfer.manage",)
    action_perms = {"list": ("inventory.view",), "retrieve": ("inventory.view",)}

    def get_queryset(self):
        return (StockTransfer.objects.visible_to(self.request.branch)
                .select_related("from_branch", "to_branch", "by").prefetch_related("lines__product"))

    def perform_create(self, serializer):
        transfer = serializer.save(from_branch=self.request.branch, by=self.request.user)
        log_action(self.request.user, "transfer.created", transfer, self.request.branch, self.request)

    def _locked(self, side):
        transfer = self.get_object()
        if getattr(transfer, f"{side}_branch_id") != self.request.branch.id:
            raise PermissionDenied(f"Only the {side} branch can do this")
        return StockTransfer.objects.select_for_update().get(pk=transfer.pk)

    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_transfer(self, request, pk=None):
        with transaction.atomic():
            transfer = self._locked("from")
            services.dispatch_transfer(transfer, request.user)
        log_action(request.user, "transfer.dispatched", transfer, request.branch, request)
        return Response(self.get_serializer(self.get_queryset().get(pk=transfer.pk)).data)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        received = {
            int(l["id"]): (int(l["qty_received"]), l.get("variance_reason", ""))
            for l in request.data.get("lines", [])
        }
        with transaction.atomic():
            transfer = self._locked("to")
            services.receive_transfer(transfer, received, request.user)
        log_action(request.user, "transfer.received", transfer, request.branch, request)
        return Response(self.get_serializer(self.get_queryset().get(pk=transfer.pk)).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        with transaction.atomic():
            transfer = self._locked("from")
            services.cancel_transfer(transfer, request.user)
        log_action(request.user, "transfer.cancelled", transfer, request.branch, request)
        return Response(self.get_serializer(self.get_queryset().get(pk=transfer.pk)).data)


# --- Stocktakes -----------------------------------------------------------------

class StocktakeLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StocktakeLine
        fields = ["id", "product", "sku", "name", "system_qty", "counted_qty", "variance", "reason"]


class StocktakeSerializer(serializers.ModelSerializer):
    by_name = serializers.CharField(source="by.username", read_only=True)
    line_count = serializers.IntegerField(source="lines.count", read_only=True)
    counted_count = serializers.SerializerMethodField()

    class Meta:
        model = Stocktake
        fields = ["id", "status", "started_at", "finished_at", "by_name", "scope", "line_count", "counted_count"]

    def get_counted_count(self, st):
        return st.lines.filter(counted_qty__isnull=False).count()


class StocktakeViewSet(BranchScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       viewsets.GenericViewSet):
    serializer_class = StocktakeSerializer
    queryset = Stocktake.objects.select_related("by")
    default_perms = ("stocktake.manage",)

    def create(self, request):
        category = None
        if cid := request.data.get("category"):
            category = Category.objects.get(pk=cid)
        with transaction.atomic():
            st = services.start_stocktake(request.branch, request.user, category)
        log_action(request.user, "stocktake.started", st, request.branch, request, scope=st.scope)
        return Response(StocktakeSerializer(st).data, status=201)

    @action(detail=True, methods=["get", "post"])
    def lines(self, request, pk=None):
        st = self.get_object()
        if request.method == "POST":
            if st.status != Stocktake.COUNTING:
                raise serializers.ValidationError({"detail": "Stocktake is not open for counting"})
            updates = {int(u["id"]): u for u in request.data.get("lines", [])}
            lines = list(st.lines.filter(id__in=updates))
            for line in lines:
                u = updates[line.id]
                counted = u.get("counted_qty")
                line.counted_qty = None if counted in (None, "") else int(counted)
                line.reason = u.get("reason", line.reason) or ""
            StocktakeLine.objects.bulk_update(lines, ["counted_qty", "reason"])
        qs = st.lines.select_related("product").order_by("product__name")
        if q := request.query_params.get("q"):
            qs = qs.filter(product__name__icontains=q) | qs.filter(product__sku__icontains=q)
        return Response(StocktakeLineSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        with transaction.atomic():
            st = Stocktake.objects.select_for_update().get(pk=self.get_object().pk)
            services.finish_stocktake(st, request.user)
        log_action(request.user, "stocktake.finished", st, request.branch, request)
        return Response(StocktakeSerializer(st).data)

