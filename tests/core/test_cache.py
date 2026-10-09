"""
Кэш и его отделённость от рабочего состояния — ТЗ 11.11, 11.12; критерии 55, 62.

Тестовая реализация проверяется часами, боевая — настоящим Redis из компоуза. Тело проверок
общее: обе обязаны вести себя одинаково, иначе тесты на подделке ничего не стоят.

Главное здесь не скорость, а **граница**. 11.12 разрешает сбрасывать `cache:*` целиком в любой
момент; 11.11 запрещает делать это с рабочим состоянием. Граница держится тем, что это разные
логические базы и разные порты, — и ровно это проверяется ниже. Стоит однажды положить
блокировку планировщика «в кэш», и критерий 62 перестанет выполняться молча.
"""

from datetime import UTC, datetime, timedelta

import pytest
import redis
from django.conf import settings as django_settings

from core.adapters.fake_cache import FakeCache
from core.adapters.fake_clock import FakeClock
from core.adapters.redis_cache import RedisCache
from core.adapters.redis_state import RedisStateStore
from core.ports.cache import (
    PREFIX,
    USER_TTL,
    CacheKey,
    overview_view,
    settings_of_domain,
    user,
)
from core.ports.state_store import login_failures, scheduler_lock

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
REDIS_URL = 'redis://redis:6379'
#: Логические базы берутся из настроек теста, а не пишутся числами: число легко разойдётся
#: с окружением, и проверка ушла бы в рабочую базу запущенного приложения.
STATE_DB = django_settings.ENV.redis_db_state
CACHE_DB = django_settings.ENV.redis_db_cache


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def cache(clock: FakeClock) -> FakeCache:
    return FakeCache(clock)


def test_11_12_every_key_lives_under_the_cache_prefix() -> None:
    """
    Сброс идёт по шаблону `cache:*`, поэтому ключ вне префикса он молча не удалит.

    Проверяется по всем построителям сразу: забытый префикс у одного означал бы ключ, который
    не исчезает никогда и однажды переживёт причину своего появления (11.11).
    """
    keys = (
        settings_of_domain(7, 3),
        overview_view(),
        user(4),
    )

    assert all(key.name.startswith(PREFIX) for key in keys)


def test_11_12_version_in_the_key_makes_the_old_value_unreachable(cache: FakeCache) -> None:
    """
    Настройки и белый список обновляются **версией**, а не удалением ключа.

    ТЗ объясняет, почему именно так: при нескольких экземплярах сервиса удаление подвержено
    гонке, а смена версии просто делает старый ключ недостижимым, и он уходит по сроку.
    """
    cache.put(settings_of_domain(7, 1), 'период 5 минут')

    assert cache.get(settings_of_domain(7, 2)) is None
    # Старое значение не удалено — оно именно недостижимо и умрёт само.
    assert cache.get(settings_of_domain(7, 1)) == 'период 5 минут'


def test_11_12_user_key_is_forgotten_immediately(cache: FakeCache) -> None:
    """Отозванный доступ не должен переживать своё отзывание (11.12, критерий 61)."""
    cache.put(user(4), 'admin')

    cache.forget(user(4))

    assert cache.get(user(4)) is None


def test_11_12_value_does_not_outlive_its_ttl(cache: FakeCache, clock: FakeClock) -> None:
    cache.put(user(4), 'admin')

    clock.advance(USER_TTL + timedelta(seconds=1))

    assert cache.get(user(4)) is None


def test_11_12_flush_takes_the_whole_cache(cache: FakeCache) -> None:
    cache.put(settings_of_domain(7, 1), 'a')
    cache.put(overview_view(), 'b')

    assert cache.flush() == 2
    assert cache.keys() == frozenset()


# ─────────────── настоящий Redis: граница между кэшем и состоянием ───────────────


@pytest.fixture
def redis_cache() -> RedisCache:
    return RedisCache.from_url(REDIS_URL, db=CACHE_DB)


@pytest.fixture
def redis_state() -> RedisStateStore:
    store = RedisStateStore.from_url(REDIS_URL, db=STATE_DB)
    store.ping()
    return store


@pytest.fixture
def cache_inspector() -> redis.Redis:
    """Отдельный клиент: срок жизни читается снаружи, а не через приватное поле адаптера."""
    return redis.Redis.from_url(REDIS_URL, db=CACHE_DB, decode_responses=True)


def test_11_12_real_cache_sets_a_lifetime_on_every_key(
    redis_cache: RedisCache, cache_inspector: redis.Redis
) -> None:
    """
    Бессрочный ключ кэша хуже отсутствующего: `volatile-lru` его не вытеснит (Q-A1).

    То есть именно бессрочный ключ и переживёт нехватку памяти, вытеснив вместо себя полезные.
    """
    key = settings_of_domain(920_001, 1)
    redis_cache.put(key, 'значение')

    ttl = cache_inspector.ttl(key.name)

    # -1 у Redis означает «без срока», -2 — «ключа нет».
    assert ttl > 0
    redis_cache.flush()


def test_62_flushing_the_cache_does_not_touch_the_working_state(
    redis_cache: RedisCache, redis_state: RedisStateStore
) -> None:
    """
    Критерий 62: сброшено пространство `cache:*` → блокировки планировщика и счётчики попыток
    не затронуты.

    Это и есть смысл двух логических баз. Проверяется настоящим Redis, а не подделкой: на
    подделке «разные базы» — это два словаря, и перепутать их нельзя по построению, тогда как
    в боевой конфигурации достаточно одной неверной цифры в переменной окружения.
    """
    lock = scheduler_lock(920_002)
    counter = login_failures('хеш-логина', '10.0.0.1')
    redis_state.release(lock)
    redis_state.release(counter)
    redis_state.acquire(lock)
    redis_state.remember(counter, '3')
    redis_cache.put(settings_of_domain(920_002, 1), 'период 5 минут')
    redis_cache.put(overview_view(), 'сводка')

    deleted = redis_cache.flush()

    assert deleted >= 2
    assert redis_cache.get(overview_view()) is None
    # Рабочее состояние на месте: блокировка занята, счётчик читается.
    assert redis_state.is_held(lock) is True
    assert redis_state.recall(counter) == '3'
    redis_state.release(lock)
    redis_state.release(counter)


def test_11_12_cache_and_state_do_not_share_a_key_space(redis_cache: RedisCache) -> None:
    """
    Одно и то же имя в двух базах — два разных ключа.

    Без этого разделение было бы декоративным: общего пространства ключей `CLAUDE.md` не
    допускает, и проверить это можно только настоящим Redis.
    """
    name = f'{PREFIX}probe:920003'
    redis_cache.put(CacheKey(name, timedelta(seconds=30)), 'из кэша')
    state_side = redis.Redis.from_url(REDIS_URL, db=STATE_DB, decode_responses=True)

    assert state_side.get(name) is None
    redis_cache.flush()
