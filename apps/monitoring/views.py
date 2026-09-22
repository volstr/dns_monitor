"""Проба работоспособности — ТЗ 11.13, Q-E4.

Единственный экран этапа 4. Остальной интерфейс приходит на этапе 6.

Обработчик делает ровно то, что ему разрешено: зовёт одну функцию сервиса и отдаёт результат
(`CLAUDE.md`). Доменных ветвлений здесь нет — решение о здоровье принимает `check_health`.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse

from apps.monitoring.services.health import check_health
from core.adapters.redis_state import RedisStateStore

HEALTHY = 200
UNHEALTHY = 503


def healthcheck(request: HttpRequest) -> JsonResponse:
    """Проба называет причину, а не отвечает «что-то пошло не так» (11.13)."""
    from django.conf import settings

    store = RedisStateStore.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)
    report = check_health(state_store=store)

    return JsonResponse(
        {
            "healthy": report.healthy,
            "failed_component": report.failed_component,
            "detail": report.detail,
        },
        status=HEALTHY if report.healthy else UNHEALTHY,
    )
