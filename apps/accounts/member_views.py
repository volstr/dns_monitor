"""Экран участников — ТЗ 3, 3.1; критерии 29, 75, 76, 80–87.

Отдельным модулем от экранов входа: те живут до входа, эти — после и только для администратора.

Решения принимают сервисы. Обработчик разбирает форму, зовёт одну функцию и выбирает, куда
вернуться. Предохранители 3.1 проверяются в сервисе, а не здесь: спрятанная кнопка ограничением
не является, и 3.1 требует проверки «и на стороне API».
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.timezone import localtime

from apps.accounts.access import admin_required
from apps.accounts.models import Member, Role
from apps.accounts.selectors.members import members_page
from apps.accounts.services.members import (
    InvitationIssued,
    PasswordIssued,
    Refused,
    deactivate_member,
    delete_member,
    invite_with_link,
    invite_with_password,
    reset_password,
    terminate_sessions,
    unbind_authenticator,
    update_member,
)
from core.adapters.redis_state import RedisStateStore
from core.adapters.system_clock import SystemClock

#: ТЗ 3: сроки жизни ссылки-приглашения.
LIFETIME_CHOICES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

GENERATED_PASSWORD_BYTES = 12


def _store() -> RedisStateStore:
    return RedisStateStore.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)


@admin_required
def member_list(request: HttpRequest) -> HttpResponse:
    """Список участников. Наблюдателю сюда нельзя (ТЗ 3, критерий 87)."""
    page = members_page(viewer=request.user, state_store=_store())  # type: ignore[arg-type]
    return render(request, "accounts/members.html", {"page": page, "screen": "members"})


@admin_required
def member_invite(request: HttpRequest) -> HttpResponse:
    """Создание участника — ссылкой или выдачей пароля (ТЗ 3).

    Способ «письмом» доступен только при заданном SMTP_HOST (критерии 75, 76); канал почты пока
    заглушка, поэтому письмо не уходит, и способ показывается выключенным.
    """
    email = request.POST.get("email", "")
    name = request.POST.get("name", "")
    role = request.POST.get("role", Role.OBSERVER)
    at = SystemClock().now()

    if request.POST.get("way") == "password":
        password = secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)
        result = invite_with_password(
            email=email, name=name, role=role, password=password, author=request.user, at=at
        )
    else:
        lifetime = LIFETIME_CHOICES.get(request.POST.get("lifetime", "7d"), timedelta(days=7))
        result = invite_with_link(
            email=email,
            name=name,
            role=role,
            lifetime=lifetime,
            author=request.user,
            at=at,
            state_store=_store(),
        )

    if isinstance(result, Refused):
        messages.error(request, result.reason)
    elif isinstance(result, PasswordIssued):
        # Показывается один раз (ТЗ 3): второй возможности не будет.
        messages.success(
            request, f"Участник создан. Пароль (показывается один раз): {result.password}"
        )
    elif isinstance(result, InvitationIssued):
        messages.success(
            request,
            f"Участник создан. Ссылка действует до "
            f"{localtime(result.expires_at):%d.%m.%Y %H:%M} и остаётся "
            f"в списке, пока не открыта.",
        )
    return redirect("member-list")


@admin_required
def member_update(request: HttpRequest, member_id: int) -> HttpResponse:
    """Правка имени, почты и роли — ТЗ 3.1."""
    result = update_member(
        member_id=member_id,
        name=request.POST.get("name", ""),
        email=request.POST.get("email", ""),
        role=request.POST.get("role", Role.OBSERVER),
        author=request.user,  # type: ignore[arg-type]
        at=SystemClock().now(),
    )
    return _back(request, result, "Изменения сохранены.")


@admin_required
def member_action(request: HttpRequest, member_id: int, action: str) -> HttpResponse:
    """Действия с доступом — ТЗ 3.1, критерии 81, 82, 83, 84, 85, 86."""
    author: Member = request.user  # type: ignore[assignment]
    at = SystemClock().now()

    if action == "deactivate":
        return _back(
            request,
            deactivate_member(member_id=member_id, author=author, at=at),
            "Участник отключён.",
        )
    if action == "delete":
        return _back(
            request,
            delete_member(member_id=member_id, author=author, at=at),
            "Участник удалён.",
        )
    if action == "sessions":
        return _back(
            request,
            terminate_sessions(member_id=member_id, author=author, at=at),
            "Все сессии участника завершены.",
        )
    if action == "authenticator":
        return _back(
            request,
            unbind_authenticator(member_id=member_id, author=author, at=at),
            "Аутентификатор отвязан: при следующем входе участник привяжет его заново.",
        )

    password = secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)
    result = reset_password(member_id=member_id, password=password, author=author, at=at)
    if isinstance(result, Refused):
        return _back(request, result, "")
    messages.success(
        request,
        f"Пароль сброшен. Новый (показывается один раз): {password}. "
        f"Прежний недействителен, сессии завершены.",
    )
    return redirect("member-list")


def _back(request: HttpRequest, result: object, success: str) -> HttpResponse:
    if isinstance(result, Refused):
        messages.error(request, result.reason)
    elif success:
        messages.success(request, success)
    return redirect("member-list")
