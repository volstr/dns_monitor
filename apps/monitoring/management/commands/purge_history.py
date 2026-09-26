"""Уборка по срокам хранения вручную — ТЗ 11.3.

Обычно уборка идёт сама, внутри `run_checker`, раз в час. Эта команда нужна для двух случаев:
проверить срок сразу после того, как его поменяли в настройках, и убрать накопившееся на
установке, где планировщик долго не работал.

Команда разбирает аргументы и зовёт сервис, логики не содержит (`CLAUDE.md`).
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.monitoring.services.retention import purge_history
from core.adapters.system_clock import SystemClock


class Command(BaseCommand):
    help = "Удалить журнал и реестр, переживших свой срок хранения (ТЗ 11.3)"

    def handle(self, *args: Any, **options: Any) -> None:
        purged = purge_history(at=SystemClock().now())
        self.stdout.write(
            f"Удалено: события {purged.events}, действия {purged.audit_records}, "
            f"реестр {purged.registry_entries}, корзины "
            f"{purged.hourly_buckets + purged.daily_buckets}, "
            f"очередь {purged.notifications}."
        )
