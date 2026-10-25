from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts import views as accounts
from admin_extras import views as admin_extras
from catalog import views as catalog
from compliance import views as compliance
from inventory import views as inventory
from purchasing import views as purchasing
from reporting import views as reporting
from sales import views as sales
from tenancy import views as tenancy

router = DefaultRouter()
router.register("users", accounts.UserViewSet, basename="user")
router.register("branches", tenancy.BranchViewSet, basename="branch")
router.register("registers", tenancy.RegisterViewSet, basename="register")
router.register("categories", catalog.CategoryViewSet, basename="category")
router.register("suppliers", catalog.SupplierViewSet, basename="supplier")
router.register("products", catalog.ProductViewSet, basename="product")
router.register("inventory", inventory.InventoryItemViewSet, basename="inventory")
router.register("movements", inventory.MovementViewSet, basename="movement")
router.register("transfers", inventory.TransferViewSet, basename="transfer")
router.register("stocktakes", inventory.StocktakeViewSet, basename="stocktake")
router.register("sales", sales.SaleViewSet, basename="sale")
router.register("credit-notes", sales.CreditNoteViewSet, basename="credit-note")
router.register("debit-notes", sales.DebitNoteViewSet, basename="debit-note")
router.register("purchase-returns", purchasing.PurchaseReturnViewSet, basename="purchase-return")
router.register("compliance/tax-categories", compliance.TaxCategoryViewSet, basename="tax-category")
router.register("compliance/cbms", compliance.CbmsPostViewSet, basename="cbms")
router.register("customers", sales.CustomerViewSet, basename="customer")
router.register("pos/shifts", sales.ShiftViewSet, basename="shift")
router.register("pos/held", sales.HeldCartViewSet, basename="held")
router.register("pos/catalog", catalog.PosCatalogView, basename="pos-catalog")
router.register("purchase-orders", purchasing.PurchaseOrderViewSet, basename="purchase-order")
router.register("audit-log", admin_extras.AuditLogViewSet, basename="audit-log")

api = [
    path("auth/csrf", accounts.CsrfView.as_view()),
    path("auth/login", accounts.LoginView.as_view()),
    path("auth/logout", accounts.LogoutView.as_view()),
    path("auth/me", accounts.MeView.as_view()),
    path("auth/active-branch", accounts.ActiveBranchView.as_view()),
    path("auth/roles", accounts.RolesView.as_view()),
    path("pos/config", sales.PosConfigView.as_view()),
    path("pos/checkout", sales.CheckoutCashView.as_view()),
    path("pos/checkout/initiate", sales.FonepayInitiateView.as_view()),
    path("pos/checkout/sync", sales.OfflineSyncView.as_view()),
    path("pos/sale/<int:pk>/payment-status", sales.PaymentStatusView.as_view()),
    path("pos/fonepay/callback", sales.FonepayCallbackView.as_view()),
    path("pos/fonepay/mock-pay/<str:prn>", sales.FonepayMockPayView.as_view()),
    path("pos/registers/<int:register_id>/receipt-gaps", sales.ReceiptGapsView.as_view()),
    path("reports/dashboard", reporting.DashboardView.as_view()),
    path("reports/branch", reporting.BranchReportView.as_view()),
    path("compliance/settings", compliance.SettingsView.as_view()),
    path("compliance/fiscal-years", compliance.FiscalYearsView.as_view()),
    path("compliance/turnover", compliance.TurnoverView.as_view()),
    path("compliance/reports/accountant-export", compliance.AccountantExportView.as_view()),
    path("compliance/reports/<str:name>", compliance.ComplianceReportView.as_view()),
    path("compliance/documents/<str:doc_type>/<int:pk>", compliance.DocumentView.as_view()),
    path("import/template/<str:kind>", admin_extras.ImportTemplateView.as_view()),
    path("import/products", admin_extras.ProductImportView.as_view()),
    path("import/opening-stock", admin_extras.OpeningStockImportView.as_view()),
    path("", include(router.urls)),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include(api)),
]
