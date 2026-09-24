"""Первый администратор установки — ТЗ 3.

ТЗ описывает три способа создания участника, и все они требуют уже вошедшего администратора.
Эта операция закрывает дыру в начале: без неё в свежую установку не войти никогда.
"""

from datetime import UTC, datetime

import pytest

from apps.accounts.models import Member, Role
from apps.accounts.services.bootstrap import (
    AdminCreated,
    AdminNotCreated,
    create_first_admin,
)

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
PASSWORD = "tuman-9-krisa-vody"


def create(**extra: object) -> object:
    fields: dict[str, object] = {
        "email": "a.kirilova@contoso.ru",
        "name": "Анна Кирилова",
        "password": PASSWORD,
        "at": MOMENT,
    }
    fields.update(extra)
    return create_first_admin(**fields)  # type: ignore[arg-type]


def test_3_first_admin_is_created_and_can_sign_in() -> None:
    result = create()

    assert isinstance(result, AdminCreated)
    member = Member.objects.get(pk=result.member_id)
    assert member.role == Role.ADMIN
    assert member.check_password(PASSWORD)
    # Пароль задан самим человеком, менять его при первом входе незачем.
    assert member.must_change_password is False


def test_3_second_admin_is_not_created_this_way() -> None:
    """Это начальная установка, а не обходной путь мимо предохранителей 3.1."""
    create()

    result = create(email="other@contoso.ru")

    assert isinstance(result, AdminNotCreated)
    assert "уже есть администратор" in result.reason
    assert Member.objects.count() == 1


def test_3_disabled_admin_does_not_block_the_bootstrap() -> None:
    """Установка без действующего администратора необратима — её надо уметь развязать."""
    first = create()
    assert isinstance(first, AdminCreated)
    Member.objects.filter(pk=first.member_id).update(is_active=False)

    result = create(email="rescue@contoso.ru")

    assert isinstance(result, AdminCreated)


def test_3_short_password_is_refused() -> None:
    result = create(password="короткий")

    assert isinstance(result, AdminNotCreated)
    assert "короче" in result.reason


def test_3_duplicate_email_is_refused() -> None:
    Member.objects.create_member(
        email="a.kirilova@contoso.ru", name="Анна", created_at=MOMENT, role=Role.OBSERVER
    )

    result = create()

    assert isinstance(result, AdminNotCreated)
    assert "уже есть" in result.reason


def test_3_createsuperuser_creates_an_administrator() -> None:
    """`manage.py createsuperuser` — рефлекс любого, кто знает Django.

    «Суперпользователя» в продукте нет: роли ровно две. Команда заводит обычного администратора
    по тем же правилам, что и `create_admin`.
    """
    member = Member.objects.create_superuser(
        email="a.kirilova@contoso.ru", name="Анна Кирилова", password=PASSWORD
    )

    assert member.role == Role.ADMIN
    assert member.check_password(PASSWORD)


def test_3_createsuperuser_refuses_a_second_administrator() -> None:
    Member.objects.create_superuser(
        email="a.kirilova@contoso.ru", name="Анна Кирилова", password=PASSWORD
    )

    with pytest.raises(ValueError, match="уже есть администратор"):
        Member.objects.create_superuser(
            email="second@contoso.ru", name="Второй", password=PASSWORD
        )


def test_email_is_normalised() -> None:
    result = create(email="  A.Kirilova@Contoso.ru  ")

    assert isinstance(result, AdminCreated)
    assert result.email == "a.kirilova@contoso.ru"
