"""
Состояние доступа участника — ТЗ 3, Q-C17, Q-D21.

Приоритет: Отключён → Ссылка не открыта → Пароль выдан, не менялся → Активен.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.models import Invitation, Member
from apps.accounts.selectors.access import MemberAccessState, access_state

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)


def make_member(**extra: object) -> Member:
    return Member.objects.create_member(
        email='d.osipov@contoso.ru', name='Дмитрий Осипов', created_at=MOMENT, **extra
    )


def invite(member: Member, *, opened_at: datetime | None = None, expires_in_days: int = 7) -> None:
    Invitation.objects.create(
        member=member,
        token_hash=f'hash-{member.pk}-{expires_in_days}',
        created_at=MOMENT,
        expires_at=MOMENT + timedelta(days=expires_in_days),
        opened_at=opened_at,
    )


def test_d21_active_member_without_invitation() -> None:
    member = make_member()

    assert access_state(member) is MemberAccessState.ACTIVE


def test_d21_unopened_invitation_is_pending() -> None:
    member = make_member()
    invite(member)

    assert access_state(member) is MemberAccessState.INVITATION_PENDING


def test_d21_opened_invitation_is_no_longer_pending() -> None:
    member = make_member()
    invite(member, opened_at=MOMENT + timedelta(hours=1))

    assert access_state(member) is MemberAccessState.ACTIVE


def test_d21_issued_password_is_shown_until_changed() -> None:
    member = make_member(must_change_password=True)

    assert access_state(member) is MemberAccessState.PASSWORD_ISSUED


def test_d21_disabled_wins_over_every_other_state() -> None:
    # Отключённый участник с непринятым приглашением и невыданным паролем — всё равно «Отключён».
    member = make_member(is_active=False, must_change_password=True)
    invite(member)

    assert access_state(member) is MemberAccessState.DISABLED


def test_d21_invitation_wins_over_issued_password() -> None:
    member = make_member(must_change_password=True)
    invite(member)

    assert access_state(member) is MemberAccessState.INVITATION_PENDING


def test_31_expired_invitation_keeps_member_visible_as_pending() -> None:
    # Критерий 31: просроченная ссылка недействительна, но участник виден как непринявший.
    member = make_member()
    invite(member, expires_in_days=-1)

    assert access_state(member) is MemberAccessState.INVITATION_PENDING
