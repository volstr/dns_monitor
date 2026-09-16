"""Настройки проекта — ТЗ 4.0, 11.10, Q-A3.

Режимы `dev` и `prod` проверяются в отдельном процессе: настройки читают окружение при импорте,
и перезагрузка модуля в текущем процессе испортила бы настройки самого прогона тестов.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(__file__).resolve().parents[2]

PROD_ENV = {
    "DATABASE_URL": "postgresql://dns_monitoring:secret@postgres:5432/dns_monitoring",
    "REDIS_URL": "redis://redis:6379",
    "APP_BASE_URL": "https://dns-monitoring.example.com",
    "SESSION_SECRET": "prod-session-secret-" + "0123456789" * 4,
}


def settings_of(module: str, env: dict[str, str], *names: str) -> dict[str, object]:
    """Значения настроек, как их видит отдельно запущенный процесс с этим окружением."""
    code = (
        "import json, django;"
        "django.setup();"
        "from django.conf import settings;"
        f"print(json.dumps({{name: getattr(settings, name, None) for name in {names!r}}}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**env, "PATH": os.environ.get("PATH", ""), "DJANGO_SETTINGS_MODULE": module},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    values: dict[str, object] = json.loads(completed.stdout)
    return values


def test_4_0_primary_keys_are_bigint() -> None:
    assert settings.DEFAULT_AUTO_FIELD == "django.db.models.BigAutoField"


def test_4_0_time_is_stored_in_utc() -> None:
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"


def test_interface_language_is_russian() -> None:
    assert settings.LANGUAGE_CODE == "ru"


def test_a3_allowed_hosts_follow_app_base_url() -> None:
    values = settings_of(
        "config.settings.prod", PROD_ENV, "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "DEBUG"
    )

    assert values["ALLOWED_HOSTS"] == ["dns-monitoring.example.com"]
    assert values["CSRF_TRUSTED_ORIGINS"] == ["https://dns-monitoring.example.com"]
    assert values["DEBUG"] is False


def test_dev_adds_localhost_to_allowed_hosts() -> None:
    env = PROD_ENV | {"APP_BASE_URL": "http://localhost:8000"}

    values = settings_of("config.settings.dev", env, "ALLOWED_HOSTS", "DEBUG")

    assert values["DEBUG"] is True
    assert set(values["ALLOWED_HOSTS"]) == {"localhost", "127.0.0.1"}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("base_url", "secure"),
    [("https://dns-monitoring.example.com", True), ("http://dns-monitoring.example.com", False)],
)
def test_prod_secure_cookies_follow_scheme(base_url: str, secure: bool) -> None:
    env = PROD_ENV | {"APP_BASE_URL": base_url}

    values = settings_of(
        "config.settings.prod", env, "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"
    )

    assert values["SESSION_COOKIE_SECURE"] is secure
    assert values["CSRF_COOKIE_SECURE"] is secure
