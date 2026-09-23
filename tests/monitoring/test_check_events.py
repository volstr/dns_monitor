"""События, инциденты и базовая проверка — ТЗ 6.1, 7.1, 7.3, 7.4.

Критерии 2, 3, 10, 11 плюс решения Q-D5 (несостоявшаяся проверка), Q-D6 (базовая проверка),
Q-13.2 (быстрое закрытие инцидента) и Q-D12 (цель CNAME).
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain
from apps.monitoring.models import Check, Event, EventType, Incident
from apps.monitoring.services.checking import run_check
from core.adapters.fake_resolver import FakeResolver, answered, no_records, unavailable
from core.types.checking import AddressStatus, CheckOutcome
from core.types.dns import RecordType

pytestmark = pytest.mark.django_db

START = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
PERIOD = timedelta(minutes=5)
NAME = "pay-gw.contoso.ru"
OUTSIDER = "185.44.9.201"


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name=NAME)
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


def check(domain: Domain, resolver: FakeResolver, at: datetime) -> None:
    run_check(domain_id=domain.pk, at=at, resolver=resolver)


def events(domain: Domain, event_type: EventType) -> int:
    return Event.objects.filter(domain=domain, event_type=event_type).count()


def test_7_4_first_check_is_baseline_and_silent(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered("10.20.1.14", "10.20.2.60")]})

    report = run_check(domain_id=domain.pk, at=START, resolver=resolver)

    assert report.is_baseline is True
    assert events(domain, EventType.WATCH_STARTED) == 1
    assert events(domain, EventType.ADDRESS_APPEARED) == 0
    assert events(domain, EventType.ADDRESS_GONE) == 0


def test_1_steady_matched_domain_produces_no_events(domain: Domain) -> None:
    # Критерий 1: три адреса, все входят в подсеть списка → статусы «Совпал», событий нет.
    # Базовая проверка порождает ровно одно WATCH_STARTED (7.4), дальше — тишина.
    resolver = FakeResolver({NAME: [answered("10.20.1.14", "10.20.2.60", "10.20.2.61")]})
    run_check(domain_id=domain.pk, at=START, resolver=resolver)
    assert Event.objects.filter(domain=domain).count() == 1

    at = START
    report = None
    for _ in range(5):
        at += PERIOD
        report = run_check(domain_id=domain.pk, at=at, resolver=resolver)

    assert report is not None
    assert {view.status for view in report.statuses} == {AddressStatus.MATCHED}
    assert report.outcome is CheckOutcome.NORMAL
    assert Event.objects.filter(domain=domain).count() == 1
    assert Incident.objects.filter(domain=domain).count() == 0


def test_2_address_outside_the_list_opens_an_incident(domain: Domain) -> None:
    # 7.4: вхождение в список — критерий абсолютный, на базовой проверке он не глушится.
    resolver = FakeResolver({NAME: [answered("10.20.1.14", OUTSIDER)]})

    report = run_check(domain_id=domain.pk, at=START, resolver=resolver)

    assert report.incident_opened is True
    assert events(domain, EventType.NOT_ALLOWED) == 1
    assert Incident.objects.filter(domain=domain, closed_at__isnull=True).count() == 1


def test_16_domain_without_an_allowlist_never_raises_an_incident() -> None:
    # Критерий 16 и 8.2: при пустом списке уведомлений нет никогда. Значит и инцидента нет —
    # иначе на обзоре висело бы открытое расхождение на каждом домене без списка (Q-D25).
    bare = Domain.objects.create(name="cdn.contoso.ru")
    resolver = FakeResolver({"cdn.contoso.ru": [answered("185.44.9.201", "10.20.1.14")]})

    at = START
    for _ in range(3):
        run_check(domain_id=bare.pk, at=at, resolver=resolver)
        at += PERIOD

    assert Incident.objects.filter(domain=bare).count() == 0
    assert Event.objects.filter(domain=bare, event_type=EventType.NOT_ALLOWED).count() == 0


def test_3_the_same_unmatched_address_does_not_repeat_the_event(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered("10.20.1.14", OUTSIDER)]})
    check(domain, resolver, START)

    at = START
    for _ in range(5):
        at += PERIOD
        check(domain, resolver, at)

    assert events(domain, EventType.NOT_ALLOWED) == 1
    assert Incident.objects.filter(domain=domain).count() == 1


def test_7_3_second_unmatched_address_reports_without_a_second_incident(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered("10.20.1.14", OUTSIDER)]})
    check(domain, resolver, START)

    resolver.set(NAME, [answered("10.20.1.14", OUTSIDER, "185.44.9.202")])
    check(domain, resolver, START + PERIOD)

    assert events(domain, EventType.NOT_ALLOWED) == 2
    assert Incident.objects.filter(domain=domain).count() == 1


def test_13_2_incident_closes_after_three_answers_without_the_address(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered("10.20.1.14", OUTSIDER)]})
    check(domain, resolver, START)
    resolver.set(NAME, [answered("10.20.1.14")])

    at = START
    for _ in range(3):
        at += PERIOD
        check(domain, resolver, at)

    incident = Incident.objects.get(domain=domain)
    assert incident.closed_at == at
    assert events(domain, EventType.BACK_TO_NORMAL) == 1
    # Порог актуальности (24 часа) при этом и близко не прошёл — Q-13.2.
    assert at - START < timedelta(hours=1)


def test_10_no_records_below_the_threshold_produces_no_event(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [no_records()]})

    at = START
    for _ in range(2):  # N по умолчанию 3
        check(domain, resolver, at)
        at += PERIOD

    assert Event.objects.filter(domain=domain).count() == 0
    assert Check.objects.filter(domain=domain, outcome=CheckOutcome.RESOLVE_ERROR).count() == 2


def test_11_no_records_at_the_threshold_produces_one_resolve_failed(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [no_records()]})

    at = START
    for _ in range(5):  # дальше порога
        check(domain, resolver, at)
        at += PERIOD

    assert events(domain, EventType.RESOLVE_FAILED) == 1


def test_7_1_resolve_recovered_after_failures(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [no_records()]})
    at = START
    for _ in range(3):
        check(domain, resolver, at)
        at += PERIOD

    resolver.set(NAME, [answered("10.20.1.14")])
    check(domain, resolver, at)

    assert events(domain, EventType.RESOLVE_RECOVERED) == 1


def test_d5_unavailable_check_changes_nothing(domain: Domain) -> None:
    resolver = FakeResolver({NAME: [answered("10.20.1.14")]})
    check(domain, resolver, START)
    resolver.set(NAME, [unavailable("все серверы молчат")])

    report = run_check(domain_id=domain.pk, at=START + PERIOD, resolver=resolver)

    assert report.outcome is CheckOutcome.NOT_PERFORMED
    assert Event.objects.filter(domain=domain, occurred_at=START + PERIOD).count() == 0
    last = Check.objects.filter(domain=domain).order_by("-checked_at").first()
    assert last is not None
    assert last.outcome == CheckOutcome.NOT_PERFORMED
    assert last.error == "все серверы молчат"


def test_d12_cname_target_is_written_only_with_the_flag() -> None:
    with_flag = Domain.objects.create(
        name="cdn.contoso.ru", record_types=[RecordType.A.value, RecordType.CNAME.value]
    )
    without_flag = Domain.objects.create(name="api.contoso.ru")
    resolver = FakeResolver(
        {
            "cdn.contoso.ru": [answered("10.20.1.14", canonical_name="edge.cdn.example")],
            "api.contoso.ru": [answered("10.20.1.14", canonical_name="edge.cdn.example")],
        }
    )

    check(with_flag, resolver, START)
    check(without_flag, resolver, START)

    with_flag.refresh_from_db()
    without_flag.refresh_from_db()
    assert with_flag.cname_target == "edge.cdn.example"
    assert without_flag.cname_target == ""
