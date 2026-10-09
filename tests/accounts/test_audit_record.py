"""Журнал действий без домена — ТЗ 3.1, 11.13, Q-D4, Q-D17."""

from datetime import UTC, datetime

import pytest

from apps.accounts.models import AuditAction, AuditRecord, Member

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)


def make_member(email: str, name: str) -> Member:
    return Member.objects.create_member(email=email, name=name, created_at=MOMENT)


def test_82_unbinding_the_authenticator_is_recorded_with_its_author() -> None:
    admin = make_member('a.kirilova@contoso.ru', 'Анна Кирилова')
    target = make_member('d.osipov@contoso.ru', 'Дмитрий Осипов')

    AuditRecord.objects.create(
        action=AuditAction.AUTHENTICATOR_UNBOUND,
        occurred_at=MOMENT,
        author=admin,
        author_name=admin.name,
        target=target,
        target_name=target.name,
    )

    record = AuditRecord.objects.get()
    assert record.action == AuditAction.AUTHENTICATOR_UNBOUND
    assert record.author_name == 'Анна Кирилова'


def test_d17_deleting_a_member_keeps_the_name_snapshot() -> None:
    # Удалять можно только тех, кто ни разу не входил (3.1). История при этом не теряет авторства.
    admin = make_member('a.kirilova@contoso.ru', 'Анна Кирилова')
    target = make_member('s.gavrilov@contoso.ru', 'Сергей Гаврилов')
    AuditRecord.objects.create(
        action=AuditAction.MEMBER_INVITED,
        occurred_at=MOMENT,
        author=admin,
        author_name=admin.name,
        target=target,
        target_name=target.name,
    )

    target.delete()

    record = AuditRecord.objects.get()
    assert record.target is None
    assert record.target_name == 'Сергей Гаврилов'
    assert record.author == admin


def test_11_13_stopping_checks_is_recorded_without_an_author() -> None:
    AuditRecord.objects.create(action=AuditAction.CHECKS_STOPPED, occurred_at=MOMENT)

    record = AuditRecord.objects.get()
    assert record.author is None
    assert record.author_name == ''
