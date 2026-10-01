"""Django settings for the Fuel Route Optimizer API.

This is an API-only project: no database models, no authentication, no admin.
External services (OSRM routing, Photon geocoding) require no API keys; base
URLs and file paths can be overridden through environment variables.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-not-for-production")

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "route_api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CSRF is enforced by DRF when session auth is used; the middleware is
    # included for standard hardening (DRF views are csrf_exempt by design).
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# No application models or migrations exist; an in-memory database satisfies
# Django's test runner without introducing any persistence.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

# --- Fuel Route Optimizer configuration -------------------------------------

FUEL_CSV_PATH = Path(os.environ.get("FUEL_CSV_PATH", BASE_DIR / "fuel-prices-for-be-assessment.csv"))
CITY_COORDS_PATH = Path(os.environ.get("CITY_COORDS_PATH", BASE_DIR / "data" / "city_coordinates.csv"))

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
PHOTON_BASE_URL = os.environ.get("PHOTON_BASE_URL", "https://photon.komoot.io").rstrip("/")

# Stations within this many miles (perpendicular) of the route are candidates.
# 25 miles accommodates the city-level precision of GeoNames coordinates.
ROUTE_CORRIDOR_MILES = float(os.environ.get("ROUTE_CORRIDOR_MILES", "25"))

# Vehicle constraints.
VEHICLE_MAX_RANGE_MILES = 500.0
VEHICLE_MPG = 10.0

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "loggers": {
        "route_api": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
