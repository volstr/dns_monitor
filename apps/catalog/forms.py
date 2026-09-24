"""Формы каталога — ТЗ 8.2, 11.5.

Форма разбирает вход и ничего не решает по существу: фильтрацию делает селектор. Значения по
умолчанию задаются здесь, чтобы обработчик не ветвился (`CLAUDE.md`).
"""

from __future__ import annotations

from django import forms

from apps.catalog.models import COMMENT_MAX_LENGTH, RECORD_TYPE_CHOICES
from apps.catalog.selectors.domains import DomainFilter


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
