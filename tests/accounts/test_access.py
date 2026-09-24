"""Доступ по ролям — ТЗ 3, 3.1, критерии 29, 61, 87.

Проверяется ограничение, а не спрятанный пункт меню: спрятать ссылку — не значит закрыть доступ.
"""

from datetime import UTC, datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.accounts.access import admin_required, member_required, navigation
from apps.accounts.models import Member, Role

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)


def make_member(email: str, role: str = Role.OBSERVER, **extra: object) -> Member:
    return Member.objects.create_member(
        email=email, name="Участник", created_at=MOMENT, role=role, **extra
    )


def open_screen(request: HttpRequest) -> HttpResponse:
    return HttpResponse("экран")


def request_from(member: Member | None) -> HttpRequest:
    request = RequestFactory().get("/domains/")
    request.user = member if member is not None else AnonymousUser()  # type: ignore[assignment]
    return request


class AnonymousUser:
    """Ровно то, что кладёт в запрос промежуточный слой Django для неизвестного посетителя."""

    is_authenticated = False


def test_3_anonymous_visitor_is_refused() -> None:
    with pytest.raises(PermissionDenied):
        member_required(open_screen)(request_from(None))


def test_3_observer_reads_domains() -> None:
    observer = make_member("watch@contoso.ru")

    response = member_required(open_screen)(request_from(observer))

    assert response.status_code == 200


def test_3_observer_has_no_access_to_the_members_screen() -> None:
    """ТЗ 3: «Наблюдатель вообще не имеет доступа к этому разделу» (критерии 29, 87)."""
    observer = make_member("watch@contoso.ru")

    with pytest.raises(PermissionDenied):
        admin_required(open_screen)(request_from(observer))


def test_3_administrator_has_access_to_the_members_screen() -> None:
    admin = make_member("boss@contoso.ru", role=Role.ADMIN)

    assert admin_required(open_screen)(request_from(admin)).status_code == 200


def test_3_1_navigation_hides_the_members_item_from_an_observer() -> None:
    observer = make_member("watch@contoso.ru")
    admin = make_member("boss@contoso.ru", role=Role.ADMIN)

    assert navigation(request_from(observer)) == {"is_admin": False}
    assert navigation(request_from(admin)) == {"is_admin": True}


def test_navigation_survives_a_request_without_a_user() -> None:
    """Контекстный процессор зовут и на странице ошибки, где участника в запросе может не быть."""
    assert navigation(RequestFactory().get("/domains/")) == {"is_admin": False}
