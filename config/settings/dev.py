"""Режим разработчика: ``docker-compose-dev.yml``."""

from .base import *  # noqa: F403
from .base import APP_HOST

DEBUG = True
ALLOWED_HOSTS = [APP_HOST, "localhost", "127.0.0.1"]
