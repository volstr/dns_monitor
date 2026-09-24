"""Формы каталога — ТЗ 8.2, 11.5.

Форма разбирает вход и ничего не решает по существу: фильтрацию делает селектор. Значения по
умолчанию задаются здесь, чтобы обработчик не ветвился (`CLAUDE.md`).
"""

from __future__ import annotations

from django import forms

from apps.catalog.models import (
    COMMENT_MAX_LENGTH,
    GROUP_NAME_MAX_LENGTH,
    RECORD_TYPE_CHOICES,
)
from apps.catalog.selectors.domains import DomainFilter

SECONDS_IN_MINUTE = 60


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
