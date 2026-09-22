"""Планировщик: выборка, резервирование, перепланирование, чистка — ТЗ 11.1, Q-D23, критерий 58."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection, transaction

from apps.catalog.models import Domain, Group
from apps.monitoring.models import Check
from apps.monitoring.services.scheduling import (
    FAILED_HISTORY,
    RESERVATION,
    SUCCESSFUL_HISTORY,
    claim_due_domains,
    prune_check_history,
    reschedule_after_check,
)
from core.types.checking import CheckOutcome

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 23, 14, 38, tzinfo=UTC)


def make_domain(name: str, *, due_at: datetime | None = MOMENT, enabled: bool = True) -> Domain:
    return Domain.objects.create(name=name, next_check_at=due_at, watch_enabled=enabled)


def test_11_1_claims_only_domains_whose_time_has_come() -> None:
    due = make_domain("pay-gw.contoso.ru", due_at=MOMENT - timedelta(seconds=1))
    make_domain("later.contoso.ru", due_at=MOMENT + timedelta(minutes=5))

    claimed = claim_due_domains(at=MOMENT, limit=10)

    assert [item.domain_id for item in claimed] == [due.pk]


def test_11_1_disabled_domain_is_not_checked() -> None:
    make_domain("status.contoso.ru", enabled=False)

    assert claim_due_domains(at=MOMENT, limit=10) == ()


def test_11_1_domain_without_a_schedule_is_not_claimed() -> None:
    make_domain("fresh.contoso.ru", due_at=None)

    assert claim_due_domains(at=MOMENT, limit=10) == ()


def test_11_1_reservation_moves_the_domain_out_of_the_next_selection() -> None:
    domain = make_domain("pay-gw.contoso.ru")

    claim_due_domains(at=MOMENT, limit=10)

    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT + RESERVATION
    # Второй тик в ту же секунду домен уже не возьмёт.
    assert claim_due_domains(at=MOMENT, limit=10) == ()


def test_11_1_crashed_process_does_not_wedge_the_domain() -> None:
    # Резервирование короткое: домен вернётся в выборку сам, без вмешательства.
    make_domain("pay-gw.contoso.ru")
    claim_due_domains(at=MOMENT, limit=10)

    later = MOMENT + RESERVATION + timedelta(seconds=1)

    assert len(claim_due_domains(at=later, limit=10)) == 1


def test_11_1_limit_bounds_one_tick() -> None:
    for index in range(5):
        make_domain(f"domain-{index}.contoso.ru")

    assert len(claim_due_domains(at=MOMENT, limit=2)) == 2


def test_11_1_reschedule_uses_the_group_period() -> None:
    group = Group.objects.create(name="Внешние подрядчики", check_period_seconds=15 * 60)
    domain = make_domain("cdn.contoso.ru")
    domain.group = group
    domain.save(update_fields=["group"])

    next_check_at = reschedule_after_check(domain_id=domain.pk, at=MOMENT)

    assert next_check_at == MOMENT + timedelta(minutes=15)
    domain.refresh_from_db()
    assert domain.next_check_at == MOMENT + timedelta(minutes=15)


def record(domain: Domain, outcome: CheckOutcome, at: datetime) -> Check:
    return Check.objects.create(domain=domain, checked_at=at, outcome=outcome)


def test_d23_successful_history_is_trimmed_to_twenty() -> None:
    domain = make_domain("pay-gw.contoso.ru")
    for index in range(SUCCESSFUL_HISTORY + 15):
        record(domain, CheckOutcome.NORMAL, MOMENT + timedelta(minutes=index))

    prune_check_history(domain_id=domain.pk)

    assert Check.objects.filter(domain=domain).count() == SUCCESSFUL_HISTORY


def test_d23_failed_checks_survive_beyond_the_successful_window() -> None:
    domain = make_domain("legacy.contoso.ru")
    record(domain, CheckOutcome.NOT_PERFORMED, MOMENT)
    record(domain, CheckOutcome.RESOLVE_ERROR, MOMENT + timedelta(minutes=1))
    for index in range(SUCCESSFUL_HISTORY + 10):
        record(domain, CheckOutcome.NORMAL, MOMENT + timedelta(hours=1, minutes=index))

    prune_check_history(domain_id=domain.pk)

    failed = Check.objects.filter(
        domain=domain, outcome__in=[CheckOutcome.NOT_PERFORMED, CheckOutcome.RESOLVE_ERROR]
    )
    assert failed.count() == 2
    assert Check.objects.filter(domain=domain, outcome=CheckOutcome.NORMAL).count() == 20


def test_d23_failed_history_is_bounded_too() -> None:
    # Сверх утверждённой формулировки: иначе навсегда сломанный домен рос бы без потолка.
    domain = make_domain("legacy.contoso.ru")
    for index in range(FAILED_HISTORY + 5):
        record(domain, CheckOutcome.RESOLVE_ERROR, MOMENT + timedelta(minutes=index))

    prune_check_history(domain_id=domain.pk)

    assert Check.objects.filter(domain=domain).count() == FAILED_HISTORY


def test_11_5_last_twenty_checks_of_any_kind_survive() -> None:
    # Колонка «История» берёт ровно 20 последних проверок любого рода.
    domain = make_domain("pay-gw.contoso.ru")
    outcomes = [CheckOutcome.NORMAL, CheckOutcome.RESOLVE_ERROR, CheckOutcome.HAS_UNMATCHED]
    for index in range(60):
        record(domain, outcomes[index % 3], MOMENT + timedelta(minutes=index))
    latest = list(
        Check.objects.filter(domain=domain)
        .order_by("-checked_at")
        .values_list("pk", flat=True)[:20]
    )

    prune_check_history(domain_id=domain.pk)

    survived = set(Check.objects.filter(domain=domain).values_list("pk", flat=True))
    assert set(latest) <= survived


@pytest.mark.django_db(transaction=True)
def test_58_two_instances_do_not_claim_the_same_domain() -> None:
    """Критерий 58: каждый домен резолвится одним экземпляром, а не двумя."""
    for index in range(2):
        make_domain(f"domain-{index}.contoso.ru")

    holding = threading.Event()
    release = threading.Event()
    other_instance: list[int] = []

    def rival() -> None:
        try:
            with transaction.atomic():
                rows = list(
                    Domain.objects.filter(watch_enabled=True, next_check_at__lte=MOMENT)
                    .select_for_update(skip_locked=True)
                    .values_list("pk", flat=True)
                )
                other_instance.extend(rows)
                holding.set()
                release.wait(timeout=5)
        finally:
            connection.close()

    thread = threading.Thread(target=rival)
    thread.start()
    try:
        assert holding.wait(timeout=5)
        # Пока соперник держит строки, наш тик их пропускает, а не ждёт и не дублирует.
        ours = claim_due_domains(at=MOMENT, limit=10)
    finally:
        release.set()
        thread.join(timeout=5)

    assert set(other_instance) & {item.domain_id for item in ours} == set()
    Domain.objects.all().delete()
