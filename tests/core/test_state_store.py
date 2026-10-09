"""
Хранилище рабочего состояния — ТЗ 11.11, 11.13, критерии 54, 56, 57.

Тестовая реализация проверяется часами, боевая — настоящим Redis из компоуза. Тело проверок
общее: оба обязаны вести себя одинаково, иначе тесты на подделке ничего не стоят.
"""

from datetime import UTC, datetime, timedelta

import pytest
import redis
from django.conf import settings as django_settings

from core.adapters.fake_clock import FakeClock
from core.adapters.fake_state_store import FakeStateStore, StateStoreUnavailable
from core.adapters.redis_state import RedisStateStore
from core.ports.state_store import (
    SCHEDULER_LOCK,
    StateKey,
    domain_cooldown,
    group_cooldown,
    merge_window,
    scheduler_lock,
)

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)
REDIS_URL = 'redis://redis:6379'
#: Логическая база берётся из настроек теста, а не пишется числом. Числом здесь стоял ноль —
#: то есть рабочая база запущенного приложения: тесты ходили в неё, и `flushdb` в проверке
#: критерия 55 стирал бы живые сессии и блокировки разработчика.
STATE_DB = django_settings.ENV.redis_db_state


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
    assert scheduler_lock(42).name == 'sched:lock:domain:42'
    assert domain_cooldown(42).name == 'check:cooldown:domain:42'
    assert group_cooldown(7).name == 'check:cooldown:group:7'


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


def test_8_6_accumulator_collects_items_and_counts_them(store: FakeStateStore) -> None:
    key = merge_window('telegram', 'not_allowed')

    assert store.accumulate(key, 'pay-gw.contoso.ru') == 1
    assert store.accumulate(key, 'api.contoso.ru') == 2
    assert store.collected(key) == ('pay-gw.contoso.ru', 'api.contoso.ru')


def test_8_6_window_is_fixed_and_not_extended_by_new_events(
    store: FakeStateStore, clock: FakeClock
) -> None:
    # 11.11: срок ставится при первом добавлении. Иначе поток событий продлевал бы окно
    # бесконечно и сводка не ушла бы никогда.
    key = merge_window('telegram', 'not_allowed')
    store.accumulate(key, 'первый')

    clock.advance(timedelta(seconds=60))
    store.accumulate(key, 'второй')
    clock.advance(timedelta(seconds=31))

    assert store.collected(key) == ()


def test_8_6_released_accumulator_starts_over(store: FakeStateStore) -> None:
    key = merge_window('telegram', 'not_allowed')
    store.accumulate(key, 'первый')

    store.release(key)

    assert store.collected(key) == ()
    assert store.accumulate(key, 'второй') == 1


@pytest.fixture
def redis_store() -> RedisStateStore:
    store = RedisStateStore.from_url(REDIS_URL, db=STATE_DB)
    store.ping()
    return store


@pytest.fixture
def redis_inspector() -> redis.Redis:
    """Отдельный клиент: срок жизни ключа читается снаружи, а не через приватное поле адаптера."""
    return redis.Redis.from_url(REDIS_URL, db=STATE_DB, decode_responses=True)


@pytest.mark.parametrize('key_number', [910_001])
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
    key = StateKey('sched:lock:domain:910002', timedelta(seconds=30))
    redis_store.release(key)

    assert redis_store.acquire(key) is True
    assert redis_store.acquire(key) is False
    redis_store.release(key)
    assert redis_store.is_held(key) is False


def test_8_6_real_redis_accumulates_with_a_lifetime(
    redis_store: RedisStateStore, redis_inspector: redis.Redis
) -> None:
    key = merge_window('telegram', 'probe_event')
    redis_store.release(key)

    assert redis_store.accumulate(key, 'pay-gw.contoso.ru') == 1
    assert redis_store.accumulate(key, 'api.contoso.ru') == 2

    assert redis_store.collected(key) == ('pay-gw.contoso.ru', 'api.contoso.ru')
    # Ключ накопителя тоже обязан иметь срок жизни (11.11, критерий 54).
    assert redis_inspector.ttl(key.name) > 0
    redis_store.release(key)


def test_real_redis_ping_fails_when_unreachable() -> None:
    store = RedisStateStore.from_url('redis://127.0.0.1:6399', db=0)

    with pytest.raises(redis.exceptions.ConnectionError):
        store.ping()


@pytest.mark.django_db
def test_55_a_full_redis_wipe_costs_counters_but_not_data(
    redis_store: RedisStateStore, redis_inspector: redis.Redis
) -> None:
    """
    Критерий 55: полная очистка Redis → домены, события, реестр и участники целы; счётчики,
    кулдауны и блокировки сброшены.

    Это первое правило 11.11, проверенное с двух сторон сразу. «Redis не источник истины» —
    утверждение не о намерении, а о размещении: всё, что обязано пережить перезапуск, лежит в
    PostgreSQL. Обратная половина не менее важна и записана в том же пункте: потеря **не**
    безобидна, она сбрасывает счётчики, кулдауны и блокировки и требует разбора. Поэтому тест
    проверяет и то, что данные целы, и то, что состояние действительно потеряно: «ничего не
    изменилось» означало бы, что состояние где-то продублировано.
    """
    from apps.accounts.models import Member
    from apps.catalog.models import Domain
    from apps.monitoring.models import AddressRegistryEntry, Event, EventType

    domain = Domain.objects.create(name='pay-gw.contoso.ru')
    Event.objects.create(
        domain=domain, occurred_at=MOMENT, event_type=EventType.WATCH_STARTED, payload={}
    )
    AddressRegistryEntry.objects.create(
        domain=domain, address='10.20.1.14', first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    member = Member.objects.create_member(
        email='boss@contoso.ru', name='Анна Кирилова', created_at=MOMENT
    )

    lock = scheduler_lock(domain.pk)
    cooldown = domain_cooldown(domain.pk)
    redis_store.acquire(lock)
    redis_store.acquire(cooldown)
    assert redis_store.is_held(lock) is True

    redis_inspector.flushdb()

    assert Domain.objects.filter(pk=domain.pk).exists()
    assert Event.objects.filter(domain=domain).count() == 1
    assert AddressRegistryEntry.objects.filter(domain=domain).count() == 1
    assert Member.objects.filter(pk=member.pk).exists()
    assert redis_store.is_held(lock) is False
    assert redis_store.is_held(cooldown) is False
