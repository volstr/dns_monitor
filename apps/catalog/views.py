"""Экраны каталога — ТЗ 11.5, 9.1, 10.2, 11.6, 8.2.

Обработчику разрешены ровно четыре вещи (`CLAUDE.md`): разобрать вход формой, вызвать одну
функцию сервиса или селектора, выбрать шаблон и передать результат. Доменных решений здесь нет —
ни разбора CIDR, ни наследования настроек, ни `timezone.now()`.

Одно место, где вызовов два, — неудачное сохранение: сервис говорит, что список не годится, а
селектор собирает ту же карточку, чтобы показать её вместе с причинами и не потерять ввод (10.2:
«корректные строки при этом не теряются»). Решение по существу принимает по-прежнему сервис.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.accounts.access import admin_required, member_required
from apps.catalog.forms import DomainEditForm, DomainFilterForm
from apps.catalog.selectors.domain_card import domain_card
from apps.catalog.selectors.domains import domain_list_page
from apps.catalog.services.allowlist import parse_allowlist
from apps.catalog.services.domains import DomainNotSaved, save_domain
from apps.monitoring.services.manual_check import (
    CheckRejected,
    RejectionReason,
    request_domain_check,
)
from core.adapters.redis_state import RedisStateStore
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


@member_required
def domain_detail(request: HttpRequest, domain_id: int) -> HttpResponse:
    """Карточка домена — ТЗ 9.1, 11.8."""
    card = domain_card(domain_id=domain_id, at=SystemClock().now())
    if card is None:
        raise Http404

    return render(request, "catalog/domain_card.html", {"card": card, "screen": "domains"})


@admin_required
def domain_save(request: HttpRequest, domain_id: int) -> HttpResponse:
    """Сохранение карточки. Наблюдателю сюда нельзя: ТЗ 3, «не меняет ничего»."""
    form = DomainEditForm(request.POST)
    if not form.is_valid():
        raise Http404

    result = save_domain(
        domain_id=domain_id,
        comment=form.comment_text(),
        record_types=form.chosen_record_types(),
        allowlist_text=form.allowlist_text(),
        watch_enabled=form.watch(),
    )

    if isinstance(result, DomainNotSaved):
        return _card_with_errors(request, domain_id, result)

    for was, became in result.normalized:
        messages.info(request, f"Запись {was} приведена к {became}.")
    messages.success(request, "Сохранено.")
    return redirect("domain-detail", domain_id=domain_id)


@admin_required
def allowlist_check(request: HttpRequest, domain_id: int) -> HttpResponse:
    """Построчная проверка списка — ТЗ 10.2, критерии 44, 45.

    Зовётся из HTMX с задержкой около 250 мс. Разбор тот же самый, что и при сохранении:
    дублировать его нельзя ни в JS, ни здесь.
    """
    form = DomainEditForm(request.POST)
    if not form.is_valid():
        raise Http404

    parsed = parse_allowlist(form.allowlist_text(), form.chosen_record_types())
    return render(request, "catalog/_allowlist_issues.html", {"parsed": parsed})


@admin_required
def domain_check_now(request: HttpRequest, domain_id: int) -> HttpResponse:
    """«Проверить сейчас» — ТЗ 11.6, Q-D18, критерии 47, 48.

    Резолв здесь не выполняется: кнопка ставит домен в очередь, разбирает его ближайший тик.
    """
    store = RedisStateStore.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)
    result = request_domain_check(
        domain_id=domain_id, at=SystemClock().now(), state_store=store
    )

    if isinstance(result, CheckRejected):
        messages.info(request, _rejection_text(result.reason))
    else:
        messages.success(
            request, "Домен поставлен в очередь: проверка пройдёт в ближайшие секунды."
        )
    return redirect("domain-detail", domain_id=domain_id)


def _rejection_text(reason: RejectionReason) -> str:
    """Отказ объясняется словами: кулдаун — не ошибка, а нормальный ответ (11.6)."""
    if reason == RejectionReason.COOLDOWN:
        return "Этот домен проверяли только что. Повторная проверка доступна через полминуты."
    return "Домен выключен из наблюдения, проверять нечего."


def _card_with_errors(
    request: HttpRequest, domain_id: int, result: DomainNotSaved
) -> HttpResponse:
    card = domain_card(domain_id=domain_id, at=SystemClock().now())
    if card is None:
        raise Http404

    return render(
        request,
        "catalog/domain_card.html",
        {
            "card": card,
            "screen": "domains",
            "parsed": result.parsed,
            "save_reasons": result.reasons,
            "draft": request.POST,
        },
    )
