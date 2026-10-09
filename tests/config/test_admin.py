"""
Админка Django — инструмент разработчика, не часть продукта (Q-D35).

Главная проверка здесь — отрицательная: на боевой установке админки нет. Остальное о том, что
доступ к ней решает роль из ТЗ 3, а не таблица разрешений Django, которой в проекте нет.
"""

from datetime import UTC, datetime

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory

from apps.accounts.models import Member, Role
from apps.catalog.models import GlobalSettings
from config.admin import MemberAdmin, site

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)


def member(role: str = Role.ADMIN, **extra: object) -> Member:
    return Member.objects.create_member(
        email=f'{role}@contoso.ru', name='Участник', created_at=MOMENT, role=role, **extra
    )


def request_from(user: object) -> RequestFactory:
    request = RequestFactory().get('/admin/')
    request.user = user  # type: ignore[assignment]
    return request


class Anonymous:
    is_authenticated = False
    is_active = False


# ─────────────── главное: вне режима разработчика админки нет ───────────────


def test_admin_is_absent_outside_the_developer_mode(client: Client) -> None:
    """Подключена только в `config/settings/dev.py`. Тесты идут на настройках без неё."""
    assert 'django.contrib.admin' not in settings.INSTALLED_APPS
    assert client.get('/admin/').status_code == 404


def test_admin_route_appears_only_with_the_application(client: Client) -> None:
    """
    Маршрут заведён по наличию приложения, а не по флагу `DEBUG`.

    Разница существенная: `DEBUG` — переменная, которую можно случайно включить на боевой
    установке, а приложения там нет вовсе, и включать нечего.
    """
    assert client.get('/admin/login/').status_code == 404


# ─────────────── доступ решает роль, а не таблица разрешений ───────────────


def test_3_only_an_active_administrator_is_let_in() -> None:
    admin = member(Role.ADMIN)

    assert site.has_permission(request_from(admin)) is True


def test_3_observer_is_not_let_into_the_admin() -> None:
    """ТЗ 3: у наблюдателя нет доступа к разделам администратора."""
    observer = member(Role.OBSERVER)

    assert site.has_permission(request_from(observer)) is False


def test_3_1_a_deactivated_administrator_is_not_let_in() -> None:
    """Отключение — основной способ прекратить доступ (3.1), и админка не исключение."""
    disabled = member(Role.ADMIN, is_active=False)

    assert site.has_permission(request_from(disabled)) is False


def test_anonymous_is_not_let_in() -> None:
    assert site.has_permission(request_from(Anonymous())) is False


def test_observer_gets_a_refusal_not_a_login_form() -> None:
    """
    Наблюдатель уже вошёл: предлагать ему войти снова значило бы соврать о причине.

    Так же ведёт себя `AdminView` в приложении — одно поведение на оба случая.
    """
    observer = member(Role.OBSERVER)

    with pytest.raises(PermissionDenied):
        site.login(request_from(observer))


# ─────────────── предохранители, которые остаются и в админке ───────────────


def test_4_1_the_settings_row_cannot_be_added_or_deleted() -> None:
    """
    ТЗ 4.1: строка общих настроек одна на установку.

    `GlobalSettings.objects.get()` зовут из каждой проверки, и он упадёт и на второй строке, и
    на нулевой. Это не урезание прав, а защита от поломки, которую не увидишь из админки.
    """
    admin = member(Role.ADMIN)
    request = request_from(admin)
    model_admin = site._registry[GlobalSettings]

    assert model_admin.has_change_permission(request) is True
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_password_hash_is_shown_but_not_editable() -> None:
    """Иначе введённое в поле легло бы в базу как есть: паролем стал бы открытый текст."""
    assert 'password' in MemberAdmin.readonly_fields
