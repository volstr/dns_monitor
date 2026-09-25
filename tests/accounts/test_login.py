"""Вход — ТЗ 3, 11.11; критерии 32, 33, 50, 51, 52, 56, 57."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.accounts.services.authentication import (
    Authenticated,
    Locked,
    PasswordChangeRequired,
    Rejected,
    TotpSetupRequired,
    authenticate,
    confirm_totp_binding,
    new_totp_secret,
)
from apps.catalog.models import GlobalSettings
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
PASSWORD = "tuman-9-krisa-vody"
EMAIL = "a.kirilova@contoso.ru"
IP = "10.20.1.14"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


@pytest.fixture
def member() -> Member:
    return Member.objects.create_member(
        email=EMAIL, name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN, password=PASSWORD
    )


def sign_in(
    store: FakeStateStore,
    *,
    email: str = EMAIL,
    password: str = PASSWORD,
    code: str = "",
    at: datetime = MOMENT,
    two_factor: bool = False,
) -> object:
    return authenticate(
        email=email,
        password=password,
        code=code,
        ip=IP,
        at=at,
        state_store=store,
        two_factor_enabled=two_factor,
    )


def test_3_correct_password_opens_access(member: Member, store: FakeStateStore) -> None:
    result = sign_in(store)

    assert isinstance(result, Authenticated)
    assert result.member_id == member.pk


def test_3_wrong_password_is_refused(member: Member, store: FakeStateStore) -> None:
    result = sign_in(store, password="не тот")

    assert isinstance(result, Rejected)
    assert result.attempts_left == 4


def test_c19_unknown_login_looks_the_same_as_a_wrong_password(store: FakeStateStore) -> None:
    """Q-C19: по тексту отказа нельзя перебрать учётные записи."""
    result = sign_in(store, email="nobody@contoso.ru")

    assert isinstance(result, Rejected)
    assert result.attempts_left == 4


def test_3_disabled_member_cannot_sign_in(member: Member, store: FakeStateStore) -> None:
    Member.objects.filter(pk=member.pk).update(is_active=False)

    assert isinstance(sign_in(store), Rejected)


def test_33_threshold_of_failures_locks_the_login(member: Member, store: FakeStateStore) -> None:
    """Критерий 33: N неудачных попыток → блокировка на 15 минут."""
    for _ in range(4):
        assert isinstance(sign_in(store, password="не тот"), Rejected)

    assert isinstance(sign_in(store, password="не тот"), Locked)
    # Даже с верным паролем: блокировка на паре «логин + IP», а не на попытке.
    assert isinstance(sign_in(store), Locked)


def test_56_lock_expires_on_its_own(
    member: Member, store: FakeStateStore, clock: FakeClock
) -> None:
    """Критерий 56: через 15 минут блокировка снята без участия приложения."""
    for _ in range(5):
        sign_in(store, password="не тот")
    assert isinstance(sign_in(store), Locked)

    clock.advance(timedelta(minutes=16))

    assert isinstance(sign_in(store, at=clock.now()), Authenticated)


def test_57_counting_window_is_fixed_not_sliding(
    member: Member, store: FakeStateStore, clock: FakeClock
) -> None:
    """Критерий 57: 4 попытки, пауза 16 минут, ещё 4 → блокировки нет.

    Срок счётчика ставится только при первом увеличении (11.11). Если продлевать его при каждой
    неудаче, редкие опечатки накопятся за месяцы и заблокируют вход на ровном месте.
    """
    for _ in range(4):
        sign_in(store, password="не тот")

    clock.advance(timedelta(minutes=16))

    for _ in range(4):
        result = sign_in(store, password="не тот", at=clock.now())
        assert isinstance(result, Rejected)


def test_32_member_with_the_flag_must_change_the_password_first(store: FakeStateStore) -> None:
    """Критерий 32: до смены пароля доступа к остальным экранам нет."""
    member = Member.objects.create_member(
        email=EMAIL,
        name="Анна Кирилова",
        created_at=MOMENT,
        password=PASSWORD,
        must_change_password=True,
    )

    result = sign_in(store)

    assert isinstance(result, PasswordChangeRequired)
    assert result.member_id == member.pk


def test_51_member_without_an_authenticator_must_bind_it(
    member: Member, store: FakeStateStore
) -> None:
    """Критерий 51: второй фактор включён, приложение не привязано — обязательная привязка."""
    result = sign_in(store, two_factor=True)

    assert isinstance(result, TotpSetupRequired)


def test_3_correct_code_opens_access(member: Member, store: FakeStateStore) -> None:
    secret = new_totp_secret()
    Member.objects.filter(pk=member.pk).update(totp_secret=secret)
    code = pyotp.TOTP(secret).at(MOMENT)

    result = sign_in(store, code=code, two_factor=True)

    assert isinstance(result, Authenticated)


def test_52_the_same_code_is_not_accepted_twice(member: Member, store: FakeStateStore) -> None:
    """Критерий 52: повторная отправка того же кода TOTP → отказ."""
    secret = new_totp_secret()
    Member.objects.filter(pk=member.pk).update(totp_secret=secret)
    code = pyotp.TOTP(secret).at(MOMENT)

    assert isinstance(sign_in(store, code=code, two_factor=True), Authenticated)
    assert isinstance(sign_in(store, code=code, two_factor=True), Rejected)


def test_3_wrong_code_counts_as_a_failed_attempt(member: Member, store: FakeStateStore) -> None:
    Member.objects.filter(pk=member.pk).update(totp_secret=new_totp_secret())

    result = sign_in(store, code="000000", two_factor=True)

    assert isinstance(result, Rejected)
    assert result.attempts_left == 4


def test_3_binding_is_confirmed_by_the_first_code(member: Member, store: FakeStateStore) -> None:
    secret = new_totp_secret()

    bound = confirm_totp_binding(
        member_id=member.pk,
        secret=secret,
        code=pyotp.TOTP(secret).at(MOMENT),
        at=MOMENT,
        state_store=store,
    )

    member.refresh_from_db()
    assert bound is True
    assert member.totp_secret == secret


def test_3_binding_refuses_a_wrong_code(member: Member, store: FakeStateStore) -> None:
    assert (
        confirm_totp_binding(
            member_id=member.pk,
            secret=new_totp_secret(),
            code="000000",
            at=MOMENT,
            state_store=store,
        )
        is False
    )


def test_login_threshold_comes_from_settings(member: Member, store: FakeStateStore) -> None:
    GlobalSettings.objects.filter(pk=1).update(login_failure_threshold=2)

    assert isinstance(sign_in(store, password="не тот"), Rejected)
    assert isinstance(sign_in(store, password="не тот"), Locked)


def test_50_code_field_is_absent_from_the_markup(client: Client) -> None:
    """Критерий 50: при AUTH_2FA_ENABLED=false разметки поля кода нет в ответе сервера."""
    body = client.get(reverse("login")).content.decode()

    assert 'name="password"' in body
    assert 'name="code"' not in body
    assert "Код из приложения" not in body


def test_3_missing_code_says_so_instead_of_blaming_the_password(
    client: Client, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Человек вводит верные почту и пароль — текст отказа обязан назвать настоящую причину."""
    monkeypatch.setattr(
        settings, "ENV", dataclasses.replace(settings.ENV, auth_2fa_enabled=True)
    )

    response = client.post(reverse("login"), {"email": EMAIL, "password": PASSWORD})

    body = response.content.decode()
    assert "AUTH_2FA_ENABLED" in body
    assert "Проверьте почту и пароль" not in body


def test_50_code_field_appears_when_the_second_factor_is_on(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "ENV", dataclasses.replace(settings.ENV, auth_2fa_enabled=True)
    )

    body = client.get(reverse("login")).content.decode()

    assert 'name="code"' in body


def test_3_login_screen_signs_a_member_in(client: Client, member: Member) -> None:
    response = client.post(reverse("login"), {"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 302
    assert client.get(reverse("overview")).status_code == 200


def test_3_anonymous_visitor_is_sent_to_the_login_screen(client: Client) -> None:
    """Перенаправление, а не отказ: человек не вошёл, и ему есть куда идти."""
    response = client.get(reverse("domain-list"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]
    # Адрес запомнен: ссылка из уведомления (8.4) после входа откроется сама.
    assert "next" in response["Location"]


def test_3_logout_ends_the_session(client: Client, member: Member) -> None:
    client.force_login(member)

    client.post(reverse("logout"))

    assert client.get(reverse("overview")).status_code == 302
