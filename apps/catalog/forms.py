"""Формы каталога — ТЗ 8.2, 11.5.

Форма разбирает вход и ничего не решает по существу: фильтрацию делает селектор. Значения по
умолчанию задаются здесь, чтобы обработчик не ветвился (`CLAUDE.md`).
"""

from __future__ import annotations

from django import forms

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
