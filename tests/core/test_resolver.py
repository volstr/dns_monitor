"""
Порт резолва — ТЗ 6.1, Q-E3.

Три исхода обязаны различаться типом, а не дисциплиной вызывающего кода: `NO_RECORDS` — это
наблюдение, `UNAVAILABLE` — «мы не смогли спросить», и второе не порождает ни событий, ни
изменений реестра.
"""

import ipaddress
from dataclasses import FrozenInstanceError

import pytest

from core.adapters.fake_resolver import FakeResolver, answered, no_records, unavailable
from core.ports.resolver import ResolveOutcome, ResolveResult
from core.types.dns import RecordType

A_ONLY = frozenset({RecordType.A})


def test_answered_collects_addresses_into_the_answer() -> None:
    result = answered("10.20.1.14", "10.20.2.61")

    assert result.outcome is ResolveOutcome.ANSWERED
    assert result.answer == {
        ipaddress.ip_address("10.20.1.14"),
        ipaddress.ip_address("10.20.2.61"),
    }


def test_6_1_no_records_and_unavailable_are_different_outcomes() -> None:
    assert no_records().outcome is ResolveOutcome.NO_RECORDS
    assert unavailable().outcome is ResolveOutcome.UNAVAILABLE


def test_6_1_unavailable_carries_no_answer_and_a_reason() -> None:
    result = unavailable("таймаут на всех серверах")

    assert result.answer == frozenset()
    assert result.error == "таймаут на всех серверах"


def test_result_is_frozen() -> None:
    result = answered("10.20.1.14")

    with pytest.raises(FrozenInstanceError):
        result.outcome = ResolveOutcome.NO_RECORDS  # type: ignore[misc]


def test_d12_canonical_name_comes_with_the_answer() -> None:
    result = answered("185.15.59.224", canonical_name="dyna.wikimedia.org.")

    assert result.canonical_name == "dyna.wikimedia.org."


def test_13_answered_by_names_the_server_that_replied() -> None:
    result = answered("10.20.1.14", answered_by="1.1.1.1")

    assert result.answered_by == "1.1.1.1"


def test_6_fake_resolver_repeats_the_sequence_in_a_circle() -> None:
    # Сценарий критерия 6: домен отдаёт по кругу два адреса из трёх.
    resolver = FakeResolver(
        {
            "pay-gw.contoso.ru": [
                answered("10.20.1.14", "10.20.2.60"),
                answered("10.20.2.60", "10.20.2.61"),
                answered("10.20.2.61", "10.20.1.14"),
            ]
        }
    )

    seen = [resolver.resolve("pay-gw.contoso.ru", A_ONLY).answer for _ in range(4)]

    assert seen[0] == {ipaddress.ip_address("10.20.1.14"), ipaddress.ip_address("10.20.2.60")}
    assert seen[1] == {ipaddress.ip_address("10.20.2.60"), ipaddress.ip_address("10.20.2.61")}
    assert seen[2] == {ipaddress.ip_address("10.20.2.61"), ipaddress.ip_address("10.20.1.14")}
    # Четвёртый вызов — снова первый ответ: последовательность идёт по кругу.
    assert seen[3] == seen[0]
    # За три ответа домен отдал все три адреса, хотя в каждом ответе их было по два.
    assert set().union(*seen[:3]) == {
        ipaddress.ip_address("10.20.1.14"),
        ipaddress.ip_address("10.20.2.60"),
        ipaddress.ip_address("10.20.2.61"),
    }


def test_fake_resolver_records_what_was_asked() -> None:
    resolver = FakeResolver({"api.contoso.ru": [no_records()]})
    types = frozenset({RecordType.A, RecordType.AAAA})

    resolver.resolve("api.contoso.ru", types)

    assert resolver.calls == [("api.contoso.ru", types)]


def test_fake_resolver_rejects_an_unknown_name() -> None:
    # Молчаливый UNAVAILABLE спрятал бы опечатку в имени и сделал бы тест зелёным зря.
    resolver = FakeResolver()

    with pytest.raises(LookupError, match="ответы не заданы"):
        resolver.resolve("api.contoso.ru", A_ONLY)


def test_fake_resolver_rejects_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="хотя бы один ответ"):
        FakeResolver({"api.contoso.ru": []})


def test_result_defaults_to_an_empty_answer() -> None:
    result = ResolveResult(outcome=ResolveOutcome.NO_RECORDS)

    assert result.answer == frozenset()
    assert result.canonical_name is None
    assert result.answered_by is None
    assert result.error is None
