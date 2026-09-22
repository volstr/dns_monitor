"""Хранилище рабочего состояния — ТЗ 11.11, 11.13, критерии 54, 56, 57.

Тестовая реализация проверяется часами, боевая — настоящим Redis из компоуза. Тело проверок
общее: оба обязаны вести себя одинаково, иначе тесты на подделке ничего не стоят.
"""

from datetime import UTC, datetime, timedelta

import pytest
import redis

from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore, StateStoreUnavailable
from core.adapters.redis_state import RedisStateStore
from core.ports.state_store import (
    SCHEDULER_LOCK,
    StateKey,
    domain_cooldown,
    group_cooldown,
    scheduler_lock,
)

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
REDIS_URL = "redis://redis:6379"
STATE_DB = 0


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def store(clock: FakeClock) -> FakeStateStore:
    return FakeStateStore(clock)


def test_11_11_key_carries_its_own_lifetime() -> None:
    # Ключ без срока жизни нельзя выразить: StateKey несёт ttl, а записи по строке нет вовсе.
    assert scheduler_lock(1).ttl == SCHEDULER_LOCK
    assert domain_cooldown(1).ttl == timedelta(seconds=30)
    assert group_cooldown(1).ttl == timedelta(seconds=60)


def test_11_11_key_names_follow_the_table() -> None:
    assert scheduler_lock(42).name == "sched:lock:domain:42"
    assert domain_cooldown(42).name == "check:cooldown:domain:42"
    assert group_cooldown(7).name == "check:cooldown:group:7"


def test_11_1_second_instance_does_not_take_a_held_lock(store: FakeStateStore) -> None:
    key = scheduler_lock(1)

    assert store.acquire(key) is True
    assert store.acquire(key) is False


def test_11_11_lock_is_released_explicitly(store: FakeStateStore) -> None:
    key = scheduler_lock(1)
    store.acquire(key)

    store.release(key)

    assert store.acquire(key) is True


def test_11_11_lock_expires_by_itself(store: FakeStateStore, clock: FakeClock) -> None:
    # Срок жизни — страховка на случай падения экземпляра посреди проверки (11.11).
    key = scheduler_lock(1)
    store.acquire(key)

    clock.advance(SCHEDULER_LOCK + timedelta(seconds=1))

    assert store.is_held(key) is False
    assert store.acquire(key) is True


def test_47_cooldown_rejects_the_second_attempt(store: FakeStateStore, clock: FakeClock) -> None:
    key = group_cooldown(1)

    assert store.acquire(key) is True
    assert store.acquire(key) is False
    clock.advance(timedelta(seconds=61))
    assert store.acquire(key) is True


def test_11_13_unavailable_store_raises(clock: FakeClock) -> None:
    # Недоступность — авария, а не режим пониженной функциональности.
    store = FakeStateStore(clock, available=False)

    with pytest.raises(StateStoreUnavailable):
        store.ping()
    with pytest.raises(StateStoreUnavailable):
        store.acquire(scheduler_lock(1))


@pytest.fixture
def redis_store() -> RedisStateStore:
    store = RedisStateStore.from_url(REDIS_URL, db=STATE_DB)
    store.ping()
    return store


@pytest.fixture
def redis_inspector() -> redis.Redis:
    """Отдельный клиент: срок жизни ключа читается снаружи, а не через приватное поле адаптера."""
    return redis.Redis.from_url(REDIS_URL, db=STATE_DB, decode_responses=True)


@pytest.mark.parametrize("key_number", [910_001])
def test_54_real_redis_sets_a_lifetime_on_every_key(
    redis_store: RedisStateStore, redis_inspector: redis.Redis, key_number: int
) -> None:
    """Критерий 54: у каждого ключа задан срок жизни."""
    key = scheduler_lock(key_number)
    redis_store.release(key)

    assert redis_store.acquire(key) is True
    ttl = redis_inspector.ttl(key.name)

    # Положительный ttl означает, что срок задан: -1 у Redis — «без срока».
    assert ttl > 0
    assert ttl <= int(SCHEDULER_LOCK.total_seconds())
    redis_store.release(key)


def test_real_redis_behaves_like_the_fake(redis_store: RedisStateStore) -> None:
    key = StateKey("sched:lock:domain:910002", timedelta(seconds=30))
    redis_store.release(key)

    assert redis_store.acquire(key) is True
    assert redis_store.acquire(key) is False
    redis_store.release(key)
    assert redis_store.is_held(key) is False


def test_real_redis_ping_fails_when_unreachable() -> None:
    store = RedisStateStore.from_url("redis://127.0.0.1:6399", db=0)

    with pytest.raises(redis.exceptions.ConnectionError):
        store.ping()
