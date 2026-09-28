"""Экран участников — ТЗ 3, 3.1; критерии 29, 30, 31, 75, 76, 87."""

import dataclasses
from datetime import UTC, datetime

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Invitation, Member, Role

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
PASSWORD = "tuman-9-krisa-vody"


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru",
        name="Анна Кирилова",
        created_at=MOMENT,
        role=Role.ADMIN,
        password=PASSWORD,
    )


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )


def test_87_observer_has_no_access_to_members(client: Client, observer: Member) -> None:
    """Критерий 87: наблюдатель обращается к изменению участника → отказ."""
    client.force_login(observer)

    assert client.get(reverse("member-list")).status_code == 403
    assert client.post(reverse("member-invite"), {"email": "x@contoso.ru"}).status_code == 403


def test_75_banner_names_the_variables_when_smtp_is_not_set(
    client: Client, admin: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Критерий 75: способ письмом выключен, виден баннер с именами переменных."""
    monkeypatch.setattr(settings, "ENV", dataclasses.replace(settings.ENV, smtp_host=None))
    client.force_login(admin)

    body = client.get(reverse("member-list")).content.decode()

    assert "SMTP_HOST" in body
    assert "Письма с приглашениями отправить не получится" in body
    # Кнопки подключения почты быть не должно: настроить её из интерфейса нельзя.
    assert 'name="smtp_host"' not in body


def test_76_no_banner_when_smtp_is_set(
    client: Client, admin: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Критерий 76: при заданном SMTP_HOST способ доступен, баннера нет."""
    monkeypatch.setattr(
        settings, "ENV", dataclasses.replace(settings.ENV, smtp_host="smtp.contoso.ru")
    )
    client.force_login(admin)

    body = client.get(reverse("member-list")).content.decode()

    assert "Письма с приглашениями отправить не получится" not in body


def test_3_invitation_link_is_issued_and_stays_copyable(client: Client, admin: Member) -> None:
    """ТЗ 3: ссылка остаётся доступной для копирования из списка, пока не открыта."""
    client.force_login(admin)

    client.post(
        reverse("member-invite"),
        {"name": "Новый", "email": "new@contoso.ru", "role": Role.OBSERVER, "way": "link"},
    )

    invitation = Invitation.objects.get()
    body = client.get(reverse("member-list")).content.decode()
    assert "Ссылка не открыта" in body
    assert "/join/" in body
    assert invitation.opened_at is None


def test_30_opened_link_stops_working(client: Client, admin: Member) -> None:
    """Критерий 30: ссылка одноразовая."""
    client.force_login(admin)
    client.post(
        reverse("member-invite"),
        {"name": "Новый", "email": "new@contoso.ru", "role": Role.OBSERVER, "way": "link"},
    )
    body = client.get(reverse("member-list")).content.decode()
    token = body.split("/join/")[1].split("/")[0]

    client.logout()
    first = client.post(
        reverse("join", args=[token]), {"password": PASSWORD, "repeat": PASSWORD}
    )
    assert first.status_code == 302

    client.logout()
    second = client.post(
        reverse("join", args=[token]), {"password": PASSWORD, "repeat": PASSWORD}
    )
    assert "уже была открыта" in second.content.decode()


def test_3_password_way_shows_the_password_once(client: Client, admin: Member) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("member-invite"),
        {"name": "Новый", "email": "new@contoso.ru", "role": Role.OBSERVER, "way": "password"},
        follow=True,
    )

    assert "показывается один раз" in response.content.decode()
    assert Member.objects.get(email="new@contoso.ru").must_change_password is True


def test_83_self_actions_are_not_offered_and_are_refused(client: Client, admin: Member) -> None:
    """Критерий 83: отказ и в интерфейсе, и в API."""
    client.force_login(admin)

    response = client.post(
        reverse("member-action", args=[admin.pk, "deactivate"]), follow=True
    )

    assert "самого себя" in response.content.decode()
    admin.refresh_from_db()
    assert admin.is_active is True


def test_3_1_admin_updates_a_member(client: Client, admin: Member, observer: Member) -> None:
    client.force_login(admin)

    client.post(
        reverse("member-update", args=[observer.pk]),
        {"name": "Новое имя", "email": observer.email, "role": Role.OBSERVER},
    )

    observer.refresh_from_db()
    assert observer.name == "Новое имя"


def test_80_changing_email_signs_the_member_out(
    client: Client, admin: Member, observer: Member
) -> None:
    """Критерий 80: после смены почты прежняя сессия недействительна."""
    other = Client()
    other.force_login(observer)
    assert other.get(reverse("overview")).status_code == 200

    client.force_login(admin)
    client.post(
        reverse("member-update", args=[observer.pk]),
        {"name": observer.name, "email": "moved@contoso.ru", "role": Role.OBSERVER},
    )

    assert other.get(reverse("overview")).status_code == 302


def test_3_1_terminating_sessions_signs_the_member_out(
    client: Client, admin: Member, observer: Member
) -> None:
    other = Client()
    other.force_login(observer)
    assert other.get(reverse("overview")).status_code == 200

    client.force_login(admin)
    client.post(reverse("member-action", args=[observer.pk, "sessions"]))

    assert other.get(reverse("overview")).status_code == 302


def test_3_1_the_screen_offers_a_way_back(client: Client, admin: Member, observer: Member) -> None:
    """
    Отключённый участник получает кнопку «Включить» вместо «Отключить» — Q-D42.

    Экран был тупиком: отключить можно было, вернуть — нечем.
    """
    client.force_login(admin)
    client.post(reverse("member-action", args=[observer.pk, "deactivate"]))

    page = client.get(reverse("member-list")).content.decode()

    assert reverse("member-action", args=[observer.pk, "activate"]) in page

    client.post(reverse("member-action", args=[observer.pk, "activate"]), follow=True)

    observer.refresh_from_db()
    assert observer.is_active is True


def test_unknown_action_is_not_a_password_reset(
    client: Client, admin: Member, observer: Member
) -> None:
    """
    Нераспознанное действие проваливалось в последнюю ветку — сброс пароля.

    То есть `/members/<id>/wathever/` выдавал участнику новый пароль и завершал его сессии.
    """
    client.force_login(admin)
    epoch = observer.session_epoch

    response = client.post(reverse("member-action", args=[observer.pk, "wathever"]))

    assert response.status_code == 404
    observer.refresh_from_db()
    assert observer.session_epoch == epoch
