"""Проба работоспособности и ответ 503 — ТЗ 11.13, Q-E4, критерий 64."""

import dataclasses
from datetime import UTC, datetime

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client, override_settings

from config.middleware import HEALTH_TTL, InfrastructureHealthMiddleware

pytestmark = pytest.mark.django_db

HEALTHY = 200
UNHEALTHY = 503
#: Порт, на котором заведомо никто не слушает: имитация недоступного Redis.
DEAD_REDIS = "redis://127.0.0.1:6399"
MOMENT = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)


def broken_env() -> object:
    return dataclasses.replace(settings.ENV, redis_url=DEAD_REDIS)


def test_healthcheck_reports_a_healthy_installation(client: Client) -> None:
    response = client.get("/health/")

    assert response.status_code == HEALTHY
    assert response.json() == {"healthy": True, "failed_component": None, "detail": None}


def test_64_healthcheck_turns_unhealthy_and_names_the_component(client: Client) -> None:
    with override_settings(ENV=broken_env()):
        response = client.get("/health/")

    assert response.status_code == UNHEALTHY
    body = response.json()
    assert body["healthy"] is False
    assert body["failed_component"] == "redis"
    # Причина названа, а не спрятана за «что-то пошло не так».
    assert body["detail"]


def test_64_interface_answers_503_when_infrastructure_is_down(client: Client) -> None:
    with override_settings(ENV=broken_env()):
        response = client.get("/any-screen/")

    assert response.status_code == UNHEALTHY
    page = response.content.decode()
    assert "redis" in page
    assert "Проверки доменов остановлены" in page


def test_11_13_healthcheck_itself_is_not_blocked_by_the_middleware(client: Client) -> None:
    # Иначе проба отвечала бы 503 вместо того, чтобы сказать, что именно сломалось.
    with override_settings(ENV=broken_env()):
        response = client.get("/health/")

    assert response["Content-Type"] == "application/json"


def test_healthy_installation_serves_normal_responses(client: Client) -> None:
    # Несуществующий адрес обязан давать 404, а не 503: инфраструктура жива.
    response = client.get("/any-screen/")

    assert response.status_code == 404


def probe() -> InfrastructureHealthMiddleware:
    return InfrastructureHealthMiddleware(lambda request: HttpResponse("экран"))


def test_11_12_health_is_not_probed_on_every_request() -> None:
    """
    11.12: состояние кэшируется, иначе на каждый запрос приходится `PING` к Redis.

    Проверяется через наблюдаемое: Redis «чинится» между двумя вызовами, но ответ в пределах
    срока кэша прежний — значит, второй пробы не было.
    """
    middleware = probe()

    with override_settings(ENV=broken_env()):
        first = middleware.health_at(MOMENT)
    second = middleware.health_at(MOMENT + HEALTH_TTL / 2)

    assert first.healthy is False
    assert second.healthy is False


def test_11_12_stale_report_is_probed_again() -> None:
    """Кэш обязан быть коротким: иначе восстановленный Redis не заметили бы вовсе."""
    middleware = probe()

    with override_settings(ENV=broken_env()):
        assert middleware.health_at(MOMENT).healthy is False

    assert middleware.health_at(MOMENT + HEALTH_TTL).healthy is True


def test_11_13_outage_is_noticed_within_the_cache_lifetime() -> None:
    """Обратная сторона: цена кэша — задержка, и она обязана быть ограниченной."""
    middleware = probe()

    assert middleware.health_at(MOMENT).healthy is True
    with override_settings(ENV=broken_env()):
        assert middleware.health_at(MOMENT + HEALTH_TTL).healthy is False
