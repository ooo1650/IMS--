"""Django settings for the IMS project.

Configuration comes from environment variables. Secrets (SECRET_KEY, FonePay
credentials) must never be committed — see .env.example.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "tenancy",
    "accounts",
    "catalog",
    "inventory",
    "sales",
    "purchasing",
    "reporting",
    "admin_extras",
    "compliance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tenancy.middleware.TenantMiddleware",
    "admin_extras.audit.AuditContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ims.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ims.wsgi.application"

# Production: postgres://user:pass@host:5432/ims. SQLite is a dev/test fallback
# only — row locks (SELECT ... FOR UPDATE) are a no-op there.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60
    )
}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    # Dev only: wait for the write lock instead of failing on concurrent requests.
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kathmandu"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions + CSRF (not JWT; see design doc §7) ---
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173")

CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ["content-type", "x-csrftoken", "x-branch-id", "idempotency-key"]

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "ims.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "ims.api.exception_handler",
}

# --- Domain configuration ---
IMS = {
    # Returns with a total above this (paisa) need manager approval.
    "RETURN_APPROVAL_THRESHOLD_PAISA": int(os.environ.get("IMS_RETURN_APPROVAL_THRESHOLD", 500000)),
    # Stocktake variances (absolute units) above this require a reason.
    "STOCKTAKE_VARIANCE_REASON_THRESHOLD": int(os.environ.get("IMS_STOCKTAKE_VARIANCE_THRESHOLD", 5)),
    "FONEPAY_EXPIRY_MINUTES": 10,
    # Cash totals round to this many paisa (100 = nearest rupee); 1 = no rounding.
    # The difference is stored in Sale.round_adjust_paisa.
    "CASH_ROUNDING_PAISA": int(os.environ.get("IMS_CASH_ROUNDING_PAISA", 1)),
    # Cashiers may discount up to this share of the gross (basis points) without approval.
    "CASHIER_MAX_DISCOUNT_BP": int(os.environ.get("IMS_CASHIER_MAX_DISCOUNT_BP", 500)),
    # Offline login is refused once the cached session is older than this.
    "OFFLINE_LOGIN_MAX_AGE_HOURS": 24,
}

# FonePay — the official API is not confirmed (design doc §14). The "mock"
# backend simulates a sandbox; swap in the real client once docs are in hand.
FONEPAY = {
    "BACKEND": os.environ.get("FONEPAY_BACKEND", "mock"),
    "MERCHANT_CODE": os.environ.get("FONEPAY_MERCHANT_CODE", ""),
    "USERNAME": os.environ.get("FONEPAY_USERNAME", ""),
    "PASSWORD": os.environ.get("FONEPAY_PASSWORD", ""),
    "SECRET": os.environ.get("FONEPAY_SECRET", "dev-fonepay-secret"),
    "BASE_URL": os.environ.get("FONEPAY_BASE_URL", ""),
}

if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
