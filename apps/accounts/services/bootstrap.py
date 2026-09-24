"""Первый администратор установки — ТЗ 3.

ТЗ описывает три способа создания участника (3), и все они требуют администратора, который уже
вошёл. Откуда берётся самый первый, ТЗ не говорит, а без него в свежую установку нельзя войти
никогда: экран входа есть, а учётных записей нет.

Поэтому — отдельная операция начальной установки. Она **не** является ещё одним способом
создания участника: если администратор в установке уже есть, она отказывает. Иначе получился бы
обходной путь мимо предохранителей 3.1.

Отказ — штатный исход с причиной (`CLAUDE.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.accounts.models import Member, Role

MIN_PASSWORD_LENGTH = 12


@dataclass(frozen=True, slots=True)
class AdminCreated:
    member_id: int
    email: str


@dataclass(frozen=True, slots=True)
class AdminNotCreated:
    reason: str


def create_first_admin(
    *, email: str, name: str, password: str, at: datetime
) -> AdminCreated | AdminNotCreated:
    """Создать первого администратора. Работает только на установке без администраторов."""
    if Member.objects.filter(role=Role.ADMIN, is_active=True).exists():
        return AdminNotCreated(
            reason=(
                "В установке уже есть администратор. Новых участников заводят из интерфейса, "
                "на вкладке «Участники»."
            )
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        return AdminNotCreated(
            reason=f"Пароль короче {MIN_PASSWORD_LENGTH} символов."
        )
    normalized = email.strip().lower()
    if Member.objects.filter(email=normalized).exists():
        return AdminNotCreated(reason=f"Участник с почтой {normalized} уже есть.")

    member = Member.objects.create_member(
        email=normalized,
        name=name.strip(),
        role=Role.ADMIN,
        password=password,
        created_at=at,
    )
    return AdminCreated(member_id=member.pk, email=member.email)
