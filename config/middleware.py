"""Ответ 503 при аварии инфраструктуры — ТЗ 11.13.

«Интерфейс и API отвечают `503` со страницей, прямо называющей отказавший компонент. Не „что-то
пошло не так“.»

Сама проба `/health/` через эту проверку не проходит: иначе она отвечала бы 503 вместо того,
чтобы сказать, что именно сломалось.

Цена решения названа прямо: на каждый запрос приходится один `PING` к Redis. На масштабе 11.2
(интерфейсом пользуются единицы администраторов) это несопоставимо дешевле, чем показать рабочий
интерфейс поверх остановленных проверок. TODO(ТЗ 11.12): кэшировать состояние на секунду-две —
этап 8, вместе с остальной обвязкой.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.monitoring.services.health import check_health
from core.adapters.redis_state import RedisStateStore

HEALTH_PATH = "/health/"
UNHEALTHY = 503


class InfrastructureHealthMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._store: RedisStateStore | None = None
        self._url: str | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith(HEALTH_PATH):
            return self.get_response(request)

        report = check_health(state_store=self._state_store())
        if report.healthy:
            return self.get_response(request)

        return render(
            request,
            "503.html",
            {"component": report.failed_component},
            status=UNHEALTHY,
        )

    def _state_store(self) -> RedisStateStore:
        """Клиент переживает процесс, но пересоздаётся, если адрес подключения сменился."""
        url = settings.ENV.redis_url
        if self._store is None or self._url != url:
            self._store = RedisStateStore.from_url(url, db=settings.ENV.redis_db_state)
            self._url = url
        return self._store
