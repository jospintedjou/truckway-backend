"""Django settings. Everything environment-specific comes from environment variables (.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
    "trips",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# The app is stateless: no database. Trips and forecasts live in the cache.
DATABASES = {}

REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_RATES": {
        "ip": os.environ.get("THROTTLE_IP", "120/min"),
        "plan": os.environ.get("THROTTLE_PLAN", "20/min"),
    },
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# ---- Third-party providers ----
TOMTOM_API_KEY = os.environ.get("TOMTOM_API_KEY", "")
TOMTOM_BASE_URL = os.environ.get("TOMTOM_BASE_URL", "https://api.tomtom.com")
OPEN_METEO_URL = os.environ.get("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_API_KEY = os.environ.get("OPEN_METEO_API_KEY", "")  # only for the paid customer endpoint
GEOCODE_COUNTRIES = os.environ.get("GEOCODE_COUNTRIES", "US")

# ---- Domain settings ----
TRUCK_TARE_LB = float(os.environ.get("TRUCK_TARE_LB", "34000"))  # empty tractor + trailer, added to load for routing
MAX_DEPARTURE_HOURS = int(os.environ.get("MAX_DEPARTURE_HOURS", "72"))
FORECAST_DAYS = int(os.environ.get("FORECAST_DAYS", "7"))
CORRIDOR_RADIUS_MI = float(os.environ.get("CORRIDOR_RADIUS_MI", "30"))
ENABLE_SCENARIOS = env_bool("ENABLE_SCENARIOS", True)
