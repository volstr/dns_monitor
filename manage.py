#!/usr/bin/env python
"""Точка входа management-команд."""

import os
import sys


def main() -> None:
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        sys.exit(
            "Приложение не запущено: не задана DJANGO_SETTINGS_MODULE "
            "(config.settings.dev или config.settings.prod)."
        )

    from config.env import EnvironmentConfigError, load_env

    # Проверка до Django: иначе ошибка конфигурации приходит вместе с трассировкой стека.
    try:
        load_env()
    except EnvironmentConfigError as error:
        sys.exit(str(error))

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
