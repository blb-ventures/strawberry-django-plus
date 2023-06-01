import os
import pathlib

from django.db import models
from django.db.models.manager import BaseManager
from django.db.models.query import QuerySet

for cls in [QuerySet, BaseManager, models.ForeignKey, models.ManyToManyField]:
    if not hasattr(cls, "__class_getitem__"):
        cls.__class_getitem__ = classmethod(lambda cls, *args, **kwargs: cls)  # type: ignore

_DIR = pathlib.Path(__file__).parent.absolute()

DEBUG = True

INTERNAL_IPS = [
    "127.0.0.1",
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "guardian",
    "django_extensions",
    "debug_toolbar",
    "strawberry.django",
    "strawberry_django_plus",
    "demo",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_DIR / "db.sqlite3") if os.environ.get("_PERSISTENT_DB") else ":memory:",
    },
}

ANONYMOUS_USER_NAME = None

AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
)

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "strawberry_django_plus.middlewares.user_warmup.user_warmup_middleware",
    "strawberry_django_plus.middlewares.debug_toolbar.DebugToolbarMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

STATIC_URL = "/static/"
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(message)s"},
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "django.db.backends": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "strawberry.execution": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}


STRAWBERRY_DJANGO_RELAY_MAX_RESULTS = 100

SECRET_KEY = "dummy"

ROOT_URLCONF = "demo.urls"
