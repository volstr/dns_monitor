"""Экран участников — ТЗ 3, 3.1; критерии 75, 76.

Список показывает **состояние доступа**, а не только дату входа: «Активен», «Ссылка не открыта»
с датой истечения, «Пароль выдан, не менялся», «Отключён» (ТЗ 3, Q-C17). Состояние вычисляется
готовым селектором `access_state`, здесь оно только переводится в подписи.

Доступность приглашения письмом зависит от `SMTP_HOST` в окружении (ТЗ 3, критерии 75, 76).
Настроить почту из интерфейса нельзя, поэтому при незаданной переменной способ показывается
выключенным с объяснением, к кому обращаться, а кнопки подключения нет вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings

from apps.accounts.models import Invitation, Member, Role
from apps.accounts.selectors.access import MemberAccessState, access_state
from core.ports.state_store import StateStore, invitation_token

#: Имена переменных для баннера критерия 75: человек должен знать, что именно просить.
SMTP_VARIABLES = ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD")

STATE_LABELS = {
    MemberAccessState.ACTIVE: ("Активен", "ok"),
    MemberAccessState.INVITATION_PENDING: ("Ссылка не открыта", "pend"),
    MemberAccessState.PASSWORD_ISSUED: ("Пароль выдан, не менялся", "change"),
    MemberAccessState.DISABLED: ("Отключён", ""),
}


@dataclass(frozen=True, slots=True)
class MemberRow:
    id: int
    name: str
    email: str
    role: str
    role_label: str
    state_label: str
    state_tone: str
    last_login: datetime | None
    is_self: bool
    #: Ссылка остаётся копируемой, пока не открыта (ТЗ 3). Пусто — копировать нечего.
    invite_token: str
    invite_expires_at: datetime | None
    authenticator_bound: bool


@dataclass(frozen=True, slots=True)
class MembersPage:
    rows: tuple[MemberRow, ...]
    #: Критерий 76: способ приглашения письмом доступен только при заданном SMTP_HOST.
    email_invites_available: bool
    smtp_variables: tuple[str, ...]
    two_factor_enabled: bool
    roles: tuple[tuple[str, str], ...]


def members_page(*, viewer: Member, state_store: StateStore) -> MembersPage:
    """Список участников. Открытые токены приглашений достаются из состояния (Q-D16)."""
    members = list(Member.objects.order_by("name"))
    invitations = {
        invitation.member_id: invitation
        for invitation in Invitation.objects.filter(
            member_id__in=[member.pk for member in members], opened_at__isnull=True
        ).order_by("created_at")
    }

    return MembersPage(
        rows=tuple(
            _row(member, viewer, invitations.get(member.pk), state_store) for member in members
        ),
        email_invites_available=bool(settings.ENV.smtp_host),
        smtp_variables=SMTP_VARIABLES,
        two_factor_enabled=settings.ENV.auth_2fa_enabled,
        roles=tuple((role.value, str(role.label)) for role in Role),
    )


def _row(
    member: Member,
    viewer: Member,
    invitation: Invitation | None,
    state_store: StateStore,
) -> MemberRow:
    state = access_state(member)
    label, tone = STATE_LABELS[state]
    token = ""
    if invitation is not None:
        # Открытый текст живёт только в Redis и только до истечения срока ссылки (Q-D16).
        lifetime = invitation.expires_at - invitation.created_at
        token = state_store.recall(invitation_token(invitation.pk, lifetime)) or ""

    return MemberRow(
        id=member.pk,
        name=member.name,
        email=member.email,
        role=member.role,
        role_label=str(Role(member.role).label),
        state_label=label,
        state_tone=tone,
        last_login=member.last_login,
        is_self=member.pk == viewer.pk,
        invite_token=token,
        invite_expires_at=invitation.expires_at if invitation else None,
        authenticator_bound=bool(member.totp_secret),
    )
