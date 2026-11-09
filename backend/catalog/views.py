import hashlib
import json

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import HasBranchPerm, can
from admin_extras.models import log_action
from ims.api import MAX_PAISA, OrgFieldsMixin, OrgScopedMixin, to_int
from inventory.models import InventoryItem

from .images import image_urls, remove_if_unused, store_image
from .models import Barcode, BranchPrice, Category, Product, Supplier


class CatalogPermission(BasePermission):
    """Catalog is org-wide. Reading needs catalog.view at the active branch (or
    org); writing needs catalog.manage. ``write_perm`` can be overridden."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        perm = "catalog.view" if request.method in SAFE_METHODS else getattr(view, "write_perm", "catalog.manage")
        return user.has_org_perm(perm, request._request.org) or user.has_branch_perm(perm, request._request.branch)


class CategorySerializer(OrgFieldsMixin, serializers.ModelSerializer):
    org_fields = ("parent",)

    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "pan", "phone", "email", "address"]

    def validate_pan(self, v):
        if v and not (v.isdigit() and len(v) == 9):
            raise serializers.ValidationError("PAN must be 9 digits")
        return v


class BarcodeSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)

    class Meta:
        model = Barcode
        fields = ["id", "code", "type", "pack_qty"]
        extra_kwargs = {"code": {"validators": []}}


class ProductSerializer(OrgFieldsMixin, serializers.ModelSerializer):
    org_fields = ("category", "supplier")
    barcodes = BarcodeSerializer(many=True, required=False)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)
    branch_price_paisa = serializers.SerializerMethodField()
    on_hand = serializers.SerializerMethodField()
    reorder_point = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "category", "category_name", "supplier", "supplier_name", "unit",
                  "cost_price_paisa", "sale_price_paisa", "branch_price_paisa", "tax_rate", "tax_inclusive",
                  "tax_category", "tax_category_code", "tax_kind", "hs_code", "excise_rate",
                  "input_credit_blocked_pct", "is_active", "archived_at", "barcodes", "on_hand", "reorder_point", "image_url", "thumb_url", "updated_at"]
        read_only_fields = ["archived_at", "updated_at", "tax_rate"]

    tax_category_code = serializers.CharField(source="tax_category.code", read_only=True, default=None)
    tax_kind = serializers.CharField(source="tax_category.kind", read_only=True, default=None)

    def _sync_rate(self, product):
        """Keep the cached tax_rate equal to the category's rate in force today."""
        if product.tax_category_id:
            from .models import tax_profile

            rate = tax_profile(product)[0]
            if rate != product.tax_rate:
                product.tax_rate = rate
                product.save(update_fields=["tax_rate"])
        return product

    def get_branch_price_paisa(self, p):
        prices = getattr(p, "_branch_prices", None)
        return prices[0].sale_price_paisa if prices else None

    def get_image_url(self, p):
        return image_urls(p.image_key)[0]

    def get_thumb_url(self, p):
        return image_urls(p.image_key)[1]

    def get_on_hand(self, p):
        items = getattr(p, "_items", None)
        return items[0].quantity_on_hand if items else None

    def get_reorder_point(self, p):
        items = getattr(p, "_items", None)
        return items[0].reorder_point if items else None

    def to_representation(self, p):
        data = super().to_representation(p)
        request = self.context.get("request")
        # Cost is a manager/admin concern; cashiers never see it.
        if request and not (can(request, "catalog.manage", org=True) or can(request, "inventory.view")):
            data.pop("cost_price_paisa", None)
        return data

    def _org(self):
        return self.instance.org if self.instance else self.context.get("org")

    def validate_sku(self, sku):
        # SKUs are unique within a business; another business may use the same one.
        clash = Product.objects.filter(org=self._org(), sku=sku)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This SKU is already used in this business")
        return sku

    def validate_barcodes(self, barcodes):
        codes = [b["code"] for b in barcodes]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError("Duplicate barcode")
        clash = Barcode.objects.filter(org=self._org(), code__in=codes)
        if self.instance:
            clash = clash.exclude(product=self.instance)
        if clash.exists():
            raise serializers.ValidationError(f"Barcode already used: {', '.join(clash.values_list('code', flat=True))}")
        return barcodes

    @transaction.atomic
    def create(self, data):
        barcodes = data.pop("barcodes", [])
        product = Product.objects.create(**data)
        Barcode.objects.bulk_create([Barcode(product=product, org_id=product.org_id,
                                             **{k: v for k, v in b.items() if k != "id"}) for b in barcodes])
        return self._sync_rate(product)

    @transaction.atomic
    def update(self, product, data):
        barcodes = data.pop("barcodes", None)
        for k, v in data.items():
            setattr(product, k, v)
        product.save()
        if barcodes is not None:
            product.barcodes.all().delete()
            Barcode.objects.bulk_create([Barcode(product=product, org_id=product.org_id,
                                                 **{k: v for k, v in b.items() if k != "id"}) for b in barcodes])
        return self._sync_rate(product)


class CategoryViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated, CatalogPermission]
    pagination_class = None
    http_method_names = ["get", "post", "patch", "put"]


class SupplierViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated, CatalogPermission]
    pagination_class = None
    write_perm = "purchasing.manage"
    http_method_names = ["get", "post", "patch", "put"]

    def check_permissions(self, request):
        # Org catalog managers may also maintain suppliers.
        if request.method not in SAFE_METHODS and can(request, "catalog.manage", org=True):
            return
        super().check_permissions(request)


class ProductViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    """Products are archived, never deleted (§9.1). Each business has its own catalog."""

    queryset = Product.objects.all()

    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, CatalogPermission]
    http_method_names = ["get", "post", "patch", "put"]

    def get_queryset(self):
        qs = super().get_queryset().select_related("category", "supplier", "tax_category").prefetch_related("barcodes")
        branch = self.request.branch
        if branch:
            qs = qs.prefetch_related(
                Prefetch("branch_prices", BranchPrice.objects.filter(branch=branch), to_attr="_branch_prices"),
                Prefetch("inventory", InventoryItem.objects.filter(branch=branch), to_attr="_items"),
            )
        params = self.request.query_params
        if q := params.get("q"):
            qs = qs.filter(Q(name__icontains=q) | Q(sku__iexact=q) | Q(sku__icontains=q) | Q(barcodes__code=q)).distinct()
        if params.get("archived") == "1":
            qs = qs.filter(archived_at__isnull=False)
        elif params.get("archived") != "all":
            qs = qs.filter(archived_at__isnull=True)
        if cat := params.get("category"):
            qs = qs.filter(category_id=cat)
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        log_action(self.request.user, "product.created", serializer.instance, request=self.request)

    def perform_update(self, serializer):
        before = {f: getattr(serializer.instance, f) for f in ("sale_price_paisa", "cost_price_paisa", "tax_rate")}
        product = serializer.save()
        log_action(self.request.user, "product.updated", product, request=self.request,
                   before={k: str(v) for k, v in before.items()})

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        product = self.get_object()
        product.archived_at = timezone.now()
        product.save(update_fields=["archived_at"])
        log_action(request.user, "product.archived", product, request=request)
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        product = self.get_object()
        product.archived_at = None
        product.save(update_fields=["archived_at"])
        log_action(request.user, "product.restored", product, request=request)
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser])
    def image(self, request, pk=None):
        """Upload or replace the photo (multipart ``image``). Audited via the Product diff."""
        product = self.get_object()
        upload = request.FILES.get("image")
        if upload is None:
            raise serializers.ValidationError({"image": "Choose an image file"})
        self._set_image(product, store_image(upload))
        return Response(self.get_serializer(self.get_queryset().get(pk=product.pk)).data)

    @action(detail=True, methods=["post"], url_path="remove-image")
    def remove_image(self, request, pk=None):
        product = self.get_object()
        self._set_image(product, "")
        return Response(self.get_serializer(self.get_queryset().get(pk=product.pk)).data)

    @staticmethod
    def _set_image(product, key):
        old = product.image_key
        if old == key:
            return
        product.image_key = key
        product.save(update_fields=["image_key", "updated_at"])
        transaction.on_commit(lambda: remove_if_unused(old))

    @action(detail=True, methods=["post"], url_path="branch-price")
    def branch_price(self, request, pk=None):
        """Set (or clear with null) the active branch's price override."""
        product = self.get_object()
        branch = request.branch
        if branch is None:
            raise PermissionDenied("Select a branch")
        price = request.data.get("sale_price_paisa")
        if price in (None, ""):
            BranchPrice.objects.filter(branch=branch, product=product).delete()
        else:
            price = to_int(price, "sale_price_paisa", min_value=0, max_value=MAX_PAISA)
            BranchPrice.objects.update_or_create(branch=branch, product=product, defaults={"sale_price_paisa": price})
        log_action(request.user, "product.branch_price", product, branch, request, price=price)
        return Response(self.get_serializer(self.get_queryset().get(pk=product.pk)).data)


class PosCatalogView(viewsets.ViewSet):
    """Offline catalog snapshot for the PWA (§10.3 step 4). Deliberately
    excludes cost prices — cost is a server concern.

    ``version`` is a hash of the content. A till sends the version it has
    (``?version=``); if nothing changed the reply is ``{"unchanged": true}``
    without the product list, so reopening the POS doesn't re-download and
    re-store the whole catalog."""

    permission_classes = [HasBranchPerm("pos.sell")]

    def list(self, request):
        branch = request.branch
        overrides = dict(BranchPrice.objects.filter(branch=branch).values_list("product_id", "sale_price_paisa"))
        stock = dict(InventoryItem.objects.filter(branch=branch).values_list("product_id", "quantity_on_hand"))
        from sales.numbering import local_today

        from .models import tax_profile

        today = local_today(branch)
        products = []
        for p in Product.objects.active().filter(org_id=branch.org_id).select_related("tax_category").prefetch_related(
                "tax_category__rates", "barcodes"):
            rate, kind, levy_type, levy_rate, _ = tax_profile(p, today)
            products.append({
                "id": p.id, "sku": p.sku, "name": p.name, "unit": p.unit,
                "sale_price_paisa": overrides.get(p.id, p.sale_price_paisa),
                "tax_rate": str(rate), "tax_inclusive": p.tax_inclusive, "tax_kind": kind,
                "excise_rate": str(p.excise_rate), "levy_type": levy_type, "levy_rate": str(levy_rate),
                "barcodes": [{"code": b.code, "pack_qty": b.pack_qty} for b in p.barcodes.all()],
                "stock_estimate": stock.get(p.id, 0),
                "thumb": image_urls(p.image_key)[1],
            })
        version = hashlib.sha256(json.dumps([branch.id, products], sort_keys=True).encode()).hexdigest()[:32]
        reply = {"branch_id": branch.id, "generated_at": timezone.now(), "version": version}
        if request.query_params.get("version") == version:
            return Response({**reply, "unchanged": True})
        return Response({**reply, "products": products})
