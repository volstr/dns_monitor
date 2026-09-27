"""
Боевой резолвер — ТЗ 6.1, критерии 12 и 13, Q-C6, Q-D12.

Тесты уровнем ниже порта: они поднимают настоящий DNS-сервер на loopback и проверяют сам
адаптер. `mock.patch` по путям импорта не используется (CLAUDE.md).
"""

import ipaddress

import pytest

from core.adapters.dns_resolver import DnsResolver
from core.ports.resolver import ResolveOutcome
from core.types.dns import RecordType
from tests.core.dns_server import FakeDnsServer, answers, empty, free_port, nxdomain, silence

A_ONLY = frozenset({RecordType.A})
NAME = "pay-gw.contoso.ru"
TIMEOUT_MS = 300


def resolver_for(*servers: FakeDnsServer, port: int) -> DnsResolver:
    return DnsResolver(
        servers=[server.address for server in servers], timeout_ms=TIMEOUT_MS, port=port
    )


def test_6_1_answered_collects_addresses() -> None:
    with FakeDnsServer(answers("10.20.1.14", "10.20.2.61")) as server:
        result = resolver_for(server, port=server.port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.ANSWERED
    assert result.answer == {
        ipaddress.ip_address("10.20.1.14"),
        ipaddress.ip_address("10.20.2.61"),
    }
    assert result.answered_by == "127.0.0.1"


@pytest.mark.parametrize(("handler", "label"), [(nxdomain(), "NXDOMAIN"), (empty(), "NOERROR")])
def test_6_1_nxdomain_and_empty_answer_are_no_records(handler: object, label: str) -> None:
    with FakeDnsServer(handler) as server:  # type: ignore[arg-type]
        result = resolver_for(server, port=server.port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.NO_RECORDS, label
    assert result.answer == frozenset()


def test_6_1_no_records_does_not_fall_through_to_the_next_server() -> None:
    # «Пустой ответ и NXDOMAIN — не ошибка сети, переход к следующему серверу не выполняется».
    port = free_port()
    with (
        FakeDnsServer(nxdomain(), address="127.0.0.1", port=port) as first,
        FakeDnsServer(answers("10.20.1.14"), address="127.0.0.2", port=port) as second,
    ):
        result = resolver_for(first, second, port=port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.NO_RECORDS
    assert result.answered_by == "127.0.0.1"
    assert second.queries == 0, "второй сервер спрашивать не следовало"


def test_13_first_server_silent_second_answers() -> None:
    port = free_port()
    with (
        FakeDnsServer(silence(), address="127.0.0.1", port=port) as first,
        FakeDnsServer(answers("10.20.1.14"), address="127.0.0.2", port=port) as second,
    ):
        result = resolver_for(first, second, port=port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.ANSWERED
    assert result.answer == {ipaddress.ip_address("10.20.1.14")}
    assert result.answered_by == "127.0.0.2"
    assert first.queries >= 1, "первый сервер должны были спросить первым"


def test_12_all_servers_silent_is_unavailable() -> None:
    port = free_port()
    with (
        FakeDnsServer(silence(), address="127.0.0.1", port=port) as first,
        FakeDnsServer(silence(), address="127.0.0.2", port=port) as second,
    ):
        result = resolver_for(first, second, port=port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.UNAVAILABLE
    assert result.answer == frozenset()
    assert result.error is not None
    assert first.queries >= 1
    assert second.queries >= 1


def test_c6_one_attempt_per_server() -> None:
    # Повторов внутри адаптера нет: механизм повтора — перебор списка (Q-C6).
    with FakeDnsServer(silence()) as server:
        resolver_for(server, port=server.port).resolve(NAME, A_ONLY)

    assert server.queries == 1


def test_d12_canonical_name_is_captured() -> None:
    with FakeDnsServer(answers("185.15.59.224", cname="dyna.wikimedia.org.")) as server:
        result = resolver_for(server, port=server.port).resolve(NAME, A_ONLY)

    assert result.outcome is ResolveOutcome.ANSWERED
    assert result.canonical_name == "dyna.wikimedia.org"


def test_d12_canonical_name_is_empty_without_a_chain() -> None:
    with FakeDnsServer(answers("10.20.1.14")) as server:
        result = resolver_for(server, port=server.port).resolve(NAME, A_ONLY)

    assert result.canonical_name is None


def test_empty_server_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="пуст"):
        DnsResolver(servers=[], timeout_ms=TIMEOUT_MS)
