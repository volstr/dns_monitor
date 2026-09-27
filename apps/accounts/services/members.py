"""Участники — ТЗ 3, 3.1; критерии 30, 31, 80–87.

**Предохранители проверяются здесь, а не в интерфейсе.** 3.1 требует этого прямо: «Каждый
проверяется и на стороне API, а не только в интерфейсе». Спрятанная кнопка ограничением доступа
не является.

Отказ — штатный исход с причиной (`CLAUDE.md`). Исключения оставлены настоящим авариям.

Все изменения пишутся в журнал: кто, кого, что изменил и когда (3.1). Рядом с каждой ссылкой на
участника лежит снимок имени (Q-D17) — удаление непринявшего не должно лишать историю авторства.

**Завершение сессий** идёт сменой поколения в `Member.session_epoch`: оно участвует в подписи
сессии, поэтому все выданные сессии, включая чужие устройства, становятся недействительными
немедленно, без ожидания истечения ключей в Redis.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F

from apps.accounts.models import AuditAction, AuditRecord, Invitation, Member, Role
from core.ports.state_store import StateStore, invitation_token

#: ТЗ 3: сроки жизни ссылки-приглашения.
INVITE_LIFETIMES = (timedelta(hours=24), timedelta(days=7), timedelta(days=30))

MIN_PASSWORD_LENGTH = 12
TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class MemberSaved:
    member_id: int


@dataclass(frozen=True, slots=True)
class InvitationIssued:
    """Ссылка показывается один раз и остаётся копируемой, пока не открыта (ТЗ 3)."""

    member_id: int
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordIssued:
    """Пароль показывается один раз (ТЗ 3)."""

    member_id: int
    password: str


@dataclass(frozen=True, slots=True)
class Refused:
    reason: str


type MemberResult = MemberSaved | Refused
type InviteResult = InvitationIssued | PasswordIssued | Refused


def invite_with_link(
    *,
    email: str,
    name: str,
    role: str,
    lifetime: timedelta,
    author: Member,
    at: datetime,
    state_store: StateStore,
) -> InviteResult:
    """Создать участника и выдать одноразовую ссылку — ТЗ 3."""
    refusal = _role_refusal(author, role) or _email_refusal(email)
    if refusal:
        return refusal

    with transaction.atomic():
        member = Member.objects.create_member(
            email=email.strip().lower(), name=name.strip(), role=role, created_at=at
        )
        # Пароля нет: его задаст сам открывший ссылку (ТЗ 3).
        member.set_unusable_password()
        member.save(update_fields=["password"])

        token = secrets.token_urlsafe(TOKEN_BYTES)
        invitation = Invitation.objects.create(
            member=member,
            token_hash=_hashed(token),
            created_at=at,
            expires_at=at + lifetime,
            created_by=author,
        )
        # Q-D16: открытый текст живёт только в Redis, со сроком жизни самой ссылки.
        state_store.remember(invitation_token(invitation.pk, lifetime), token)
        _record(AuditAction.MEMBER_INVITED, author, member, at)

    return InvitationIssued(member_id=member.pk, token=token, expires_at=invitation.expires_at)


def invite_with_password(
    *, email: str, name: str, role: str, password: str, author: Member, at: datetime
) -> InviteResult:
    """Создать участника с выданным паролем — ТЗ 3.

    Флаг «сменить при первом входе» включён по умолчанию: пароль видел администратор.
    """
    refusal = _role_refusal(author, role) or _email_refusal(email) or _password_refusal(password)
    if refusal:
        return refusal

    with transaction.atomic():
        member = Member.objects.create_member(
            email=email.strip().lower(),
            name=name.strip(),
            role=role,
            password=password,
            created_at=at,
            must_change_password=True,
        )
        _record(AuditAction.MEMBER_INVITED, author, member, at)

    return PasswordIssued(member_id=member.pk, password=password)


def accept_invitation(
    *, token: str, password: str, at: datetime, state_store: StateStore
) -> MemberResult:
    """Открыть ссылку и задать пароль — критерии 30, 31."""
    refusal = _password_refusal(password)
    if refusal:
        return refusal

    invitation = (
        Invitation.objects.filter(token_hash=_hashed(token)).select_related("member").first()
    )
    if invitation is None:
        return Refused(reason="Ссылка недействительна.")
    if invitation.opened_at is not None:
        # Критерий 30: ссылка одноразовая.
        return Refused(reason="Ссылка уже была открыта.")
    if invitation.expires_at <= at:
        # Критерий 31: просроченная ссылка недействительна, участник остаётся непринявшим.
        return Refused(reason="Срок действия ссылки истёк. Попросите администратора выдать новую.")

    with transaction.atomic():
        member = invitation.member
        member.set_password(password)
        member.must_change_password = False
        member.save(update_fields=["password", "must_change_password"])
        Invitation.objects.filter(pk=invitation.pk).update(opened_at=at)
        state_store.release(invitation_token(invitation.pk, timedelta(seconds=1)))

    return MemberSaved(member_id=member.pk)


def update_member(
    *, member_id: int, name: str, email: str, role: str, author: Member, at: datetime
) -> MemberResult:
    """Правка имени, почты и роли — ТЗ 3.1, критерии 80, 83, 84, 87."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")

    refusal = (
        _role_refusal(author, role)
        or _self_demotion_refusal(author, member, role)
        or _last_admin_refusal(member, role=role, is_active=member.is_active)
        or _email_refusal(email, exclude_id=member_id)
    )
    if refusal:
        return refusal

    email_changed = member.email != email.strip().lower()
    role_changed = member.role != role

    with transaction.atomic():
        Member.objects.filter(pk=member_id).update(
            name=name.strip(),
            email=email.strip().lower(),
            role=role,
            # Смена почты — чувствительное действие: она же логин, и все сессии завершаются
            # (3.1, критерий 80).
            session_epoch=F("session_epoch") + 1 if email_changed else F("session_epoch"),
        )
        if email_changed:
            _record(AuditAction.EMAIL_CHANGED, author, member, at)
        if role_changed:
            _record(AuditAction.ROLE_CHANGED, author, member, at)
        if not email_changed and not role_changed:
            _record(AuditAction.MEMBER_UPDATED, author, member, at)

    return MemberSaved(member_id=member_id)


def reset_password(
    *, member_id: int, password: str, author: Member, at: datetime
) -> MemberResult:
    """Сброс пароля — критерий 81: прежний недействителен немедленно, сессии завершены."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")
    refusal = _password_refusal(password)
    if refusal:
        return refusal

    with transaction.atomic():
        member.set_password(password)
        member.must_change_password = True
        member.session_epoch += 1
        member.save(update_fields=["password", "must_change_password", "session_epoch"])
        _record(AuditAction.PASSWORD_RESET, author, member, at)

    return MemberSaved(member_id=member_id)


def set_own_password(*, member_id: int, password: str) -> MemberResult:
    """Участник задаёт себе пароль сам — критерий 32, обязательная смена при первом входе.

    Отличия от `reset_password`, из-за которых это отдельная функция, а не флаг:

    - **Автора нет.** Действие совершает сам участник, и `AuditAction` такого значения не
      содержит: список в 3.1 закрытый, а вводить новый термин без спроса нельзя (`CLAUDE.md`).
    - **Признак «сменить пароль» снимается**, а не ставится: ради этого шаг и существует.
    - **Сессии не завершаются.** Завершать нечего: пароль участнику выдал администратор, и
      `reset_password` уже поднял `session_epoch` в тот момент.
    """
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")
    refusal = _password_refusal(password)
    if refusal:
        return refusal

    with transaction.atomic():
        member.set_password(password)
        member.must_change_password = False
        member.save(update_fields=["password", "must_change_password"])

    return MemberSaved(member_id=member_id)


def terminate_sessions(*, member_id: int, author: Member, at: datetime) -> MemberResult:
    """«Завершить все сессии» без смены пароля — 3.1."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")

    with transaction.atomic():
        Member.objects.filter(pk=member_id).update(session_epoch=F("session_epoch") + 1)
        _record(AuditAction.SESSIONS_TERMINATED, author, member, at)

    return MemberSaved(member_id=member_id)


def unbind_authenticator(*, member_id: int, author: Member, at: datetime) -> MemberResult:
    """Отвязать аутентификатор — критерий 82: при следующем входе участник привяжет заново."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")

    with transaction.atomic():
        Member.objects.filter(pk=member_id).update(totp_secret="")
        _record(AuditAction.AUTHENTICATOR_UNBOUND, author, member, at)

    return MemberSaved(member_id=member_id)


def deactivate_member(*, member_id: int, author: Member, at: datetime) -> MemberResult:
    """Отключение — основной способ прекратить доступ (3.1), критерии 83, 84."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")

    refusal = _self_refusal(author, member) or _last_admin_refusal(
        member, role=member.role, is_active=False
    )
    if refusal:
        return refusal

    with transaction.atomic():
        Member.objects.filter(pk=member_id).update(
            is_active=False, session_epoch=F("session_epoch") + 1
        )
        _record(AuditAction.MEMBER_DEACTIVATED, author, member, at)

    return MemberSaved(member_id=member_id)


def delete_member(*, member_id: int, author: Member, at: datetime) -> MemberResult:
    """Удаление — только для тех, кто ни разу не входил (3.1), критерии 85, 86."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return Refused(reason="Участник не найден.")

    refusal = _self_refusal(author, member)
    if refusal:
        return refusal
    if member.last_login is not None:
        return Refused(
            reason=(
                "Этот участник уже входил, поэтому на него ссылается журнал — удалить нельзя. "
                "Отключите его: записи сохранятся, а роль можно будет вернуть."
            )
        )

    with transaction.atomic():
        # Запись пишется до удаления: после него снимок имени взять будет неоткуда (Q-D17).
        _record(AuditAction.MEMBER_DELETED, author, member, at)
        Member.objects.filter(pk=member_id).delete()

    return MemberSaved(member_id=member_id)


def _record(action: str, author: Member, target: Member, at: datetime) -> None:
    """Запись журнала со снимками имён — 3.1, Q-D17."""
    AuditRecord.objects.create(
        action=action,
        occurred_at=at,
        author=author,
        author_name=author.name,
        target=target,
        target_name=target.name,
    )


def _self_refusal(author: Member, member: Member) -> Refused | None:
    if author.pk == member.pk:
        return Refused(reason="Нельзя отключить или удалить самого себя.")
    return None


def _self_demotion_refusal(author: Member, member: Member, role: str) -> Refused | None:
    if author.pk == member.pk and role != author.role:
        return Refused(reason="Нельзя изменить собственную роль.")
    return None


def _role_refusal(author: Member, role: str) -> Refused | None:
    """Нельзя назначить роль выше собственной (3.1)."""
    if role not in Role.values:
        return Refused(reason="Такой роли нет.")
    if role == Role.ADMIN and author.role != Role.ADMIN:
        return Refused(reason="Нельзя назначить роль выше собственной.")
    return None


def _last_admin_refusal(member: Member, *, role: str, is_active: bool) -> Refused | None:
    """Установка без администратора необратима: некому вернуть права (3.1, критерий 84)."""
    if member.role != Role.ADMIN or not member.is_active:
        return None
    stays_admin = role == Role.ADMIN and is_active
    if stays_admin:
        return None

    others = Member.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=member.pk).count()
    if others == 0:
        return Refused(
            reason=(
                "Это последний администратор. Установка без администратора необратима — "
                "сначала назначьте другого."
            )
        )
    return None


def _email_refusal(email: str, exclude_id: int | None = None) -> Refused | None:
    normalized = email.strip().lower()
    if not normalized:
        return Refused(reason="Почта обязательна: она же логин.")
    taken = Member.objects.filter(email=normalized)
    if exclude_id is not None:
        taken = taken.exclude(pk=exclude_id)
    if taken.exists():
        return Refused(reason=f"Участник с почтой {normalized} уже есть.")
    return None


def _password_refusal(password: str) -> Refused | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return Refused(reason=f"Пароль короче {MIN_PASSWORD_LENGTH} символов.")
    return None


def _hashed(token: str) -> str:
    """В базе лежит только хеш токена (11.14, Q-D16)."""
    return hashlib.sha256(token.encode()).hexdigest()
