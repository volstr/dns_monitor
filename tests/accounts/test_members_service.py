"""Участники — ТЗ 3, 3.1; критерии 30, 31, 80–87."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.models import AuditAction, AuditRecord, Invitation, Member, Role
from apps.accounts.services.members import (
    InvitationIssued,
    MemberSaved,
    PasswordIssued,
    Refused,
    accept_invitation,
    deactivate_member,
    delete_member,
    invite_with_link,
    invite_with_password,
    reset_password,
    set_own_password,
    terminate_sessions,
    unbind_authenticator,
    update_member,
)
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore
from core.ports.state_store import invitation_token

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
PASSWORD = "tuman-9-krisa-vody"
DAY = timedelta(hours=24)


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(MOMENT))


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru",
        name="Анна Кирилова",
        created_at=MOMENT,
        role=Role.ADMIN,
        password=PASSWORD,
    )


def make_observer(email: str = "watch@contoso.ru", **extra: object) -> Member:
    return Member.objects.create_member(
        email=email, name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER, **extra
    )


def test_3_link_invitation_stores_only_a_hash(admin: Member, store: FakeStateStore) -> None:
    """Q-D16: в базе только хеш, открытый текст — в Redis со сроком жизни ссылки."""
    result = invite_with_link(
        email="new@contoso.ru",
        name="Новый",
        role=Role.OBSERVER,
        lifetime=DAY,
        author=admin,
        at=MOMENT,
        state_store=store,
    )

    assert isinstance(result, InvitationIssued)
    invitation = Invitation.objects.get()
    assert invitation.token_hash != result.token
    assert store.recall(invitation_token(invitation.pk, DAY)) == result.token


def test_30_opened_link_is_not_valid_again(admin: Member, store: FakeStateStore) -> None:
    """Критерий 30: ссылка-приглашение открыта повторно → недействительна."""
    issued = invite_with_link(
        email="new@contoso.ru",
        name="Новый",
        role=Role.OBSERVER,
        lifetime=DAY,
        author=admin,
        at=MOMENT,
        state_store=store,
    )
    assert isinstance(issued, InvitationIssued)

    first = accept_invitation(token=issued.token, password=PASSWORD, at=MOMENT, state_store=store)
    second = accept_invitation(token=issued.token, password=PASSWORD, at=MOMENT, state_store=store)

    assert isinstance(first, MemberSaved)
    assert isinstance(second, Refused)
    assert "уже была открыта" in second.reason


def test_31_expired_link_is_refused_and_member_stays_unaccepted(
    admin: Member, store: FakeStateStore
) -> None:
    """Критерий 31: просроченная ссылка недействительна, участник виден как непринявший."""
    issued = invite_with_link(
        email="new@contoso.ru",
        name="Новый",
        role=Role.OBSERVER,
        lifetime=DAY,
        author=admin,
        at=MOMENT,
        state_store=store,
    )
    assert isinstance(issued, InvitationIssued)

    result = accept_invitation(
        token=issued.token,
        password=PASSWORD,
        at=MOMENT + DAY + timedelta(minutes=1),
        state_store=store,
    )

    assert isinstance(result, Refused)
    assert "истёк" in result.reason
    assert Invitation.objects.get().opened_at is None


def test_3_password_invitation_requires_a_change_at_first_login(admin: Member) -> None:
    result = invite_with_password(
        email="new@contoso.ru",
        name="Новый",
        role=Role.OBSERVER,
        password=PASSWORD,
        author=admin,
        at=MOMENT,
    )

    assert isinstance(result, PasswordIssued)
    assert Member.objects.get(pk=result.member_id).must_change_password is True


def test_3_1_observer_cannot_be_made_admin_by_an_observer() -> None:
    """3.1: нельзя назначить роль выше собственной."""
    observer = make_observer()

    result = invite_with_password(
        email="new@contoso.ru",
        name="Новый",
        role=Role.ADMIN,
        password=PASSWORD,
        author=observer,
        at=MOMENT,
    )

    assert isinstance(result, Refused)
    assert "выше собственной" in result.reason


def test_83_cannot_disable_yourself(admin: Member) -> None:
    """Критерий 83: попытка отключить самого себя → отказ."""
    result = deactivate_member(member_id=admin.pk, author=admin, at=MOMENT)

    assert isinstance(result, Refused)
    admin.refresh_from_db()
    assert admin.is_active is True


def test_83_cannot_change_your_own_role(admin: Member) -> None:
    result = update_member(
        member_id=admin.pk,
        name=admin.name,
        email=admin.email,
        role=Role.OBSERVER,
        author=admin,
        at=MOMENT,
    )

    assert isinstance(result, Refused)
    assert "собственную роль" in result.reason


def test_84_last_admin_cannot_be_demoted(admin: Member) -> None:
    """
    Критерий 84: отказ с объяснением причины.

    Автор здесь не сам администратор, а другой участник: собственную роль он и так менять не
    может, и без этого предохранитель был бы недостижим. 3.1 требует проверки «и на стороне
    API, а не только в интерфейсе» — то есть независимо от того, кто дотянулся до вызова.
    """
    other = make_observer()

    result = update_member(
        member_id=admin.pk,
        name=admin.name,
        email=admin.email,
        role=Role.OBSERVER,
        author=other,
        at=MOMENT,
    )

    assert isinstance(result, Refused)
    assert "последний администратор" in result.reason
    admin.refresh_from_db()
    assert admin.role == Role.ADMIN


def test_84_last_admin_cannot_be_disabled(admin: Member) -> None:
    other = make_observer()

    result = deactivate_member(member_id=admin.pk, author=other, at=MOMENT)

    assert isinstance(result, Refused)
    admin.refresh_from_db()
    assert admin.is_active is True


def test_84_admin_can_be_disabled_while_another_one_remains(admin: Member) -> None:
    second = Member.objects.create_member(
        email="second@contoso.ru", name="Второй", created_at=MOMENT, role=Role.ADMIN
    )

    result = deactivate_member(member_id=second.pk, author=admin, at=MOMENT)

    assert isinstance(result, MemberSaved)
    second.refresh_from_db()
    assert second.is_active is False


def test_85_member_who_signed_in_cannot_be_deleted(admin: Member) -> None:
    """Критерий 85: отказ; отключение при этом доступно."""
    member = make_observer()
    Member.objects.filter(pk=member.pk).update(last_login=MOMENT)

    refused = delete_member(member_id=member.pk, author=admin, at=MOMENT)
    assert isinstance(refused, Refused)
    assert "уже входил" in refused.reason

    assert isinstance(deactivate_member(member_id=member.pk, author=admin, at=MOMENT), MemberSaved)


def test_86_member_who_never_signed_in_is_deleted(admin: Member) -> None:
    """Критерий 86: участник удалён, ссылка недействительна."""
    member = make_observer()

    result = delete_member(member_id=member.pk, author=admin, at=MOMENT)

    assert isinstance(result, MemberSaved)
    assert Member.objects.filter(pk=member.pk).exists() is False
    # Авторство в журнале не потеряно: снимок имени записан до удаления (Q-D17).
    assert AuditRecord.objects.filter(action=AuditAction.MEMBER_DELETED).count() == 1


def test_80_changing_the_email_ends_all_sessions(admin: Member) -> None:
    """Критерий 80: смена почты завершает сессии и меняет логин."""
    member = make_observer()
    before = member.get_session_auth_hash()

    result = update_member(
        member_id=member.pk,
        name=member.name,
        email="moved@contoso.ru",
        role=member.role,
        author=admin,
        at=MOMENT,
    )

    assert isinstance(result, MemberSaved)
    member.refresh_from_db()
    assert member.email == "moved@contoso.ru"
    assert member.get_session_auth_hash() != before


def test_81_password_reset_ends_sessions_immediately(admin: Member) -> None:
    """Критерий 81: прежний пароль недействителен немедленно, сессии завершены."""
    member = make_observer(password="staryi-parol-12345")
    before = member.get_session_auth_hash()

    result = reset_password(member_id=member.pk, password=PASSWORD, author=admin, at=MOMENT)

    assert isinstance(result, MemberSaved)
    member.refresh_from_db()
    assert member.check_password("staryi-parol-12345") is False
    assert member.get_session_auth_hash() != before


def test_3_1_terminating_sessions_keeps_the_password(admin: Member) -> None:
    member = make_observer(password=PASSWORD)
    before = member.get_session_auth_hash()

    result = terminate_sessions(member_id=member.pk, author=admin, at=MOMENT)

    assert isinstance(result, MemberSaved)
    member.refresh_from_db()
    assert member.check_password(PASSWORD) is True
    assert member.get_session_auth_hash() != before


def test_82_unbinding_the_authenticator_is_recorded_with_its_author(admin: Member) -> None:
    """Критерий 82: действие есть в журнале с указанием того, кто его выполнил."""
    member = make_observer(totp_secret="JBSWY3DPEHPK3PXP")

    result = unbind_authenticator(member_id=member.pk, author=admin, at=MOMENT)

    assert isinstance(result, MemberSaved)
    member.refresh_from_db()
    assert member.totp_secret == ""
    record = AuditRecord.objects.get(action=AuditAction.AUTHENTICATOR_UNBOUND)
    assert (record.author_name, record.target_name) == ("Анна Кирилова", "Наблюдатель")


def test_3_1_every_change_is_recorded(admin: Member) -> None:
    member = make_observer()

    update_member(
        member_id=member.pk,
        name="Другое имя",
        email=member.email,
        role=member.role,
        author=admin,
        at=MOMENT,
    )

    assert AuditRecord.objects.filter(action=AuditAction.MEMBER_UPDATED).count() == 1


def test_duplicate_email_is_refused(admin: Member) -> None:
    make_observer("taken@contoso.ru")

    result = invite_with_password(
        email="taken@contoso.ru",
        name="Новый",
        role=Role.OBSERVER,
        password=PASSWORD,
        author=admin,
        at=MOMENT,
    )

    assert isinstance(result, Refused)
    assert "уже есть" in result.reason


def test_32_member_sets_the_password_and_the_flag_goes_down() -> None:
    """Критерий 32: обязательная смена при первом входе — тот самый шаг, ради которого флаг."""
    member = Member.objects.create_member(
        email="boss@contoso.ru",
        name="Анна Кирилова",
        created_at=MOMENT,
        password=PASSWORD,
        must_change_password=True,
    )

    result = set_own_password(member_id=member.pk, password="novyj-parol-9-krisa")

    member.refresh_from_db()
    assert isinstance(result, MemberSaved)
    assert member.must_change_password is False
    assert member.check_password("novyj-parol-9-krisa")


def test_32_a_short_password_is_refused_by_the_service_too() -> None:
    """Форма проверяет длину, но сервис обязан быть безопасен сам по себе (`CLAUDE.md`)."""
    member = Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, password=PASSWORD
    )

    result = set_own_password(member_id=member.pk, password="корот")

    member.refresh_from_db()
    assert isinstance(result, Refused)
    assert member.check_password(PASSWORD)


def test_32_own_password_change_leaves_no_audit_record() -> None:
    """
    Список действий 3.1 закрытый, и смены пароля самим участником в нём нет (Q-C11).

    Запись без автора была бы вымышленным термином, а вводить его без спроса нельзя.
    """
    member = Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, password=PASSWORD
    )

    set_own_password(member_id=member.pk, password="novyj-parol-9-krisa")

    assert AuditRecord.objects.count() == 0


def test_32_own_password_change_does_not_terminate_the_session() -> None:
    """Участник входит этим же шагом: завершать его собственную сессию бессмысленно."""
    member = Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, password=PASSWORD
    )
    epoch = member.session_epoch

    set_own_password(member_id=member.pk, password="novyj-parol-9-krisa")

    member.refresh_from_db()
    assert member.session_epoch == epoch
