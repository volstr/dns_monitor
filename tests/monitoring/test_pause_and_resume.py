"""
Возобновление после паузы — ТЗ 7.4, 11.1, 11.13; критерии 35, 36, 37, 66.

Все четыре критерия — об одном страхе: после перерыва накопившиеся расхождения хлынут наружу
разом. ТЗ отвечает на него **одним** правилом 7.4 в редакции Q-D6: проверка считается базовой,
если успешных ещё не было или пауза оказалась дольше порога актуальности. Базовая молча ставит
отправную точку, событий изменения не порождает — значит и уведомлять не о чем.

Важно, что правило одно на все случаи. Домен выключали руками (35, 37), стоял весь сервис (36),
лежал Redis (66) — механизм срабатывает по **паузе**, а не по её причине, и знать причину ему не
нужно. Поэтому четыре критерия проверяются здесь рядом: порознь легко решить, что каждому нужен
свой предохранитель, и завести три лишних.

Сутки модельного времени прогоняются без `sleep`: момент передаётся явно, резолвер и хранилище
состояния подменяются портами.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, NotificationMode
from apps.monitoring.models import Event, EventType
from apps.monitoring.services.checking import run_check
from apps.monitoring.services.tick import tick
from apps.notifications.models import Notification
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore

pytestmark = pytest.mark.django_db

START = datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
THRESHOLD = timedelta(hours=24)
NAME = "pay-gw.contoso.ru"
BEFORE = ("10.20.1.14", "10.20.2.60")
AFTER = ("10.20.3.77", "10.20.3.78")


@pytest.fixture(autouse=True)
def notify_on_any_change() -> None:
    """
    Режим «при изменении адресов»: иначе проверять нечего.

    По умолчанию установка уведомляет только о выходе за список разрешённых, и «лавины нет»
    выполнялось бы само собой, независимо от 7.4. Здесь адреса **входят** в список — значит
    любое сообщение, которое всё-таки появится, придёт именно из-за смены набора.
    """
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.ADDRESS_CHANGE)


@pytest.fixture
def store() -> FakeStateStore:
    return FakeStateStore(FakeClock(START))


def make_domain(name: str = NAME, *, due_at: datetime | None = None) -> Domain:
    domain = Domain.objects.create(name=name, next_check_at=due_at)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def changes(domain: Domain) -> int:
    return Event.objects.filter(
        domain=domain,
        event_type__in=[EventType.ADDRESS_APPEARED, EventType.ADDRESS_GONE],
    ).count()


def test_35_domain_switched_off_longer_than_the_threshold_resumes_with_a_baseline() -> None:
    """
    Критерий 35: пауза дольше порога → проверка базовая, событий изменения нет.

    Набор за время простоя сменился целиком. Без правила 7.4 это дало бы два `ADDRESS_GONE` и
    два `ADDRESS_APPEARED` — про домен, за которым в это время никто не наблюдал.
    """
    domain = make_domain()
    resolver = FakeResolver({NAME: [answered(*BEFORE)]})
    run_check(domain_id=domain.pk, at=START, resolver=resolver)

    Domain.objects.filter(pk=domain.pk).update(watch_enabled=False)
    resolver.set(NAME, [answered(*AFTER)])
    Domain.objects.filter(pk=domain.pk).update(watch_enabled=True)

    report = run_check(
        domain_id=domain.pk, at=START + THRESHOLD + timedelta(minutes=1), resolver=resolver
    )

    assert report.is_baseline is True
    assert changes(domain) == 0
    assert Event.objects.filter(domain=domain, event_type=EventType.WATCH_STARTED).count() == 2


def test_37_domain_switched_off_for_less_than_the_threshold_resumes_normally() -> None:
    """
    Критерий 37: пауза короче порога → проверка обычная, базовой не считается.

    Обратная половина 35, и без неё правило было бы наполовину проверено: молчать после
    короткой отлучки нельзя, иначе смена адресов в этот промежуток пропадёт навсегда.
    """
    domain = make_domain()
    resolver = FakeResolver({NAME: [answered(*BEFORE)]})
    run_check(domain_id=domain.pk, at=START, resolver=resolver)

    Domain.objects.filter(pk=domain.pk).update(watch_enabled=False)
    resolver.set(NAME, [answered(*AFTER)])
    Domain.objects.filter(pk=domain.pk).update(watch_enabled=True)

    report = run_check(
        domain_id=domain.pk, at=START + THRESHOLD - timedelta(hours=1), resolver=resolver
    )

    assert report.is_baseline is False
    assert EventType.ADDRESS_APPEARED in report.events


def test_36_service_stopped_longer_than_the_threshold_starts_without_an_avalanche(
    store: FakeStateStore,
) -> None:
    """
    Критерий 36: сервис стоял дольше порога → базовые проверки по всем доменам, лавины нет.

    Три домена вместо одного — потому что критерий про лавину: проверять «нет уведомлений» на
    единственном домене бессмысленно, лавина начинается со второго.
    """
    names = [f"host-{index}.contoso.ru" for index in range(3)]
    domains = [make_domain(name, due_at=START) for name in names]
    resolver = FakeResolver({name: [answered(*BEFORE)] for name in names})
    tick(at=START, resolver=resolver, state_store=store, concurrency=1)
    Notification.objects.all().delete()

    for name in names:
        resolver.set(name, [answered(*AFTER)])
    resumed_at = START + THRESHOLD + timedelta(hours=2)
    Domain.objects.update(next_check_at=resumed_at)

    report = tick(at=resumed_at, resolver=resolver, state_store=store, concurrency=1)

    assert report.checked == 3
    assert sum(changes(domain) for domain in domains) == 0
    assert Notification.objects.count() == 0


def test_66_redis_outage_longer_than_the_threshold_ends_the_same_way(
    store: FakeStateStore,
) -> None:
    """
    Критерий 66: Redis восстановлен после простоя дольше порога → проверки базовые, лавины нет.

    Отдельного предохранителя для этого случая нет и быть не должно. Недоступность Redis по
    11.13 останавливает проверки — а дальше это ровно тот же перерыв, что и в 36, и работает
    то же правило 7.4. Значение имеет длина паузы, а не её причина.

    Потеря содержимого Redis при этом на решение не влияет: правило считает паузу по
    `last_successful_check_at`, то есть по PostgreSQL. По 11.11 Redis не источник истины, и
    базовость проверки не должна зависеть от того, пережил ли он перезапуск.
    """
    domain = make_domain(due_at=START)
    resolver = FakeResolver({NAME: [answered(*BEFORE)]})
    tick(at=START, resolver=resolver, state_store=store, concurrency=1)
    Notification.objects.all().delete()

    resolver.set(NAME, [answered(*AFTER)])
    resumed_at = START + THRESHOLD + timedelta(hours=3)
    Domain.objects.update(next_check_at=resumed_at)

    # Redis вернулся пустым: новый экземпляр хранилища, без единого ключа из прошлой жизни.
    restarted = FakeStateStore(FakeClock(resumed_at))
    tick(at=resumed_at, resolver=resolver, state_store=restarted, concurrency=1)

    assert changes(domain) == 0
    assert Notification.objects.count() == 0
