import os
import sys
from pathlib import Path
from environs import Env

BASE_DIR = Path(__file__).resolve().parent.parent
env = Env()
env.read_env(BASE_DIR / ".env")

# One dependency-free wire contract, shared with the existing client.
sys.path.insert(0, str(BASE_DIR.parent))
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env.str("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "rest_framework",
                  "accounts", "devices", "synchronization"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "config.middleware.SafeBoundary", "django.middleware.csrf.CsrfViewMiddleware",
              "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": env.str("PGDATABASE", "cmdvault"),
    "USER": env.str("PGUSER", "cmdvault"),
    "PASSWORD": env.str("PGPASSWORD", ""),
    "HOST": env.str("PGHOST", "127.0.0.1"),
    "PORT": env.int("PGPORT", "5432"),
    "CONN_MAX_AGE": 0,
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher",
                    "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 14}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["devices.authentication.DeviceAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "config.middleware.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DEVICE_TOKEN_DAYS = int(os.getenv("DEVICE_TOKEN_DAYS", "90"))
LOGIN_LIMIT = int(os.getenv("LOGIN_LIMIT", "10"))
LOGIN_WINDOW_SECONDS = 300
if DEVICE_TOKEN_DAYS < 1 or LOGIN_LIMIT < 1:
    raise ValueError("DEVICE_TOKEN_DAYS and LOGIN_LIMIT must be positive")
# Only synthetic encrypted data until client encryption/recovery/filtering is ready.
SYNC_ENABLED = env.bool("SYNC_ENABLED", False) == True
TRUST_PROXY = env.bool("TRUST_PROXY", default=False)

if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

SECURE_SSL_REDIRECT = env.bool(
    "SECURE_SSL_REDIRECT",
    default=True,
)
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"
# Never serialize request headers/bodies or exception locals into mail/log handlers.
LOGGING = {
    "version": 1, "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
    "loggers": {"django": {"handlers": ["null"], "propagate": False}},
}
