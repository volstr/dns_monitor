"""
Часовой пояс отображения — ТЗ 4.0, решение Q-A3.

«Метки времени — тип с часовым поясом, хранение в UTC, **отображение в часовом поясе
установки**». Хранение обеспечивает `USE_TZ`, отображение — `TIME_ZONE`, и приходит он из
`APP_TIME_ZONE`.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain
from apps.monitoring.models import Event, EventType
from config.env import parse_env

pytestmark = pytest.mark.django_db

# 14:38 UTC — это 17:38 в Москве. Разница нужна, чтобы подмена пояса была видна.
MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)
MOSCOW = "Europe/Moscow"

REQUIRED: dict[str, str] = {
    "DATABASE_URL": "postgresql://dns_monitoring:secret@postgres:5432/dns_monitoring",
    "REDIS_URL": "redis://redis:6379",
    "APP_BASE_URL": "https://dns-monitoring.example.com",
    "SESSION_SECRET": "test-session-secret-" + "0123456789" * 4,
}


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


def test_4_0_default_time_zone_is_utc() -> None:
    """Пояс не задан — показываем UTC. Молча подставлять чужой пояс неправильно."""
    result = parse_env(REQUIRED)

    assert result.env is not None
    assert result.env.app_time_zone == "UTC"


def test_4_0_time_zone_comes_from_the_environment() -> None:
    result = parse_env(REQUIRED | {"APP_TIME_ZONE": MOSCOW})

    assert result.env is not None
    assert result.env.app_time_zone == MOSCOW


def test_4_0_unknown_time_zone_stops_the_start() -> None:
    """Опечатка вроде «Europe/Moskva» обязана падать при старте, а не при отрисовке страницы."""
    result = parse_env(REQUIRED | {"APP_TIME_ZONE": "Europe/Moskva"})

    assert result.env is None
    assert any(error.variable == "APP_TIME_ZONE" for error in result.errors)
    assert "неизвестный часовой пояс" in result.errors[0].reason


@override_settings(TIME_ZONE=MOSCOW)
def test_4_0_journal_shows_the_installation_time(client: Client, admin: Member) -> None:
    domain = Domain.objects.create(name="google.com")
    Event.objects.create(domain=domain, event_type=EventType.NOT_ALLOWED, occurred_at=MOMENT)
    client.force_login(admin)

    body = client.get(reverse("event-list")).content.decode()

    assert "17:38" in body
    assert "14:38" not in body


@override_settings(TIME_ZONE=MOSCOW)
def test_4_0_export_carries_the_offset(client: Client, admin: Member) -> None:
    """11.7 требует ISO 8601; смещение в нём делает момент однозначным."""
    domain = Domain.objects.create(name="google.com")
    Event.objects.create(domain=domain, event_type=EventType.NOT_ALLOWED, occurred_at=MOMENT)
    client.force_login(admin)

    body = client.get(reverse("event-export")).content.decode("utf-8")

    assert "2026-09-26T17:38:00+03:00" in body


@override_settings(TIME_ZONE=MOSCOW)
def test_4_0_card_headline_shows_the_installation_time(client: Client, admin: Member) -> None:
    """Подзаголовок собирается в Python и мимо перевода, который Django делает в шаблоне."""
    domain = Domain.objects.create(name="google.com", last_successful_check_at=MOMENT)
    client.force_login(admin)

    body = client.get(reverse("domain-detail", args=[domain.pk])).content.decode()

    assert "последний ответ в 17:38" in body


def test_4_0_storage_stays_in_utc() -> None:
    """Пояс меняет только показ: в базе по-прежнему UTC (4.0)."""
    domain = Domain.objects.create(name="google.com", last_successful_check_at=MOMENT)

    domain.refresh_from_db()

    assert domain.last_successful_check_at is not None
    assert domain.last_successful_check_at.astimezone(UTC).hour == 14
