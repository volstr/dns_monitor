"""Как показывается «не проверяется» — Q-D25, ТЗ 8.2, 9.2.

Пустой белый список означает, что вхождение **не вычислялось**: сравнивать было не с чем. Такой
адрес не решён и не «не решён» — он вне этой шкалы. Ошибиться здесь легко и незаметно: достаточно
покрасить отметку в зелёный или положить адрес к решённым, и интерфейс начинает утверждать, что
адрес прошёл проверку, которой не было.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain
from apps.catalog.selectors.domain_card import RegistryFilter, domain_card
from apps.monitoring.models import AddressRegistryEntry

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)


def make_domain(name: str = "github.com") -> Domain:
    domain = Domain.objects.create(name=name, last_successful_check_at=MOMENT)
    AddressRegistryEntry.objects.create(
        domain=domain,
        address="140.82.121.3",
        first_seen_at=MOMENT - timedelta(days=1),
        last_seen_at=MOMENT,
    )
    return domain


def card(domain: Domain, **extra: object) -> object:
    result = domain_card(domain_id=domain.pk, at=MOMENT, **extra)  # type: ignore[arg-type]
    assert result is not None
    return result


def test_d25_not_checked_is_not_painted_as_resolved() -> None:
    """Ни подписи «Решён», ни зелёного тона: проверки не было."""
    line = card(make_domain()).registry[0]

    assert line.status_label == "Не проверяется"
    assert line.status_tone == ""
    assert line.match_text == "список не задан"


def test_d25_trace_mark_is_neutral_not_green() -> None:
    """9.2: цвет отметки — от статуса. У «не проверяется» он свой, а не зелёный.

    Зелёная отметка в легенде значит «адрес решён» — про адрес без списка это неправда.
    """
    row = card(make_domain()).trace["rows"]

    assert row == [] or row[0]["status"] == "not_checked"


def test_d25_not_checked_is_absent_from_the_resolved_segment() -> None:
    """Класть его к решённым значило бы утверждать, что адрес прошёл проверку."""
    domain = make_domain()

    assert card(domain, registry=RegistryFilter.MATCHED).registry == ()
    assert card(domain, registry=RegistryFilter.UNMATCHED).registry == ()
    assert len(card(domain, registry=RegistryFilter.ALL).registry) == 1


def test_d25_a_real_match_still_reads_as_resolved() -> None:
    """Обратная проверка: там, где список есть и адрес в него попал, зелёное законно."""
    domain = make_domain()
    AllowlistEntry.objects.create(domain=domain, entry="140.82.0.0/16", raw="140.82.0.0/16")

    line = card(domain).registry[0]

    assert (line.status_label, line.status_tone) == ("Решён", "ok")
    assert card(domain, registry=RegistryFilter.MATCHED).registry != ()


def test_d25_summary_does_not_claim_success() -> None:
    result = card(make_domain())

    assert "не заданы" in result.summary_label
    assert result.summary_tone == ""
