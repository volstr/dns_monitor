"""
Доступ по ролям — ТЗ 3, 3.1, критерии 29, 61, 87.

Проверяется ограничение, а не спрятанный пункт меню: спрятать ссылку — не значит закрыть доступ.
"""

from datetime import UTC, datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.accounts.access import AdminView, MemberView, navigation
from apps.accounts.models import Member, Role

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)


def make_member(email: str, role: str = Role.OBSERVER, **extra: object) -> Member:
    return Member.objects.create_member(
        email=email, name='Участник', created_at=MOMENT, role=role, **extra
    )


class MemberScreen(MemberView):
    def get(self, request: HttpRequest) -> HttpResponse:
        del request
        return HttpResponse('экран')


class AdminScreen(AdminView):
    def get(self, request: HttpRequest) -> HttpResponse:
        del request
        return HttpResponse('экран')


def request_from(member: Member | None) -> HttpRequest:
    request = RequestFactory().get('/domains/')
    request.user = member if member is not None else AnonymousUser()  # type: ignore[assignment]
    return request


class AnonymousUser:
    """Ровно то, что кладёт в запрос промежуточный слой Django для неизвестного посетителя."""

    is_authenticated = False


def test_3_anonymous_visitor_is_sent_to_the_login_screen() -> None:
    """
    Не отказ, а вход: человек не вошёл, и ему есть куда идти.

    Адрес запоминается — ссылка из уведомления (8.4) после входа откроется сама.
    """
    response = MemberScreen.as_view()(request_from(None))

    assert response.status_code == 302
    assert response['Location'].startswith('/login/')
    assert 'next=' in response['Location']


def test_3_observer_reads_domains() -> None:
    observer = make_member('watch@contoso.ru')

    response = MemberScreen.as_view()(request_from(observer))

    assert response.status_code == 200


def test_3_observer_has_no_access_to_the_members_screen() -> None:
    """
    ТЗ 3: «Наблюдатель вообще не имеет доступа к этому разделу» (критерии 29, 87).

    Здесь именно отказ, а не вход: наблюдатель уже вошёл, и предлагать ему войти снова значило
    бы соврать о причине.
    """
    observer = make_member('watch@contoso.ru')

    with pytest.raises(PermissionDenied):
        AdminScreen.as_view()(request_from(observer))


def test_3_administrator_has_access_to_the_members_screen() -> None:
    admin = make_member('boss@contoso.ru', role=Role.ADMIN)

    assert AdminScreen.as_view()(request_from(admin)).status_code == 200


def test_3_anonymous_visitor_on_an_admin_screen_is_also_sent_to_login() -> None:
    """
    Не `PermissionDenied`: анонимный посетитель не знает, что он не администратор.

    Раньше это обеспечивалось повторённой проверкой в `admin_required`; теперь — наследованием
    `AdminView` от `MemberView`, и проверка стоит здесь, чтобы разойтись они не могли.
    """
    response = AdminScreen.as_view()(request_from(None))

    assert response.status_code == 302
    assert response['Location'].startswith('/login/')


def test_3_1_navigation_hides_the_members_item_from_an_observer() -> None:
    observer = make_member('watch@contoso.ru')
    admin = make_member('boss@contoso.ru', role=Role.ADMIN)

    assert navigation(request_from(observer))['is_admin'] is False
    assert navigation(request_from(admin))['is_admin'] is True


def test_nav_counts_are_prepared_for_a_member() -> None:
    """Числа у пунктов меню — часть макета (Q-E5), считаются здесь же."""
    admin = make_member('boss@contoso.ru', role=Role.ADMIN)

    counts = navigation(request_from(admin))['counts']

    assert counts is not None
    assert counts.members == 1  # type: ignore[union-attr]


def test_navigation_survives_a_request_without_a_user() -> None:
    """
    Контекстный процессор зовут и на странице ошибки, где участника в запросе может не быть.

    Счётчики там не считаются: меню на экране входа нет, и лишние запросы ни к чему.
    """
    context = navigation(RequestFactory().get('/domains/'))

    assert context['is_admin'] is False
    assert context['counts'] is None
