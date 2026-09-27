"""
Запасной пояс, когда `APP_TIME_ZONE` не задан — ТЗ 4.0, Q-A3.

Порядок: `APP_TIME_ZONE`, затем `TZ`, затем `/etc/localtime`, затем UTC. Смысл — чтобы установка
вне контейнера показывала местное время без лишней настройки.
"""

from pathlib import Path

import pytest

from config import env as env_module
from config.env import parse_env

REQUIRED: dict[str, str] = {
    "DATABASE_URL": "postgresql://dns_monitoring:secret@postgres:5432/dns_monitoring",
    "REDIS_URL": "redis://redis:6379",
    "APP_BASE_URL": "https://dns-monitoring.example.com",
    "SESSION_SECRET": "test-session-secret-" + "0123456789" * 4,
}
MOSCOW = "Europe/Moscow"


def zone_of(source: dict[str, str]) -> str:
    result = parse_env(source)
    assert result.env is not None, result.errors
    return result.env.app_time_zone


def test_explicit_variable_wins_over_the_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_module, "LOCALTIME_PATH", Path("/usr/share/zoneinfo/Asia/Tokyo"))

    assert zone_of(REQUIRED | {"APP_TIME_ZONE": MOSCOW, "TZ": "Asia/Tokyo"}) == MOSCOW


def test_tz_is_used_when_the_explicit_one_is_absent() -> None:
    assert zone_of(REQUIRED | {"TZ": MOSCOW}) == MOSCOW


def test_localtime_is_used_when_no_variables_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Так пояс хранит любой дистрибутив Linux: ссылка на файл в zoneinfo."""
    monkeypatch.setattr(env_module, "LOCALTIME_PATH", Path("/usr/share/zoneinfo/Europe/Moscow"))

    assert zone_of(REQUIRED) == MOSCOW


def test_utc_when_nothing_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_module, "LOCALTIME_PATH", Path("/nonexistent/localtime"))

    assert zone_of(REQUIRED) == "UTC"


def test_broken_tz_does_not_stop_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    `TZ` — чужая переменная и догадка, а не требование: падать из-за неё неправильно.

    В отличие от `APP_TIME_ZONE`, где опечатка роняет запуск: её задавали осознанно.
    """
    monkeypatch.setattr(env_module, "LOCALTIME_PATH", Path("/nonexistent/localtime"))

    assert zone_of(REQUIRED | {"TZ": "Europe/Moskva"}) == "UTC"


def test_broken_explicit_variable_still_stops_the_start() -> None:
    result = parse_env(REQUIRED | {"APP_TIME_ZONE": "Europe/Moskva"})

    assert result.env is None
