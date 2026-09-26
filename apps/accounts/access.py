"""Ограничение доступа по ролям — ТЗ 3, 3.1, критерии 29, 61, 87.

Роль читается из участника, а не из кэша. 11.12 предлагает кэшировать её на 30 секунд, но
`request.user` Django и так приходит из БД на каждом запросе, поэтому кэш здесь не убрал бы ни
одного обращения, а отзыв доступа сделал бы медленнее. Критерий 61 («доступ прекращается не позже
чем через 30 секунд») выполняется строго лучше: отключение действует немедленно.

TODO(ТЗ 11.12): если профилирование покажет, что обращение за участником дорогое, ключ
`cache:user:{id}` вводится вместе с загрузкой участника — но только с немедленным удалением
при смене роли и отключении, как требует 11.12.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.urls import reverse

from apps.accounts.models import Role

type View = Callable[..., HttpResponse]


def member_required(view: View) -> View:
    """Экран доступен только участнику рабочего пространства.

    Анонимный запрос ведёт на вход с запомненным адресом: человек, открывший ссылку из
    уведомления (8.4), после входа попадает именно на неё, а не на обзор.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("login"))
        return view(request, *args, **kwargs)

    return wrapper


def admin_required(view: View) -> View:
    """Экран доступен только администратору.

    ТЗ 3: «Наблюдатель вообще не имеет доступа к этому разделу». Проверка живёт здесь, а не в
    шаблоне: спрятанный пункт меню не является ограничением доступа (критерии 29, 87).

    Наблюдатель получает именно отказ, а не вход: он уже вошёл, и предлагать ему войти снова
    значило бы врать о причине.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("login"))
        if request.user.role != Role.ADMIN:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def navigation(request: HttpRequest) -> dict[str, object]:
    """Контекст шаблонов: какие пункты меню показывать и какие числа у них стоят.

    Отдельно от проверок выше намеренно. Здесь решается только видимость, а доступ ограничивают
    декораторы: если однажды эти два места разойдутся, спрятанный пункт всё равно не откроется.

    Счётчики считаются только для вошедшего: на экране входа меню нет вовсе, и лишние запросы
    там ни к чему.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {"is_admin": False, "counts": None}

    # Импорт внутри функции: `access` зовут из настроек шаблонов, и на уровне модуля он потянул
    # бы приложения до того, как реестр Django их загрузит.
    from apps.catalog.selectors.health import install_warnings
    from apps.catalog.selectors.navigation import navigation_counts

    return {
        "is_admin": user.role == Role.ADMIN,
        "counts": navigation_counts(),
        # Состояния, в которых продукт цел, но не делает своего дела. Показываются на каждом
        # экране: пустой реестр сам по себе ничего не объясняет.
        "install_warnings": install_warnings(),
    }
