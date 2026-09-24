"""Создать первого администратора установки — ТЗ 3.

Команда разбирает аргументы и зовёт сервис, логики не содержит (`CLAUDE.md`).

Пароль читается из окружения или запрашивается скрытым вводом: передавать его аргументом значит
оставить его в истории команд и в списке процессов.
"""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.services.bootstrap import AdminNotCreated, create_first_admin

PASSWORD_VARIABLE = "ADMIN_PASSWORD"  # noqa: S105 — имя переменной, а не значение


class Command(BaseCommand):
    help = "Создать первого администратора. Работает только на установке без администраторов."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        password = os.environ.get(PASSWORD_VARIABLE) or getpass.getpass("Пароль: ")

        result = create_first_admin(
            email=options["email"],
            name=options["name"],
            password=password,
            at=datetime.now(UTC),
        )

        if isinstance(result, AdminNotCreated):
            raise CommandError(result.reason)

        self.stdout.write(f"Администратор {result.email} создан. Можно входить.")
