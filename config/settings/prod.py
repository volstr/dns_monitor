"""Боевой режим."""

from .base import *  # noqa: F403
from .base import ENV

DEBUG = False

if ENV.app_base_url.startswith("https://"):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
