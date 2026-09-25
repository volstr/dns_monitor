"""Формы каталога — ТЗ 8.2, 11.5.

Форма разбирает вход и ничего не решает по существу: фильтрацию делает селектор. Значения по
умолчанию задаются здесь, чтобы обработчик не ветвился (`CLAUDE.md`).
"""

from __future__ import annotations

from enum import StrEnum

from django import forms

from apps.catalog.models import (
    COMMENT_MAX_LENGTH,
    DOMAIN_NAME_MAX_LENGTH,
    GROUP_NAME_MAX_LENGTH,
    RECORD_TYPE_CHOICES,
)
from apps.catalog.selectors.domains import DomainFilter
from apps.catalog.services.settings import SettingsInput
from apps.monitoring.selectors.journal import JournalFilter, JournalPeriod

SECONDS_IN_MINUTE = 60
SECONDS_IN_HOUR = 60 * 60
SECONDS_IN_DAY = 24 * 60 * 60


class DomainFilterForm(forms.Form):
    """Фильтры списка доменов: поиск, сегмент и группа."""

    search = forms.CharField(required=False, max_length=253)
    chosen = forms.ChoiceField(
        required=False,
        choices=[(item.value, item.value) for item in DomainFilter],
    )
    group = forms.IntegerField(required=False, min_value=1)

    def selected_filter(self) -> DomainFilter:
        """Сегмент фильтра. Мусор в параметре — это «Все», а не ошибка страницы."""
        if not self.is_valid():
            return DomainFilter.ALL
        chosen = self.cleaned_data.get("chosen")
        return DomainFilter(chosen) if chosen else DomainFilter.ALL

    def search_text(self) -> str:
        return str(self.cleaned_data.get("search") or "") if self.is_valid() else ""

    def group_id(self) -> int | None:
        return self.cleaned_data.get("group") if self.is_valid() else None


class DomainCreateForm(forms.Form):
    """Добавление домена — ТЗ 10.1.

    Имя здесь не нормализуется: приведение к нижнему регистру, отбрасывание конечной точки и
    перевод в punycode — доменные правила, и живут они в сервисе. Форма проверяет только длину.
    """

    name = forms.CharField(max_length=DOMAIN_NAME_MAX_LENGTH)
    group = forms.IntegerField(required=False, min_value=1)
    comment = forms.CharField(required=False, max_length=COMMENT_MAX_LENGTH)

    def domain_name(self) -> str:
        return str(self.cleaned_data.get("name") or "")

    def group_id(self) -> int | None:
        return self.cleaned_data.get("group")

    def comment_text(self) -> str:
        return str(self.cleaned_data.get("comment") or "")


class DomainEditForm(forms.Form):
    """Правка карточки домена — ТЗ 4.3, 10.2.

    Белый список принимается текстом и здесь **не разбирается**: разбор живёт в сервисе, потому
    что его же зовут построчная проверка через HTMX и будущий импорт (10.3). Форма проверяет
    только то, что относится к самому полю ввода.
    """

    comment = forms.CharField(required=False, max_length=COMMENT_MAX_LENGTH)
    allowlist = forms.CharField(required=False, widget=forms.Textarea)
    record_types = forms.MultipleChoiceField(required=False, choices=RECORD_TYPE_CHOICES)
    watch_enabled = forms.BooleanField(required=False)

    def comment_text(self) -> str:
        return str(self.cleaned_data.get("comment") or "")

    def allowlist_text(self) -> str:
        return str(self.cleaned_data.get("allowlist") or "")

    def chosen_record_types(self) -> tuple[str, ...]:
        return tuple(self.cleaned_data.get("record_types") or ())

    def watch(self) -> bool:
        return bool(self.cleaned_data.get("watch_enabled"))


class GroupForm(forms.Form):
    """Настройка группы — ТЗ 5.

    Пустое значение выбора означает «по умолчанию», то есть `NULL` в столбце. Это **не** ноль:
    у интервала напоминаний ноль означает «не напоминать» (8.5), и сливать их нельзя.

    Своё значение вводится в минутах отдельным полем и перекрывает выбор из списка. Два поля
    вместо одного — потому что разворачивать «Своё» по клику нечем: клиентский код разрешён в
    трёх местах, и это не одно из них.
    """

    name = forms.CharField(max_length=GROUP_NAME_MAX_LENGTH)
    comment = forms.CharField(required=False, max_length=COMMENT_MAX_LENGTH)
    check_period = forms.CharField(required=False)
    check_period_minutes = forms.IntegerField(required=False, min_value=1)
    freshness_threshold = forms.CharField(required=False)
    freshness_threshold_minutes = forms.IntegerField(required=False, min_value=1)
    reminder_interval = forms.CharField(required=False)
    notification_mode = forms.CharField(required=False)
    notify_back_to_normal = forms.CharField(required=False)

    def group_name(self) -> str:
        return str(self.cleaned_data.get("name") or "")

    def comment_text(self) -> str:
        return str(self.cleaned_data.get("comment") or "")

    def period_seconds(self) -> int | None:
        return self._seconds("check_period", "check_period_minutes")

    def threshold_seconds(self) -> int | None:
        return self._seconds("freshness_threshold", "freshness_threshold_minutes")

    def reminder_seconds(self) -> int | None:
        return self._chosen_seconds("reminder_interval")

    def mode(self) -> str | None:
        return str(self.cleaned_data.get("notification_mode") or "") or None

    def notify_back(self) -> bool | None:
        """Три состояния: «по умолчанию», «да», «нет». Флажок их не выражает, поэтому список."""
        raw = str(self.cleaned_data.get("notify_back_to_normal") or "")
        if raw == "":
            return None
        return raw == "yes"

    def _seconds(self, choice_field: str, minutes_field: str) -> int | None:
        minutes = self.cleaned_data.get(minutes_field)
        if minutes:
            return int(minutes) * SECONDS_IN_MINUTE
        return self._chosen_seconds(choice_field)

    def _chosen_seconds(self, field: str) -> int | None:
        raw = str(self.cleaned_data.get(field) or "")
        return int(raw) if raw.isdigit() else None


class GlobalSettingsForm(forms.Form):
    """Общие настройки — ТЗ 4.1.

    Единицы ввода человеческие: период в минутах, пороги в часах, хранение в днях. Пересчёт в
    секунды — здесь, чтобы ни шаблон, ни сервис его не повторяли.

    Подключения каналов сюда не входят: токен бота и учётные данные почты живут в окружении
    (8.3, Q-A2). Здесь только адресаты.
    """

    check_period_minutes = forms.IntegerField(min_value=1)
    freshness_threshold_hours = forms.IntegerField(min_value=1)
    notification_mode = forms.CharField()
    notify_back_to_normal = forms.BooleanField(required=False)
    reminder_interval_hours = forms.IntegerField(min_value=0)
    merge_window_seconds = forms.IntegerField(min_value=1)
    merge_threshold = forms.IntegerField(min_value=1)
    dns_servers = forms.CharField(required=False, widget=forms.Textarea)
    resolve_failure_threshold = forms.IntegerField(min_value=1)
    event_retention_days = forms.IntegerField(min_value=1)
    registry_retention_days = forms.IntegerField(required=False, min_value=1)
    message_include_full_set = forms.BooleanField(required=False)
    message_include_comment = forms.BooleanField(required=False)
    message_include_link = forms.BooleanField(required=False)
    session_lifetime_hours = forms.IntegerField(min_value=1)
    login_failure_threshold = forms.IntegerField(min_value=1)
    telegram_chat_id = forms.CharField(required=False, max_length=64)
    email_recipients = forms.CharField(required=False, widget=forms.Textarea)
    webhook_url = forms.CharField(required=False, max_length=2048)

    def values(self) -> SettingsInput:
        """Значения формы в секундах и списках — в том виде, в каком их ждёт сервис."""
        data = self.cleaned_data
        registry_days = data.get("registry_retention_days")

        return SettingsInput(
            check_period_seconds=data["check_period_minutes"] * SECONDS_IN_MINUTE,
            freshness_threshold_seconds=data["freshness_threshold_hours"] * SECONDS_IN_HOUR,
            notification_mode=str(data["notification_mode"]),
            notify_back_to_normal=bool(data.get("notify_back_to_normal")),
            reminder_interval_seconds=data["reminder_interval_hours"] * SECONDS_IN_HOUR,
            merge_window_seconds=data["merge_window_seconds"],
            merge_threshold=data["merge_threshold"],
            dns_servers=_lines(data.get("dns_servers")),
            resolve_failure_threshold=data["resolve_failure_threshold"],
            event_retention_seconds=data["event_retention_days"] * SECONDS_IN_DAY,
            registry_retention_seconds=(
                registry_days * SECONDS_IN_DAY if registry_days else None
            ),
            message_include_full_set=bool(data.get("message_include_full_set")),
            message_include_comment=bool(data.get("message_include_comment")),
            message_include_link=bool(data.get("message_include_link")),
            session_lifetime_seconds=data["session_lifetime_hours"] * SECONDS_IN_HOUR,
            login_failure_threshold=data["login_failure_threshold"],
            telegram_chat_id=str(data.get("telegram_chat_id") or ""),
            email_recipients=_lines(data.get("email_recipients")),
            webhook_url=str(data.get("webhook_url") or ""),
        )


class JournalFilterForm(forms.Form):
    """Фильтры журнала — ТЗ 11.7, Q-C21. Мусор в параметрах — это «всё», а не ошибка страницы."""

    search = forms.CharField(required=False, max_length=253)
    chosen = forms.CharField(required=False)
    period = forms.CharField(required=False)

    def selected_filter(self) -> JournalFilter:
        return self._enum(JournalFilter, "chosen", JournalFilter.ALL)

    def selected_period(self) -> JournalPeriod:
        return self._enum(JournalPeriod, "period", JournalPeriod.ALL)

    def search_text(self) -> str:
        return str(self.cleaned_data.get("search") or "") if self.is_valid() else ""

    def _enum[T: StrEnum](self, kind: type[T], field: str, default: T) -> T:
        if not self.is_valid():
            return default
        raw = str(self.cleaned_data.get(field) or "")
        try:
            return kind(raw)
        except ValueError:
            return default


def _lines(text: object) -> tuple[str, ...]:
    """Список по строке в строке: так его вводят и в DNS-серверах, и в адресах почты."""
    return tuple(line.strip() for line in str(text or "").splitlines() if line.strip())
