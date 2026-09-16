"""Режим автотестов: ``pytest --ds=config.settings.test``."""

from .base import *  # noqa: F403
from .base import APP_HOST

ALLOWED_HOSTS = ["testserver", APP_HOST]
