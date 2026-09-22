"""Проба работоспособности и поведение при аварии — ТЗ 11.13, Q-E4.

Недоступность Redis — **авария**, а не режим пониженной функциональности: приложение не пытается
работать «как получится». Проверки останавливаются, потому что без взаимного исключения два
экземпляра задублируют уведомления, а без кэша настроек каждый резолв пойдёт в БД за цепочкой
наследования.

Главное требование раздела: остановка должна быть **заметной**. Инструмент мониторинга, который
молча перестал мониторить, опаснее отсутствия инструмента — люди продолжают считать, что за
доменами следят.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError, connection

from apps.accounts.models import AuditAction, AuditRecord
from core.ports.state_store import StateStore

REDIS = "redis"
DATABASE = "postgresql"


@dataclass(frozen=True, slots=True)
class HealthReport:
    healthy: bool
    #: Какой именно компонент отказал. Страница 503 обязана назвать его прямо, а не отвечать
    #: «что-то пошло не так» (11.13).
    failed_component: str | None = None
    detail: str | None = None


def check_health(*, state_store: StateStore) -> HealthReport:
    """Состояние инфраструктуры. Исключений не бросает: отказ — это результат."""
    try:
        state_store.ping()
    except Exception as error:
        # Ловим всё: клиент Redis, сеть и подделка бросают разные типы, а смысл один —
        # хранилище недоступно, и это авария по 11.13.
        return HealthReport(healthy=False, failed_component=REDIS, detail=str(error))

    try:
        connection.ensure_connection()
    except DatabaseError as error:
        return HealthReport(healthy=False, failed_component=DATABASE, detail=str(error))

    return HealthReport(healthy=True)


def record_checks_stopped(*, at: datetime, report: HealthReport) -> AuditRecord:
    """Факт остановки проверок пишется в журнал — 11.13, критерий 64.

    Записи без домена живут в `AuditRecord` (Q-D4), автора у неё нет: остановку вызвал не человек.
    """
    return AuditRecord.objects.create(
        action=AuditAction.CHECKS_STOPPED,
        occurred_at=at,
        author=None,
        author_name="",
    )
