"""Участники и приглашения — ТЗ 3, 3.1, 4.0, Q-D16."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import Invitation, Member, Role

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)
PASSWORD = 'tuman-9-krisa-vody'


def make_member(email: str = 'a.kirilova@contoso.ru', **extra: object) -> Member:
    return Member.objects.create_member(
        email=email, name='Анна Кирилова', created_at=MOMENT, **extra
    )


def test_3_email_is_the_login_and_is_unique() -> None:
    make_member()

    with pytest.raises(IntegrityError), transaction.atomic():
        make_member()


def test_3_password_is_stored_only_as_a_hash() -> None:
    member = make_member(password=PASSWORD)

    assert member.password != PASSWORD
    assert member.check_password(PASSWORD)


def test_4_0_password_hash_column_has_room_for_a_new_algorithm() -> None:
    assert Member._meta.get_field('password').max_length == 255


def test_3_observer_is_the_default_role() -> None:
    member = make_member()

    assert member.role == Role.OBSERVER


def test_3_1_authenticator_is_not_bound_initially() -> None:
    member = make_member()

    assert member.totp_secret == ''


def test_d17_deleting_the_creator_keeps_the_member() -> None:
    admin = make_member()
    invited = make_member(email='s.gavrilov@contoso.ru', created_by=admin)

    admin.delete()

    invited.refresh_from_db()
    assert invited.created_by is None


def test_d16_only_the_token_hash_is_stored() -> None:
    member = make_member()

    Invitation.objects.create(
        member=member,
        token_hash='0f6c1a' * 8,
        created_at=MOMENT,
        expires_at=MOMENT + timedelta(days=7),
    )

    invitation = Invitation.objects.get()
    assert invitation.opened_at is None
    # Открытого токена в базе нет: он живёт ключом auth:invite:{id} в Redis (Q-D16).
    assert not hasattr(invitation, 'token')


def test_d16_token_hash_is_unique() -> None:
    first = make_member()
    second = make_member(email='s.gavrilov@contoso.ru')
    shared_hash = '0f6c1a' * 8

    Invitation.objects.create(
        member=first,
        token_hash=shared_hash,
        created_at=MOMENT,
        expires_at=MOMENT + timedelta(days=7),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Invitation.objects.create(
            member=second,
            token_hash=shared_hash,
            created_at=MOMENT,
            expires_at=MOMENT + timedelta(days=7),
        )


def test_deleting_a_member_deletes_their_invitations() -> None:
    member = make_member()
    Invitation.objects.create(
        member=member,
        token_hash='0f6c1a' * 8,
        created_at=MOMENT,
        expires_at=MOMENT + timedelta(days=7),
    )

    member.delete()

    assert Invitation.objects.count() == 0
