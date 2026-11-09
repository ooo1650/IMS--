from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import HasBranchPerm, HasOrgPerm, require_branch_perm
from tenancy.models import Branch

from . import analytics, queries


class DashboardView(APIView):
    """Business-wide dashboard: every branch of the active business (§6)."""

    permission_classes = [HasOrgPerm("dashboard.view")]

    def get(self, request):
        branches = list(Branch.objects.filter(org=request.org, is_active=True))
        summaries = [queries.branch_summary(b) for b in branches]
        totals = {
            k: sum(s[k]["total_paisa"] for s in summaries) for k in ("today", "week", "month")
        }
        return Response({
            "totals": {**totals, "inventory_value_paisa": sum(s["inventory_value_paisa"] for s in summaries),
                       "stock_conflicts": sum(s["stock_conflicts"] for s in summaries),
                       "pending_pos": sum(s["pending_pos"] for s in summaries),
                       "low_stock": sum(s["low_stock"] for s in summaries)},
            "branches": summaries,
            "trend": queries.daily_trend(branches),
            "top_products": queries.top_products(branches),
            "slow_movers": queries.slow_movers(branches),
            "low_stock": queries.low_stock_alerts(branches),
            "stock_conflicts": queries.stock_conflicts(branches),
            "fonepay_mismatches": queries.fonepay_mismatches(request.org),
        })


class BranchReportView(APIView):
    permission_classes = [HasBranchPerm("reports.view")]

    def get(self, request):
        branches = [request.branch]
        return Response({
            "summary": queries.branch_summary(request.branch),
            "trend": queries.daily_trend(branches),
            "top_products": queries.top_products(branches),
            "slow_movers": queries.slow_movers(branches),
            "low_stock": queries.low_stock_alerts(branches),
            "stock_conflicts": queries.stock_conflicts(branches),
        })


class AnalyticsView(APIView):
    """Sales analytics for a period vs the one before it (reporting/analytics.py).

    ?days=7|30|90 (default 30). Org users with dashboard.view get every active
    branch, or one with ?branch=<id>. Everyone else gets the active branch only
    and needs reports.view there.
    """

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            days = 30
        if days not in analytics.PERIODS:
            raise ValidationError({"days": f"Use one of {', '.join(map(str, analytics.PERIODS))}."})
        if request.user.has_org_perm("dashboard.view", request.org):
            branches = Branch.objects.filter(org=request.org, is_active=True).order_by("code")
            if (raw := request.query_params.get("branch")) and raw != "all":
                branches = branches.filter(pk=raw if raw.isdigit() else 0)
                if not branches.exists():
                    raise NotFound("Branch not found")
        else:
            if request.branch is None:
                raise PermissionDenied("Choose a branch first (the branch picker in the sidebar). If yours isn't listed, ask an administrator for access.")
            require_branch_perm(request, "reports.view")
            branches = [request.branch]
        return Response(analytics.analytics(branches, days))


class SetupStatusView(APIView):
    """Which first steps the active business has done, for the "get started"
    checklist: each is one cheap EXISTS query, nothing is stored."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.models import UserBranchRole
        from catalog.models import Product, Supplier
        from inventory.models import InventoryItem
        from sales.models import Sale

        org = getattr(request, "org", None)
        if org is None:
            raise NotFound("Pick a business first")
        return Response({
            "products": Product.objects.filter(org=org).exists(),
            "stock": InventoryItem.objects.filter(branch__org=org, quantity_on_hand__gt=0).exists(),
            "suppliers": Supplier.objects.filter(org=org).exists(),
            "staff": UserBranchRole.objects.filter(org=org).values("user").distinct().count() > 1,
            "sale": Sale.objects.filter(branch__org=org).exists(),
        })
