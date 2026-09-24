"""Экраны каталога — ТЗ 11.5, 8.2.

Обработчику разрешены ровно четыре вещи (`CLAUDE.md`): разобрать вход формой, вызвать одну
функцию селектора, выбрать шаблон и передать результат. Доменных решений здесь нет — ни
наследования настроек, ни вычисления статусов, ни `timezone.now()`.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.accounts.access import member_required
from apps.catalog.forms import DomainFilterForm
from apps.catalog.selectors.domains import domain_list_page
from core.adapters.system_clock import SystemClock


@member_required
def domain_list(request: HttpRequest) -> HttpResponse:
    """Список доменов. Ответ на запрос HTMX — только строки таблицы, без каркаса страницы."""
    form = DomainFilterForm(request.GET)
    page = domain_list_page(
        at=SystemClock().now(),
        search=form.search_text(),
        group_id=form.group_id(),
        chosen=form.selected_filter(),
    )

    template = (
        "catalog/_domain_rows.html"
        if request.headers.get("HX-Request")
        else "catalog/domain_list.html"
    )
    return render(request, template, {"page": page, "screen": "domains"})
