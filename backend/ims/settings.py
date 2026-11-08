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
    "ims.platform_guard.PlatformGuardMiddleware",
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
# Transaction-mode poolers (Supabase/Supavisor port 6543, PgBouncer
# pool_mode=transaction) hand each transaction a different server connection:
# no prepared statements, no server-side cursors, no session advisory locks
# (run_scheduler skips them). Detected from port 6543, or set
# IMS_DB_TRANSACTION_POOLER. Session mode (Supabase pooler port 5432) needs none of this.
IMS_DB_TRANSACTION_POOLER = env_bool(
    "IMS_DB_TRANSACTION_POOLER", str(DATABASES["default"].get("PORT")) == "6543")
if IMS_DB_TRANSACTION_POOLER:
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES["default"].setdefault("OPTIONS", {})["prepare_threshold"] = None
    DATABASES["default"]["CONN_MAX_AGE"] = 0
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    # Dev only: wait for the write lock instead of failing on concurrent requests.
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]

# --- Email (sign-in codes, password resets) ---
# The SMTP server, user, password and sender are set in the operator console
# (admin_extras.SystemSettings) and read on every send by this backend; with
# no server configured, codes are written to the backend log.
EMAIL_BACKEND = "admin_extras.mail.DatabaseSmtpBackend"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = "IMS <no-reply@localhost>"  # used when the console has no sender set
# Send mail on a background thread so replies don't wait on SMTP (tests turn it off).
IMS_EMAIL_ASYNC = env_bool("IMS_EMAIL_ASYNC", True)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kathmandu"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Product photos (catalog/images.py). Content-addressed file names, so the web
# server can cache /media/ forever. Docker mounts the `media` volume here.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("IMS_MEDIA_ROOT", BASE_DIR / "media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions + CSRF (not JWT; see design doc §7) ---
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173")

# Production serves SPA + API from one origin, so CORS stays off unless configured.
CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ORIGINS", "http://localhost:5173" if DEBUG else "")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ["content-type", "x-csrftoken", "x-branch-id", "x-org-id", "idempotency-key"]

if not DEBUG:
    # Behind nginx, which terminates TLS and sets X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^api/v1/health$"]  # container health checks talk plain HTTP
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Reverse proxies in front of Django that append to X-Forwarded-For (ims/net.py).
IMS_TRUSTED_PROXIES = int(os.environ.get("IMS_TRUSTED_PROXIES", 0 if DEBUG else 1))

# Hosting without the bundled nginx (e.g. Vercel + Render; docs/DEPLOYMENT.md):
# the console's address(es) and, optionally, the operators' networks. Only
# these may use /api/v1/platform/ (ims/platform_guard.py). Empty = nginx does it.
IMS_PLATFORM_ORIGINS = env_list("IMS_PLATFORM_ORIGINS", "")
IMS_PLATFORM_ALLOW = env_list("IMS_PLATFORM_ALLOW", "")
# Serve product photos (/media/) from Django when no web server does it.
IMS_SERVE_MEDIA = env_bool("IMS_SERVE_MEDIA", False)

# TESTING PHASE ONLY: one-click sign-in as any active user, no password or
# emailed code (accounts.views.TestLoginView). Off by default; only answers
# requests from private/loopback addresses; every use is audit-logged and the
# app shows a warning banner while it is on. Never enable on a live server.
IMS_TEST_LOGIN = env_bool("IMS_TEST_LOGIN", False)
# Operator console: "Load demo businesses" only where this is on (dev/demo boxes).
IMS_ALLOW_DEMO_SEED = env_bool("IMS_ALLOW_DEMO_SEED", DEBUG)
if IMS_TEST_LOGIN:
    import logging as _logging
    _logging.getLogger("ims").warning("IMS_TEST_LOGIN is ON: anyone on the local network can sign in as any user")

# Sign-in lockout limits and the roles that need an emailed code are in the
# console (SystemSettings); business rules are per business (tenancy.BusinessRules).

# Containers log to stdout; security warnings (bad hosts, CSRF failures) included.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("IMS_LOG_LEVEL", "INFO")},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "ims.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "ims.api.exception_handler",
}

# --- Domain configuration ---
# Cash rounding, discount limit, return approval and stocktake variance are
# per business (tenancy.BusinessRules, edited in the operator console).
IMS = {
    "FONEPAY_EXPIRY_MINUTES": 10,
    # Offline login is refused once the cached session is older than this.
    "OFFLINE_LOGIN_MAX_AGE_HOURS": 24,
}

# FonePay configuration lives in the DATABASE (Admin → Branches & registers →
# FonePay): the mode and API address in sales.FonepaySettings, merchant code,
# username, password and secret per branch in sales.FonepayMerchant (secrets
# encrypted with CBMS_ENCRYPTION_KEY). Only guard rails stay here, so an admin
# account cannot weaken them:
# * demo ("mock") mode lets anyone with pos.sell mark a QR paid, so it is only
#   selectable on dev servers unless IMS_ALLOW_FONEPAY_MOCK=true;
# * the live API address must be https on one of these domains, so merchant
#   credentials can't be sent anywhere else.
FONEPAY_MOCK_ALLOWED = env_bool("IMS_ALLOW_FONEPAY_MOCK", DEBUG)
FONEPAY_ALLOWED_HOSTS = env_list("IMS_FONEPAY_HOSTS", "fonepay.com")
# Signs callbacks from the in-process mock gateway (dev/tests only).
FONEPAY_MOCK_SECRET = "dev-fonepay-secret"

if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

