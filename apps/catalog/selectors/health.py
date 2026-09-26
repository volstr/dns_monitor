"""Предупреждения об установке — ТЗ 11.13, 4.1.

Есть состояния, в которых продукт цел, но не делает своего дела. Самое грубое — пустой список
DNS-серверов: спрашивать некого, и `run_checker` каждые несколько секунд отказывается работать,
честно объясняя причину **в журнале процесса**. Человек за экраном при этом видит пустой реестр
и ничего больше.

Поэтому такие состояния поднимаются в интерфейс и показываются на каждом экране. Это не то же
самое, что авария по 11.13: база и Redis живы, отвечать `503` не за что — просто установка не
настроена до конца, и сказать об этом нужно словами.

**Сюда не попадает ненастроенный канал доставки.** Пустой `telegram_chat_id` — нормальный режим
работы по 8.3, а не недоделка: домены проверяются, журнал пишется, статусы видны.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.catalog.models import Domain, GlobalSettings


@dataclass(frozen=True, slots=True)
class InstallWarning:
    """Предупреждение с прямым указанием, что сделать и где."""

    text: str
    action: str
    url_name: str


def install_warnings() -> tuple[InstallWarning, ...]:
    """Что мешает установке делать своё дело прямо сейчас."""
    settings_row = GlobalSettings.objects.get()
    collected: list[InstallWarning] = []

    if not settings_row.dns_servers:
        collected.append(
            InstallWarning(
                text=(
                    "Список DNS-серверов пуст — проверки не идут. Спрашивать некого, поэтому "
                    "реестр адресов и журнал останутся пустыми, сколько ни ждать."
                ),
                action="Заполнить в настройках",
                url_name="settings",
            )
        )

    return tuple(collected)


def checks_are_possible() -> bool:
    """Может ли установка вообще проверять домены. Нужно карточке домена."""
    return bool(GlobalSettings.objects.get().dns_servers)


def waiting_domains() -> int:
    """Сколько доменов стоит в очереди и не дождётся проверки из-за настройки."""
    return Domain.objects.filter(watch_enabled=True, next_check_at__isnull=False).count()
