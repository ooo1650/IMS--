from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasBranchPerm, HasOrgPerm
from tenancy.models import Branch

from . import queries


class DashboardView(APIView):
    """Org-wide dashboard; bypasses the tenant filter by design (§6)."""

    permission_classes = [HasOrgPerm("dashboard.view")]

    def get(self, request):
        branches = list(Branch.objects.filter(is_active=True))
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
            "fonepay_mismatches": queries.fonepay_mismatches(),
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
