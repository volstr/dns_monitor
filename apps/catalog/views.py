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
from apps.catalog.forms import (
    DomainCardForm,
    DomainCreateForm,
    DomainEditForm,
    DomainFilterForm,
    GlobalSettingsForm,
    GroupForm,
    JournalFilterForm,
)
from apps.catalog.models import Domain
from apps.catalog.selectors.domain_card import domain_card
from apps.catalog.selectors.domains import DomainFilter, domain_list_page
from apps.catalog.selectors.global_settings import settings_page
from apps.catalog.selectors.groups import group_edit_page, group_list_page
from apps.catalog.services.allowlist import parse_allowlist
from apps.catalog.services.domains import (
    DomainNotCreated,
    DomainNotSaved,
    DomainsNotAdded,
    add_domains,
    delete_domain,
    save_domain,
    switch_watch,
)
from apps.catalog.services.groups import GroupNotSaved, save_group
from apps.catalog.services.settings import SettingsNotSaved, save_global_settings
from apps.monitoring.selectors.journal import ExportRefused, journal_export, journal_page
from apps.monitoring.selectors.overview import overview
from apps.monitoring.services.manual_check import (
    CheckRejected,
    RejectionReason,
    request_domain_check,
    request_group_check,
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


@admin_required
def domain_create(request: HttpRequest) -> HttpResponse:
    """Добавление домена — ТЗ 10.1. Наблюдателю сюда нельзя (ТЗ 3)."""
    if request.method != "POST":
        return render(
            request,
            "catalog/domain_new.html",
            {"groups": group_list_page().cards, "screen": "domains"},
        )

    form = DomainCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Укажите хотя бы одно имя домена.")
        return redirect("domain-new")

    result = add_domains(
        names=form.domain_names(),
        group_id=form.group_id(),
        comment=form.comment_text(),
        allowlist_text=form.allowlist_text(),
        at=SystemClock().now(),
    )

    if isinstance(result, DomainsNotAdded):
        # 10.2: пока в списке есть ошибочная строка, сохранять нельзя — и домены не создаются.
        return render(
            request,
            "catalog/domain_new.html",
            {
                "groups": group_list_page().cards,
                "screen": "domains",
                "parsed": result.allowlist,
                "draft": request.POST,
            },
        )

    for line in result.refused:
        messages.error(request, f"«{line.text}»: {line.reason}")
    for line in result.created:
        if line.converted_from:
            # 10.1: перевод в punycode показывается, а не происходит молча.
            messages.info(request, f"«{line.converted_from}» записан как {line.name}.")
    if result.trap_warning:
        messages.warning(request, result.trap_warning)

    created = result.created
    if not created:
        return redirect("domain-new")

    messages.success(
        request,
        f"Добавлено доменов: {len(created)}. Первая проверка пройдёт в ближайшие секунды.",
    )
    if len(created) == 1:
        name = created[0].name
        return redirect("domain-detail", domain_id=Domain.objects.get(name=name).pk)
    return redirect("domain-list")


@member_required
def domain_detail(request: HttpRequest, domain_id: int) -> HttpResponse:
    """Карточка домена — ТЗ 9.1, 11.8. Сегменты фильтров приходят в адресе страницы."""
    form = DomainCardForm(request.GET)
    card = domain_card(
        domain_id=domain_id,
        at=SystemClock().now(),
        registry=form.registry_filter(),
        history=form.history_filter(),
    )
    if card is None:
        raise Http404

    return render(request, "catalog/domain_card.html", {"card": card, "screen": "domains"})


@admin_required
def domain_watch(request: HttpRequest, domain_id: int) -> HttpResponse:
    """«Приостановить» и «Возобновить» — ТЗ 11.1."""
    enabled = request.POST.get("enabled") == "yes"
    result = switch_watch(domain_id=domain_id, enabled=enabled, at=SystemClock().now())

    if isinstance(result, DomainNotCreated):
        raise Http404
    if result.watch_enabled:
        messages.success(request, "Наблюдение возобновлено. Проверка пройдёт в ближайшие секунды.")
    else:
        messages.info(
            request,
            "Наблюдение приостановлено. Домен не проверяется и не шлёт уведомлений, "
            "реестр адресов сохраняется.",
        )
    return redirect("domain-detail", domain_id=domain_id)


@admin_required
def domain_delete(request: HttpRequest, domain_id: int) -> HttpResponse:
    """Удаление домена с подтверждением: оно необратимо и уносит историю."""
    card = domain_card(domain_id=domain_id, at=SystemClock().now())
    if card is None:
        raise Http404

    if request.method != "POST":
        return render(
            request,
            "catalog/domain_delete.html",
            {"card": card, "screen": "domains", "events": len(card.events)},
        )

    result = delete_domain(domain_id=domain_id)
    if isinstance(result, DomainNotCreated):
        raise Http404

    messages.success(
        request,
        f"Домен {result.name} удалён вместе с реестром и записями журнала: {result.events}.",
    )
    return redirect("domain-list")


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


@member_required
def overview_screen(request: HttpRequest) -> HttpResponse:
    """Обзор — ТЗ 11.9, Q-E5, Q-C12."""
    return render(
        request,
        "monitoring/overview.html",
        {"page": overview(at=SystemClock().now()), "screen": "overview"},
    )


@member_required
def journal(request: HttpRequest) -> HttpResponse:
    """Журнал — ТЗ 11.7, Q-D4. Наблюдателю доступен: раздел 3 включает журнал в его чтение."""
    form = JournalFilterForm(request.GET)
    page = journal_page(
        at=SystemClock().now(),
        search=form.search_text(),
        chosen=form.selected_filter(),
        period=form.selected_period(),
    )
    return render(request, "monitoring/journal.html", {"page": page, "screen": "events"})


@member_required
def journal_csv(request: HttpRequest) -> HttpResponse:
    """Выгрузка журнала — ТЗ 11.7, критерий 49. Выгружается текущая выборка с фильтрами."""
    form = JournalFilterForm(request.GET)
    result = journal_export(
        at=SystemClock().now(),
        search=form.search_text(),
        chosen=form.selected_filter(),
        period=form.selected_period(),
    )

    if isinstance(result, ExportRefused):
        messages.error(request, result.reason)
        return redirect("event-list")

    response = HttpResponse(result.content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="dns-monitoring-events.csv"'
    return response


@member_required
def global_settings(request: HttpRequest) -> HttpResponse:
    """Общие настройки — ТЗ 4.1. Наблюдателю на чтение и без адресатов (Q-D19)."""
    return render(
        request,
        "catalog/settings.html",
        {"page": settings_page(), "screen": "settings"},
    )


@admin_required
def global_settings_save(request: HttpRequest) -> HttpResponse:
    """Сохранение общих настроек. Наблюдателю сюда нельзя — критерий 29."""
    form = GlobalSettingsForm(request.POST)
    if not form.is_valid():
        for field, errors in form.errors.items():
            messages.error(request, f"{field}: {'; '.join(errors)}")
        return redirect("settings")

    result = save_global_settings(form.values())

    if isinstance(result, SettingsNotSaved):
        for reason in result.reasons:
            messages.error(request, reason)
        return redirect("settings")

    for warning in result.warnings:
        messages.warning(request, warning)
    messages.success(request, "Сохранено.")
    return redirect("settings")


@member_required
def group_list(request: HttpRequest) -> HttpResponse:
    """Список групп — ТЗ 5, 11.3."""
    return render(
        request,
        "catalog/group_list.html",
        {"page": group_list_page(), "screen": "groups"},
    )


@admin_required
def group_edit(request: HttpRequest, group_id: int | None = None) -> HttpResponse:
    """Настройка группы и создание новой. Наблюдателю сюда нельзя (ТЗ 3)."""
    page = group_edit_page(group_id)
    if page is None:
        raise Http404

    return render(request, "catalog/group_edit.html", {"page": page, "screen": "groups"})


@admin_required
def group_save(request: HttpRequest, group_id: int | None = None) -> HttpResponse:
    """Сохранение группы. Предупреждения Q-13.1 сохранению не мешают, но показываются."""
    form = GroupForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Проверьте поля формы: значения не приняты.")
        return redirect("group-edit", group_id=group_id) if group_id else redirect("group-new")

    result = save_group(
        group_id=group_id,
        name=form.group_name(),
        comment=form.comment_text(),
        check_period_seconds=form.period_seconds(),
        freshness_threshold_seconds=form.threshold_seconds(),
        notification_mode=form.mode(),
        notify_back_to_normal=form.notify_back(),
        reminder_interval_seconds=form.reminder_seconds(),
    )

    if isinstance(result, GroupNotSaved):
        for reason in result.reasons:
            messages.error(request, reason)
        return redirect("group-edit", group_id=group_id) if group_id else redirect("group-new")

    for warning in result.warnings:
        messages.warning(request, warning)
    messages.success(request, "Сохранено.")
    return redirect("group-edit", group_id=result.group_id)


@member_required
def group_domains(request: HttpRequest, group_id: int) -> HttpResponse:
    """«Домены группы» — тот же список доменов, отфильтрованный по группе (11.3)."""
    page = domain_list_page(at=SystemClock().now(), group_id=group_id, chosen=DomainFilter.ALL)
    return render(request, "catalog/domain_list.html", {"page": page, "screen": "domains"})


@admin_required
def group_check_now(request: HttpRequest, group_id: int) -> HttpResponse:
    """«Проверить домены группы» — ТЗ 11.6, кулдаун 60 секунд, критерий 47."""
    store = RedisStateStore.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)
    result = request_group_check(
        group_id=group_id,
        at=SystemClock().now(),
        state_store=store,
        confirmed=request.POST.get("confirmed") == "yes",
    )

    if isinstance(result, CheckRejected):
        messages.info(request, _group_rejection_text(result))
    else:
        messages.success(
            request, f"Домены поставлены в очередь: {result.domains}. Проверка пройдёт скоро."
        )
    return redirect("group-edit", group_id=group_id)


def _group_rejection_text(result: CheckRejected) -> str:
    if result.reason == RejectionReason.COOLDOWN:
        return "Домены этой группы проверяли только что. Повторный запуск — через минуту."
    if result.reason == RejectionReason.NEEDS_CONFIRMATION:
        # 11.6: больше двухсот доменов — запуск требует подтверждения с указанием их числа.
        return (
            f"В группе {result.domains} доменов. Нажмите «Проверить домены группы» ещё раз с "
            f"подтверждением, чтобы запустить проверку всех."
        )
    return "В группе нет доменов под наблюдением."


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
