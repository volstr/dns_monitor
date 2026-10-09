"""
Адрес клиента за обратным прокси — ТЗ 11.11, решение Q-D50.

Счётчик неудачных входов живёт по ключу `auth:fail:{хеш логина}:{ip}`. За обратным прокси
`REMOTE_ADDR` — это адрес **прокси**, один на всех, и пара вырождается в «логин»: любой
желающий блокирует чужую учётную запись, перебирая её пароль откуда угодно, а владелец не
входит и со своего адреса.

Поэтому адрес берётся из `X-Real-IP`, который `deploy/nginx.conf` перезаписывает на каждом
запросе. Доверие держится на двух условиях сразу, и оба проверяются здесь: заголовок
принимается, а `REMOTE_ADDR` остаётся запасным вариантом там, где прокси нет.
"""

import pytest
from django.test import RequestFactory

from apps.accounts.views import _client_ip

PROXY = '172.19.0.7'
CLIENT = '203.0.113.7'


@pytest.fixture
def factory() -> RequestFactory:
    return RequestFactory()


def test_11_11_address_behind_a_proxy_is_the_client_not_the_proxy(
    factory: RequestFactory,
) -> None:
    """Проверено на стенде: без этого в ключе стоял адрес контейнера nginx."""
    request = factory.get('/login/', REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=CLIENT)

    assert _client_ip(request) == CLIENT


def test_11_11_without_a_proxy_the_peer_address_is_used(factory: RequestFactory) -> None:
    """Режим разработчика: `runserver` без nginx, заголовка нет."""
    request = factory.get('/login/', REMOTE_ADDR=CLIENT)

    assert _client_ip(request) == CLIENT


def test_11_11_address_is_never_empty(factory: RequestFactory) -> None:
    """
    Пустой адрес сделал бы ключ `auth:fail:{хеш}:` общим для всех.

    Случай не выдуманный: `REMOTE_ADDR` отсутствует, например, у запроса по сокету.
    """
    request = factory.get('/login/')
    request.META.pop('REMOTE_ADDR', None)

    assert _client_ip(request) == 'unknown'
