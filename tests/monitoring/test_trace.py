"""
След реестра для графика — ТЗ 9.2, решения Q-E1, Q-D27.

До этого файла у следа тестов не было вовсе: виджет проверялся браузером (этап 7), а сам
селектор — ничем. Правило 9.2, которое здесь закрепляется, легко потерять при любой правке,
потому что оно противоречит первому побуждению:

    «Цвет отметки соответствует **текущему** статусу адреса […] Незакрашенная отметка означает,
    что в этот период адрес не отдавался».

То есть цвет и заливка говорят о **разном времени**: заливка — про тот период, цвет — про
сейчас. Адрес, который отдавался неделю назад и с тех пор пропал, красит все свои отметки в
серый: закрашены они потому, что тогда он отдавался, а серые — потому что сейчас он неактуален.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import Domain
from apps.monitoring.models import AddressObservationBucket, BucketSize
from apps.monitoring.selectors.trace import trace_of_domain

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 22, 14, 38, tzinfo=UTC)
FRESH = "142.250.180.14"
GONE = "142.251.39.142"
IPV6 = "2a00:1450:400e:808::200e"


def observe(domain: Domain, address: str, *days: int) -> None:
    for day in days:
        AddressObservationBucket.objects.create(
            domain=domain,
            address=address,
            bucket_size=BucketSize.DAY.value,
            bucket_start=MOMENT - timedelta(days=day),
        )


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name="google.com")


def test_9_2_colour_is_the_current_status_and_fill_is_the_past(domain: Domain) -> None:
    """
    Два разных времени в одной отметке, и это не оплошность, а 9.2 дословно.

    Разработчик споткнулся именно здесь: адрес отдавался три дня из четырёх, но в последние
    сутки не пришёл ни разу — и все три отметки оказались серыми, а не зелёными. Ожидание
    «отдавался в тот день → зелёный» естественно, но тогда цвет дублировал бы заливку, а
    текущее состояние адреса на графике стало бы не видно вовсе.
    """
    observe(domain, GONE, 3, 2, 1)

    trace = trace_of_domain(domain_id=domain.pk, at=MOMENT, statuses={GONE: "stale"})

    row = trace["rows"][0]
    assert row["status"] == "stale", "цвет — от статуса сейчас, а не от того, что было в тот день"
    assert len(row["days"]) == 3, "заливка — от того, что было в тот день"


def test_9_2_a_day_without_an_answer_leaves_no_mark(domain: Domain) -> None:
    """След из отметок, а не сплошная полоса: пропуски обязаны быть видны (9.2)."""
    observe(domain, FRESH, 3, 1)

    trace = trace_of_domain(domain_id=domain.pk, at=MOMENT, statuses={FRESH: "matched"})

    # Ось — четверо суток, отдача была в двух: между ними пропуск, и он не закрашивается.
    assert trace["days"] == 4
    assert trace["rows"][0]["days"] == [0, 2]


def test_9_2_axis_covers_the_whole_period_of_watching(domain: Domain) -> None:
    """
    Ось охватывает весь срок наблюдения, чтобы след любого адреса попал в поле зрения.

    Считается по самой ранней корзине **любого** адреса, а не текущего: иначе у каждой строки
    была бы своя ось, и строки перестали бы сравниваться между собой.
    """
    observe(domain, GONE, 9)
    observe(domain, FRESH, 1)

    trace = trace_of_domain(
        domain_id=domain.pk, at=MOMENT, statuses={GONE: "stale", FRESH: "matched"}
    )

    assert trace["days"] == 10
    rows = {row["address"]: row["days"] for row in trace["rows"]}
    assert rows[GONE] == [0]
    assert rows[FRESH] == [8]


def test_d27_address_that_does_not_exist_gets_no_row(domain: Domain) -> None:
    """
    Снятый общий флаг IPv6 означает, что таких адресов нет вовсе (Q-D27, ТЗ 4.3).

    В реестре их и нет, а в корзинах наблюдений они остаются — записаны, пока флаг был поднят.
    Без отбора по статусам такая строка уезжала в разметку с пустым статусом, а виджет на
    неизвестный статус подставляет зелёный, то есть «адрес решён»: цвет, означающий ровно
    противоположное тому, что произошло.
    """
    observe(domain, FRESH, 1)
    observe(domain, IPV6, 1)

    trace = trace_of_domain(domain_id=domain.pk, at=MOMENT, statuses={FRESH: "matched"})

    assert [row["address"] for row in trace["rows"]] == [FRESH]


def test_9_2_domain_without_observations_gives_an_empty_trace(domain: Domain) -> None:
    trace = trace_of_domain(domain_id=domain.pk, at=MOMENT, statuses={})

    assert trace["rows"] == []
    assert trace["days"] == 1
