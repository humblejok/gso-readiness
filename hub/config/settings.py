"""Explicit development/production configuration; no paid services required locally."""

import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
if os.environ.get("HUB_ENV", "development") not in {"development", "production"}:
    raise ImproperlyConfigured("HUB_ENV must be development or production.")
PRODUCTION = os.environ.get("HUB_ENV", "development") == "production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-not-for-hosting")
DEBUG = not PRODUCTION
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
PUBLIC_URL = os.environ.get("HUB_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
DEPLOYMENT_MODE = os.environ.get("HUB_DEPLOYMENT_MODE", "shared")
if DEPLOYMENT_MODE not in {"shared", "dedicated"}:
    raise ImproperlyConfigured("HUB_DEPLOYMENT_MODE must be shared or dedicated.")
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=0,
    )
}
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "axes",
    "hubapp",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "hubapp.middleware.WorkspaceMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "hubapp.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = LOGIN_URL
PASSWORD_RESET_TIMEOUT = 3600
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Finding Hub <noreply@example.invalid>")
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_SECURE = PRODUCTION
CSRF_COOKIE_SECURE = PRODUCTION
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_HSTS_SECONDS = 31536000 if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCTION
SECURE_HSTS_PRELOAD = PRODUCTION
SECURE_REFERRER_POLICY = "same-origin"
# Set only when a trusted ingress strips and overwrites this header.
if os.environ.get("HUB_TRUST_PROXY", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", PUBLIC_URL).split(",")
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["hubapp.authentication.WorkspaceTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PARSER_CLASSES": ["hubapp.parsers.BoundedJSONParser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
OUTBOUND_INTEGRATIONS_ENABLED = (
    os.environ.get("OUTBOUND_INTEGRATIONS_ENABLED", "false").lower() == "true"
)
# Operator-reviewed hostname policy; private networks only on dedicated deployments.
JIRA_ALLOWED_HOSTS = set(filter(None, os.environ.get("JIRA_ALLOWED_HOSTS", "").split(",")))
JIRA_PRIVATE_HOSTS = set(filter(None, os.environ.get("JIRA_PRIVATE_HOSTS", "").split(",")))
JIRA_CA_BUNDLE = os.environ.get("JIRA_CA_BUNDLE") or True
SECRET_ENCRYPTION_KEYS = list(filter(None, os.environ.get("HUB_ENCRYPTION_KEYS", "").split(",")))
BILLING_PROVIDER = os.environ.get("HUB_BILLING_PROVIDER", "manual")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICES = json.loads(os.environ.get("STRIPE_PRICES", "{}"))
SOURCE_URL = os.environ.get("HUB_SOURCE_URL", "")
TRIAL_DAYS = 14
PLANS = {
    "trial": {
        "repositories": 2,
        "members": 3,
        "imports_month": 100,
        "storage_mb": 100,
        "branding": False,
        "retention_days": 30,
    },
    "individual": {
        "repositories": 5,
        "members": 1,
        "imports_month": 500,
        "storage_mb": 500,
        "branding": False,
        "retention_days": 90,
    },
    "team": {
        "repositories": 30,
        "members": 15,
        "imports_month": 5000,
        "storage_mb": 5000,
        "branding": False,
        "retention_days": 365,
    },
    "enterprise": {
        "repositories": 500,
        "members": 500,
        "imports_month": 50000,
        "storage_mb": 50000,
        "branding": True,
        "retention_days": 730,
    },
}
if PRODUCTION:
    failures = []
    if len(SECRET_KEY) < 50:
        failures.append("provide a random DJANGO_SECRET_KEY of at least 50 characters")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        failures.append("production requires PostgreSQL")
    if urlsplit(PUBLIC_URL).scheme != "https" or "*" in ALLOWED_HOSTS:
        failures.append("configure HTTPS HUB_PUBLIC_URL and explicit DJANGO_ALLOWED_HOSTS")
    if EMAIL_BACKEND.endswith("console.EmailBackend"):
        failures.append("configure a production email backend")
    if not SOURCE_URL.startswith("https://"):
        failures.append("set HUB_SOURCE_URL to the corresponding source for this deployed release")
    if JIRA_PRIVATE_HOSTS and DEPLOYMENT_MODE != "dedicated":
        failures.append("private Jira hosts require a dedicated deployment")
    if failures:
        raise ImproperlyConfigured("; ".join(failures))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"credential_paths": {"()": "hubapp.logging.CredentialPathFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["credential_paths"]}},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
