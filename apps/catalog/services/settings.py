"""
Правка общих настроек — ТЗ 4.1, 8.3, 8.4; решения Q-13.1, Q-C4, Q-D19.

Строка одна на установку (4.1), она создаётся миграцией и никогда не удаляется — поэтому здесь
только правка, без создания.

**Что сюда не попадает.** Подключения каналов — токен бота, учётные данные почтового сервера —
живут в окружении (8.3, 11.10, Q-A2) и через эту форму не проходят. Администратор приложения не
должен получать возможность направить исходящее через свой сервер. Адресаты — идентификатор чата,
перечень почтовых адресов, адрес вебхука — наоборот, задаются здесь (Q-D11).

Отказ — штатный исход с перечнем причин. Предупреждения Q-13.1 сохранению не мешают и приходят
вместе с успехом: это предупреждение, а не запрет.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from apps.catalog.models import GlobalSettings
from apps.catalog.services.intervals import warnings_after_global_change

MIN_PERIOD_SECONDS = 60


@dataclass(frozen=True, slots=True)
class SettingsInput:
    """Значения формы общих настроек. Отдельным типом, чтобы не передавать двадцать аргументов."""

    check_period_seconds: int
    freshness_threshold_seconds: int
    notification_mode: str
    notify_back_to_normal: bool
    reminder_interval_seconds: int
    merge_window_seconds: int
    merge_threshold: int
    dns_servers: tuple[str, ...]
    #: Q-D27: снятый флаг означает «адресов IPv6 не существует».
    check_ipv6: bool
    resolve_failure_threshold: int
    event_retention_seconds: int
    registry_retention_seconds: int | None
    message_include_full_set: bool
    message_include_comment: bool
    message_include_link: bool
    session_lifetime_seconds: int
    login_failure_threshold: int
    telegram_chat_id: str
    email_recipients: tuple[str, ...]
    webhook_url: str


@dataclass(frozen=True, slots=True)
class SettingsSaved:
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SettingsNotSaved:
    reasons: tuple[str, ...]


def save_global_settings(values: SettingsInput) -> SettingsSaved | SettingsNotSaved:
    """Сохранить общие настройки."""
    reasons = _reasons(values)
    if reasons:
        return SettingsNotSaved(reasons=reasons)

    with transaction.atomic():
        GlobalSettings.objects.filter(pk=1).update(
            check_period_seconds=values.check_period_seconds,
            freshness_threshold_seconds=values.freshness_threshold_seconds,
            notification_mode=values.notification_mode,
            notify_back_to_normal=values.notify_back_to_normal,
            reminder_interval_seconds=values.reminder_interval_seconds,
            merge_window_seconds=values.merge_window_seconds,
            merge_threshold=values.merge_threshold,
            dns_servers=list(values.dns_servers),
            check_ipv6=values.check_ipv6,
            resolve_failure_threshold=values.resolve_failure_threshold,
            event_retention_seconds=values.event_retention_seconds,
            registry_retention_seconds=values.registry_retention_seconds,
            message_include_full_set=values.message_include_full_set,
            message_include_comment=values.message_include_comment,
            message_include_link=values.message_include_link,
            session_lifetime_seconds=values.session_lifetime_seconds,
            login_failure_threshold=values.login_failure_threshold,
            telegram_chat_id=values.telegram_chat_id,
            email_recipients=list(values.email_recipients),
            webhook_url=values.webhook_url,
        )

    # Правка общих значений могла сломать группы, которые унаследовали одно и задали другое
    # (решение Q-13.1). Сломавшиеся называются поимённо.
    return SettingsSaved(warnings=warnings_after_global_change(GlobalSettings.objects.get()))


def _reasons(values: SettingsInput) -> tuple[str, ...]:
    collected: list[str] = []

    if values.check_period_seconds < MIN_PERIOD_SECONDS:
        collected.append("Период проверки не может быть меньше минуты (ТЗ 11.1).")
    if values.freshness_threshold_seconds <= 0:
        collected.append("Порог актуальности должен быть больше нуля.")
    if values.merge_window_seconds <= 0:
        collected.append("Окно слияния должно быть больше нуля.")
    if values.merge_threshold < 1:
        collected.append("Порог слияния — не меньше одного события.")
    if values.resolve_failure_threshold < 1:
        collected.append("Повторов при ошибке резолва — не меньше одного.")
    if values.event_retention_seconds <= 0:
        collected.append("Срок хранения журнала должен быть больше нуля.")
    if values.registry_retention_seconds is not None and values.registry_retention_seconds <= 0:
        collected.append("Срок хранения реестра либо не задан, либо больше нуля.")
    if values.session_lifetime_seconds <= 0:
        collected.append("Срок сессии должен быть больше нуля.")
    if values.login_failure_threshold < 1:
        collected.append("Порог блокировки входа — не меньше одной попытки.")

    collected.extend(_dns_reasons(values.dns_servers))
    collected.extend(_email_reasons(values.email_recipients))
    return tuple(collected)


def _dns_reasons(servers: tuple[str, ...]) -> tuple[str, ...]:
    """Список DNS-серверов упорядочен и непуст (4.1): без него резолвить нечем."""
    if not servers:
        return ("Нужен хотя бы один DNS-сервер: без него проверять нечем.",)
    for server in servers:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            return (f"«{server}» — не похоже на адрес DNS-сервера. Нужен IP-адрес.",)
    return ()


def _email_reasons(recipients: tuple[str, ...]) -> tuple[str, ...]:
    for recipient in recipients:
        try:
            validate_email(recipient)
        except ValidationError:
            return (f"«{recipient}» — не похоже на почтовый адрес.",)
    return ()
