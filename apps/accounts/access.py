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

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from apps.accounts.models import Role

type View = Callable[..., HttpResponse]


def member_required(view: View) -> View:
    """Экран доступен только участнику рабочего пространства.

    TODO(ТЗ 3): анонимный запрос пока получает 403. Перенаправление на экран входа появится
    вместе с самим экраном — раздел 3 стоит последним в порядке реализации из ТЗ, и ссылка на
    несуществующий маршрут была бы хуже честного отказа.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_authenticated:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def admin_required(view: View) -> View:
    """Экран доступен только администратору.

    ТЗ 3: «Наблюдатель вообще не имеет доступа к этому разделу». Проверка живёт здесь, а не в
    шаблоне: спрятанный пункт меню не является ограничением доступа (критерии 29, 87).
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_authenticated:
            raise PermissionDenied
        if request.user.role != Role.ADMIN:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def navigation(request: HttpRequest) -> dict[str, bool]:
    """Контекст шаблонов: какие пункты меню показывать.

    Отдельно от проверок выше намеренно. Здесь решается только видимость, а доступ ограничивают
    декораторы: если однажды эти два места разойдутся, спрятанный пункт всё равно не откроется.
    """
    user = getattr(request, "user", None)
    is_admin = bool(user and user.is_authenticated and user.role == Role.ADMIN)
    return {"is_admin": is_admin}
