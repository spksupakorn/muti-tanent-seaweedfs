import base64
import os
from pathlib import Path

from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")

INSTALLED_APPS = [
    "corsheaders",
    "accounts",
    "files",
    "audit",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # One transaction per request: the RLS settings live exactly that long.
        "ATOMIC_REQUESTS": True,
    }
}

# A second connection to the same database for work that must commit on its own (file uploads).
# It is not wrapped in the request transaction.
DATABASES["direct"] = {**DATABASES["default"], "ATOMIC_REQUESTS": False}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

S3_ENDPOINT_URL = os.environ["S3_ENDPOINT_URL"]
S3_BUCKET = os.environ["S3_BUCKET"]
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]

# Only the React app's origin may call the API from a browser.
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
CORS_ALLOW_HEADERS = (*default_headers, "x-tenant-id")

# Wraps every Tenant key (ADR 0001). Read from a file (a compose secret) kept outside the repo, every
# volume and every Backup. Only the backend gets it; unset elsewhere on purpose.
_master_key_file = os.environ.get("MASTER_KEY_FILE")
MASTER_KEY = base64.b64decode(Path(_master_key_file).read_text().strip()) if _master_key_file else None
if MASTER_KEY is not None and len(MASTER_KEY) != 32:
    raise ImproperlyConfigured("The master key file must hold 32 bytes, base64-encoded")
