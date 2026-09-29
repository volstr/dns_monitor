"""
Экран общих настроек — ТЗ 4.1, 8.3, 8.4; решения Q-C4, Q-D19, Q-D11.

Значения отдаются в тех единицах, в которых их вводит человек: период в минутах, пороги в часах,
хранение в днях. Пересчёт живёт здесь и в форме, а не в шаблоне — вычислений в разметке быть не
должно (`CLAUDE.md`).

Наблюдателю экран доступен на чтение, но без блока адресатов (Q-D19): разбор инцидента без режима
уведомлений и порогов невозможен, а «кому отправлять» ему знать незачем.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from apps.catalog.models import GlobalSettings, NotificationMode
from core.text import humanize_duration

MINUTE = 60
HOUR = 60 * 60
DAY = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class ModeChoice:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class SettingsPage:
    """Текущие значения общих настроек в единицах ввода."""

    check_period_minutes: int
    freshness_threshold_hours: int
    #: То же значение словами: «14 суток». Часы читабельны до сотни, а порог по умолчанию
    #: теперь 336 — число, в котором суток не видно (Q-D47). Считается здесь, а не в шаблоне:
    #: вычислений в шаблоне быть не должно.
    freshness_threshold_text: str
    modes: tuple[ModeChoice, ...]
    notify_back_to_normal: bool
    reminder_interval_hours: int
    merge_window_seconds: int
    merge_threshold: int
    dns_servers_text: str
    check_ipv6: bool
    resolve_failure_threshold: int
    event_retention_days: int
    registry_retention_days: int | None
    message_include_full_set: bool
    message_include_comment: bool
    message_include_link: bool
    session_lifetime_hours: int
    login_failure_threshold: int
    telegram_chat_id: str
    email_recipients_text: str
    webhook_url: str


def settings_page() -> SettingsPage:
    """Одна строка на установку (4.1). Её создаёт миграция, поэтому она всегда есть."""
    row = GlobalSettings.objects.get()

    return SettingsPage(
        check_period_minutes=row.check_period_seconds // MINUTE,
        freshness_threshold_hours=row.freshness_threshold_seconds // HOUR,
        freshness_threshold_text=humanize_duration(
            timedelta(seconds=row.freshness_threshold_seconds)
        ),
        modes=tuple(
            ModeChoice(
                value=mode.value,
                label=str(mode.label),
                selected=row.notification_mode == mode.value,
            )
            for mode in NotificationMode
        ),
        notify_back_to_normal=row.notify_back_to_normal,
        # Ноль — «не напоминать» (8.5), и он должен пережить пересчёт в часы.
        reminder_interval_hours=row.reminder_interval_seconds // HOUR,
        merge_window_seconds=row.merge_window_seconds,
        merge_threshold=row.merge_threshold,
        dns_servers_text="\n".join(row.dns_servers),
        check_ipv6=row.check_ipv6,
        resolve_failure_threshold=row.resolve_failure_threshold,
        event_retention_days=row.event_retention_seconds // DAY,
        registry_retention_days=(
            row.registry_retention_seconds // DAY
            if row.registry_retention_seconds is not None
            else None
        ),
        message_include_full_set=row.message_include_full_set,
        message_include_comment=row.message_include_comment,
        message_include_link=row.message_include_link,
        session_lifetime_hours=row.session_lifetime_seconds // HOUR,
        login_failure_threshold=row.login_failure_threshold,
        telegram_chat_id=row.telegram_chat_id,
        email_recipients_text="\n".join(row.email_recipients),
        webhook_url=row.webhook_url,
    )
