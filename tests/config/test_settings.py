"""
Настройки проекта — ТЗ 4.0, 11.10, Q-A3.

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
    "ALLOWED_HOSTS": "dns-monitoring.example.com",
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
    """4.0: хранение в UTC. За это отвечает USE_TZ, и он не зависит от пояса показа."""
    assert settings.USE_TZ is True


def test_4_0_display_time_zone_comes_from_the_environment() -> None:
    """4.0: отображение — в часовом поясе установки, а он приходит из окружения (Q-A3)."""
    assert settings.ENV.app_time_zone == settings.TIME_ZONE


def test_interface_language_is_russian() -> None:
    assert settings.LANGUAGE_CODE == "ru"


def test_allowed_hosts_come_from_their_own_variable() -> None:
    """
    Имена задаются `ALLOWED_HOSTS` и только ею — из `APP_BASE_URL` они больше не выводятся.

    Это разные величины: `APP_BASE_URL` — адрес, по которому установку открывает человек и
    который уходит в ссылки уведомлений (8.4); отвечать приложение может ещё и по внутреннему
    имени или за обратным прокси.
    """
    env = PROD_ENV | {"ALLOWED_HOSTS": "dns.example.com, 10.20.0.7, .internal"}

    values = settings_of(
        "config.settings.prod", env, "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "DEBUG"
    )

    assert values["ALLOWED_HOSTS"] == ["dns.example.com", "10.20.0.7", ".internal"]
    # Адрес из ссылок остаётся своим: его в список имён никто не переносит.
    assert values["CSRF_TRUSTED_ORIGINS"] == ["https://dns-monitoring.example.com"]
    assert values["DEBUG"] is False


def test_prod_refuses_to_start_without_allowed_hosts() -> None:
    """
    Пустой список в бою — не мелочь: Django отверг бы тогда любой запрос.

    Приложение выглядело бы сломанным, не называя причины, поэтому оно не стартует вовсе —
    как и при любой другой недостающей обязательной переменной (11.10).
    """
    env = {name: value for name, value in PROD_ENV.items() if name != "ALLOWED_HOSTS"}

    with pytest.raises(AssertionError) as failure:
        settings_of("config.settings.prod", env, "ALLOWED_HOSTS")

    assert "ALLOWED_HOSTS" in str(failure.value)


def test_dev_does_not_require_allowed_hosts() -> None:
    """В режиме разработчика переменную задавать необязательно: локальные имена добавляются сами."""
    env = {name: value for name, value in PROD_ENV.items() if name != "ALLOWED_HOSTS"}

    values = settings_of("config.settings.dev", env, "ALLOWED_HOSTS", "DEBUG")

    assert values["DEBUG"] is True
    assert set(values["ALLOWED_HOSTS"]) == {"localhost", "127.0.0.1", "[::1]"}  # type: ignore[arg-type]


def test_dev_keeps_the_names_that_were_given() -> None:
    """Установку разработчика открывают и по имени машины в сети — заданное не теряется."""
    env = PROD_ENV | {"ALLOWED_HOSTS": "dev-box.local"}

    values = settings_of("config.settings.dev", env, "ALLOWED_HOSTS")

    assert "dev-box.local" in values["ALLOWED_HOSTS"]  # type: ignore[operator]


@pytest.mark.parametrize(
    ("app_base_url", "secure"),
    [("https://dns-monitoring.example.com", True), ("http://dns-monitoring.example.com", False)],
)
def test_prod_secure_cookies_follow_scheme(app_base_url: str, secure: bool) -> None:
    env = PROD_ENV | {"APP_BASE_URL": app_base_url}

    values = settings_of(
        "config.settings.prod", env, "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"
    )

    assert values["SESSION_COOKIE_SECURE"] is secure
    assert values["CSRF_COOKIE_SECURE"] is secure
