"""
Ответ 503 при аварии инфраструктуры — ТЗ 11.13.

«Интерфейс и API отвечают `503` со страницей, прямо называющей отказавший компонент. Не „что-то
пошло не так“.»

Сама проба `/health/` через эту проверку не проходит: иначе она отвечала бы 503 вместо того,
чтобы сказать, что именно сломалось.

Состояние кэшируется на две секунды (11.12), иначе на каждый запрос приходился бы `PING` к
Redis: одна страница тянет ещё и статику, и запрос HTMX.

**Кэш здесь только в памяти процесса, и иначе быть не может.** Хранить в Redis ответ на вопрос
«жив ли Redis» бессмысленно, а `CACHES` в проекте пока и нет (11.12). У нескольких рабочих
процессов кэш свой у каждого — на двух секундах это безразлично.

Цена названа прямо: до двух секунд после отказа интерфейс ещё отвечает как исправный. Это
меньше, чем один тик планировщика, и несопоставимо дешевле, чем показывать рабочий интерфейс
поверх остановленных проверок неограниченно долго.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.monitoring.services.health import HealthReport, check_health
from core.adapters.redis_state import RedisStateStore
from core.adapters.system_clock import SystemClock

HEALTH_PATH = '/health/'
UNHEALTHY = 503

#: Сколько живёт результат пробы. 11.12 про кэш представлений говорит «отставание на секунды
#: здесь безвредно»; две секунды меньше одного тика планировщика.
HEALTH_TTL = timedelta(seconds=2)


class InfrastructureHealthMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._store: RedisStateStore | None = None
        self._url: str | None = None
        self._report: HealthReport | None = None
        self._checked_at: datetime | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith(HEALTH_PATH):
            return self.get_response(request)

        report = self.health_at(SystemClock().now())
        if report.healthy:
            return self.get_response(request)

        return render(
            request,
            '503.html',
            {'component': report.failed_component},
            status=UNHEALTHY,
        )

    def health_at(self, at: datetime) -> HealthReport:
        """
        Проба не чаще раза в `HEALTH_TTL` — ТЗ 11.12.

        Момент передаётся явно, как и везде в проекте: `timezone.now()` живёт только в адаптере
        часов, а тест иначе не мог бы перешагнуть срок кэша, не дожидаясь двух секунд.
        """
        fresh = self._checked_at is not None and at - self._checked_at < HEALTH_TTL
        if fresh and self._report is not None:
            return self._report

        self._report = check_health(state_store=self._state_store())
        self._checked_at = at
        return self._report

    def _state_store(self) -> RedisStateStore:
        """Клиент переживает процесс, но пересоздаётся, если адрес подключения сменился."""
        url = settings.ENV.redis_url
        if self._store is None or self._url != url:
            self._store = RedisStateStore.from_url(url, db=settings.ENV.redis_db_state)
            self._url = url
        return self._store
