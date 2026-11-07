"""Back office for Platform operators: Django admin only, as the back-office DB role.

It runs as its own service with its own secret key and gets NO master key and NO S3 credentials, so nothing
in this process can read or decrypt a stored file. (settings.py insists the S3 names exist; here they are
empty on purpose.)
"""
import os

for _name in ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
    os.environ.setdefault(_name, "")

from .settings import *  # noqa: E402,F401,F403
from .settings import BASE_DIR  # noqa: E402

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "files",
    "audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.backoffice_urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
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

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Cookies are shared by every port of the same host, so give these their own names.
SESSION_COOKIE_NAME = "backoffice_sessionid"
CSRF_COOKIE_NAME = "backoffice_csrftoken"
SESSION_COOKIE_AGE = 8 * 60 * 60
