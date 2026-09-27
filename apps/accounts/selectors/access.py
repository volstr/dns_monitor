"""
Состояние доступа участника — ТЗ 3, Q-C17, Q-D21.

Состояние не хранится, а вычисляется. Оно складывается из четырёх источников: признака
активности, требования сменить пароль, факта входа и состояния приглашения. Хранимое поле
пришлось бы держать в согласии со всеми четырьмя сразу, и любая правка мимо сервиса —
отключение участника, открытие ссылки, смена пароля — оставляла бы его врать.
"""

from __future__ import annotations

from enum import StrEnum

from apps.accounts.models import Member


class MemberAccessState(StrEnum):
    """Три состояния из ТЗ 3 плюс «Отключён» из 3.1 (Q-C17)."""

    DISABLED = "disabled"
    INVITATION_PENDING = "invitation_pending"
    # Это состояние доступа «Пароль выдан, не менялся» (ТЗ 3), а не пароль: S105 ложный.
    PASSWORD_ISSUED = "password_issued"  # noqa: S105
    ACTIVE = "active"


def access_state(member: Member) -> MemberAccessState:
    """
    Состояние доступа. Приоритет утверждён в Q-D21 и идёт сверху вниз.

    Просроченное приглашение остаётся «Ссылка не открыта»: по критерию 31 участник обязан быть
    виден как непринявший, а срок показывается рядом отдельной строкой.
    """
    if not member.is_active:
        return MemberAccessState.DISABLED
    if member.invitations.filter(opened_at__isnull=True).exists():
        return MemberAccessState.INVITATION_PENDING
    if member.must_change_password:
        return MemberAccessState.PASSWORD_ISSUED
    return MemberAccessState.ACTIVE
