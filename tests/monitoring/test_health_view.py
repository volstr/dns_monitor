"""Проба работоспособности и ответ 503 — ТЗ 11.13, Q-E4, критерий 64."""

import dataclasses

import pytest
from django.conf import settings
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db

HEALTHY = 200
UNHEALTHY = 503
#: Порт, на котором заведомо никто не слушает: имитация недоступного Redis.
DEAD_REDIS = "redis://127.0.0.1:6399"


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
