from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts import views as accounts
from admin_extras import platform
from admin_extras import views as admin_extras
from catalog import views as catalog
from compliance import views as compliance
from inventory import views as inventory
from purchasing import views as purchasing
from reporting import views as reporting
from sales import views as sales
from tenancy import views as tenancy

from .api import HealthView

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
router.register("fonepay-merchants", sales.FonepayMerchantViewSet, basename="fonepay-merchant")

api = [
    path("health", HealthView.as_view()),
    path("auth/csrf", accounts.CsrfView.as_view()),
    path("auth/brand", accounts.BrandView.as_view()),
    path("auth/login", accounts.LoginView.as_view()),
    path("auth/login/verify", accounts.LoginVerifyView.as_view()),
    path("auth/login/resend", accounts.LoginResendView.as_view()),
    path("auth/password/forgot", accounts.ForgotPasswordView.as_view()),
    path("auth/password/reset", accounts.ResetPasswordView.as_view()),
    path("auth/signup", accounts.SignupView.as_view()),
    path("auth/password/change", accounts.ChangePasswordView.as_view()),
    path("auth/email", accounts.EmailChangeView.as_view()),
    path("auth/email/verify", accounts.EmailVerifyView.as_view()),
    path("auth/2fa", accounts.TwoFactorView.as_view()),
    path("auth/sign-out-everywhere", accounts.SignOutEverywhereView.as_view()),
    path("auth/logout", accounts.LogoutView.as_view()),
    path("auth/test-login", accounts.TestLoginView.as_view()),  # testing phase only
    path("auth/me", accounts.MeView.as_view()),
    path("auth/active-branch", accounts.ActiveBranchView.as_view()),
    path("auth/active-org", accounts.ActiveOrgView.as_view()),
    path("auth/roles", accounts.RolesView.as_view()),
    path("pos/config", sales.PosConfigView.as_view()),
    path("pos/checkout", sales.CheckoutCashView.as_view()),
    path("pos/checkout/initiate", sales.FonepayInitiateView.as_view()),
    path("pos/checkout/sync", sales.OfflineSyncView.as_view()),
    path("pos/sale/<int:pk>/payment-status", sales.PaymentStatusView.as_view()),
    path("pos/fonepay/callback", sales.FonepayCallbackView.as_view()),
    path("pos/fonepay/mock-pay/<str:prn>", sales.FonepayMockPayView.as_view()),
    path("pos/registers/<int:register_id>/receipt-gaps", sales.ReceiptGapsView.as_view()),
    path("fonepay-settings", sales.FonepaySettingsView.as_view()),
    path("reports/dashboard", reporting.DashboardView.as_view()),
    path("reports/branch", reporting.BranchReportView.as_view()),
    path("reports/analytics", reporting.AnalyticsView.as_view()),
    path("compliance/settings", compliance.SettingsView.as_view()),
    path("compliance/fiscal-years", compliance.FiscalYearsView.as_view()),
    path("compliance/turnover", compliance.TurnoverView.as_view()),
    path("compliance/reports/accountant-export", compliance.AccountantExportView.as_view()),
    path("compliance/reports/<str:name>", compliance.ComplianceReportView.as_view()),
    path("compliance/documents/<str:doc_type>/<int:pk>", compliance.DocumentView.as_view()),
    path("import/template/<str:kind>", admin_extras.ImportTemplateView.as_view()),
    path("import/products", admin_extras.ProductImportView.as_view()),
    path("import/opening-stock", admin_extras.OpeningStockImportView.as_view()),
    # Operator console (platform admins only; nginx serves these on the console's server block only).
    path("platform/overview", platform.OverviewView.as_view()),
    path("platform/businesses", platform.BusinessListView.as_view()),
    path("platform/businesses/<int:pk>", platform.BusinessDetailView.as_view()),
    path("platform/businesses/<int:pk>/rules", platform.RulesView.as_view()),
    path("platform/businesses/<int:pk>/tax-settings", platform.TaxSettingsView.as_view()),
    path("platform/businesses/<int:pk>/fonepay", platform.FonepayView.as_view()),
    path("platform/businesses/<int:pk>/fonepay/merchants/<int:branch_pk>", platform.FonepayMerchantView.as_view()),
    path("platform/businesses/<int:pk>/branches", platform.BranchesView.as_view()),
    path("platform/branches/<int:branch_pk>", platform.BranchDetailView.as_view()),
    path("platform/branches/<int:branch_pk>/registers", platform.RegistersView.as_view()),
    path("platform/registers/<int:register_pk>", platform.RegisterDetailView.as_view()),
    path("platform/users", platform.UsersView.as_view()),
    path("platform/users/<int:user_pk>", platform.UserDetailView.as_view()),
    path("platform/users/<int:user_pk>/<str:action>", platform.UserActionView.as_view()),
    path("platform/system", platform.SystemSettingsView.as_view()),
    path("platform/system/test-email", platform.TestEmailView.as_view()),
    path("platform/jobs", platform.JobsView.as_view()),
    path("platform/jobs/<str:name>", platform.JobRunView.as_view()),
    path("platform/demo-seed", platform.DemoSeedView.as_view()),
    path("platform/audit", platform.AuditView.as_view()),
    path("", include(router.urls)),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include(api)),
]
if settings.IMS_SERVE_MEDIA and not settings.DEBUG:
    # No web server in front (e.g. Render): Django serves the product photos.
    # Content-addressed names (catalog/images.py), so they can be cached forever.
    from django.urls import re_path
    from django.views.static import serve

    def _media(request, path):
        response = serve(request, path, document_root=settings.MEDIA_ROOT)
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    urlpatterns += [re_path(r"^media/(?P<path>.+)$", _media)]
else:
    # Dev only (a no-op when DEBUG is off): nginx serves /media/ in production.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
