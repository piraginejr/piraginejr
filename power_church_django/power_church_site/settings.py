from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


SECRET_KEY = os.environ.get("POWER_CHURCH_DJANGO_SECRET_KEY", "dev-only-change-before-production")
DEBUG = env_bool("POWER_CHURCH_DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("POWER_CHURCH_DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "auditlog",
    "anymail",
    "crispy_forms",
    "crispy_bootstrap5",
    "django_filters",
    "django_tables2",
    "djmoney",
    "formtools",
    "guardian",
    "import_export",
    "waffle",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "power_church_django.apps.accounts",
    "power_church_django.apps.people",
    "power_church_django.apps.contributions",
    "power_church_django.apps.imports",
    "power_church_django.apps.audit",
    "power_church_django.apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
    "waffle.middleware.WaffleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "power_church_site.urls"

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
                "power_church_django.services.branding.branding_context",
            ],
        },
    }
]

WSGI_APPLICATION = "power_church_site.wsgi.application"
ASGI_APPLICATION = "power_church_site.asgi.application"

if os.environ.get("POWER_CHURCH_POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POWER_CHURCH_POSTGRES_DB"],
            "USER": os.environ.get("POWER_CHURCH_POSTGRES_USER", "power_church"),
            "PASSWORD": os.environ.get("POWER_CHURCH_POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POWER_CHURCH_POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POWER_CHURCH_POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("POWER_CHURCH_DJANGO_DB_PATH", str(REPO_ROOT / "data" / "power_church_django.sqlite3")),
        }
    }

POWER_CHURCH_LEGACY_DB_PATH = os.environ.get(
    "POWER_CHURCH_LEGACY_DB_PATH",
    str(REPO_ROOT / "data" / "power_church_membros_importado.db"),
)
POWER_CHURCH_BRAND_LOGO_PATH = os.environ.get(
    "POWER_CHURCH_BRAND_LOGO_PATH",
    str(REPO_ROOT / "data" / "branding" / "power_church_logo.jpg"),
)

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]
ANONYMOUS_USER_NAME = "power_church_anonimo"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

DJANGO_TABLES2_TEMPLATE = "django_tables2/bootstrap5.html"

WAFFLE_FLAG_DEFAULT = False
WAFFLE_SWITCH_DEFAULT = False
WAFFLE_SAMPLE_DEFAULT = False

EMAIL_BACKEND = os.environ.get("POWER_CHURCH_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("POWER_CHURCH_EMAIL_HOST", "smtp.office365.com")
EMAIL_PORT = int(os.environ.get("POWER_CHURCH_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("POWER_CHURCH_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("POWER_CHURCH_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("POWER_CHURCH_EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("POWER_CHURCH_EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = os.environ.get("POWER_CHURCH_DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "recebimento@localhost")
POWER_CHURCH_EMAIL_PROVIDER = os.environ.get("POWER_CHURCH_EMAIL_PROVIDER", "smtp")
POWER_CHURCH_GRAPH_TENANT_ID = os.environ.get("POWER_CHURCH_GRAPH_TENANT_ID", "")
POWER_CHURCH_GRAPH_CLIENT_ID = os.environ.get("POWER_CHURCH_GRAPH_CLIENT_ID", "")
POWER_CHURCH_GRAPH_CLIENT_SECRET = os.environ.get("POWER_CHURCH_GRAPH_CLIENT_SECRET", "")
POWER_CHURCH_GRAPH_SENDER_USER = os.environ.get("POWER_CHURCH_GRAPH_SENDER_USER", "")
POWER_CHURCH_GRAPH_SCOPE = os.environ.get("POWER_CHURCH_GRAPH_SCOPE", "https://graph.microsoft.com/.default")
POWER_CHURCH_GRAPH_BASE_URL = os.environ.get("POWER_CHURCH_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
POWER_CHURCH_GRAPH_TIMEOUT_SECONDS = int(os.environ.get("POWER_CHURCH_GRAPH_TIMEOUT_SECONDS", "30"))
POWER_CHURCH_RECEIPT_REPLY_TO = os.environ.get("POWER_CHURCH_RECEIPT_REPLY_TO", "")
POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED = env_bool("POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED", True)
POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED = env_bool("POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED", True)
ANYMAIL = {}

DATA_UPLOAD_MAX_NUMBER_FILES = int(os.environ.get("POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FILES", "5000"))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.environ.get("POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FIELDS", "20000"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
