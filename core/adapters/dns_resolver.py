"""
Боевой резолвер на dnspython — ТЗ 6.1.

**Единственное место во всём проекте с `import dns`.** Доменный код знает только порт и не знает,
что серверов несколько.

Правила обхода взяты из 6.1 дословно:

- серверы опрашиваются **по порядку**, до первого ответившего;
- пустой ответ (`NOERROR` без записей) и `NXDOMAIN` — это **результат**, а не ошибка сети:
  переход к следующему серверу не выполняется;
- сетевая ошибка или таймаут — переход к следующему серверу; если молчат все, проверка
  не состоялась.

Собственные повторы dnspython отключены (Q-C6): на сервер приходится ровно одна попытка, а
механизмом повтора служит сам перебор списка. Иначе таймаут одной проверки стал бы произведением
трёх чисел и планировщик встал бы на тысяче доменов.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

import dns.exception
import dns.resolver

from core.ports.resolver import ResolveOutcome, Resolver, ResolveResult
from core.types.dns import IPAddress, RecordType

DEFAULT_DNS_PORT = 53

#: Типы записей, которые дают адреса. CNAME адресов не даёт: по Q-D12 его цель приходит вместе
#: с ответом на A-запрос, отдельного запроса для неё не нужно.
_ADDRESS_RECORD_TYPES: dict[RecordType, str] = {RecordType.A: "A", RecordType.AAAA: "AAAA"}


class DnsResolver(Resolver):
    def __init__(
        self,
        servers: Sequence[str],
        timeout_ms: int,
        port: int = DEFAULT_DNS_PORT,
    ) -> None:
        if not servers:
            message = "Список DNS-серверов пуст: опрашивать некого (ТЗ 4.1)"
            raise ValueError(message)
        self._servers = tuple(servers)
        self._timeout = timeout_ms / 1000
        self._port = port

    def resolve(self, name: str, record_types: frozenset[RecordType]) -> ResolveResult:
        wanted = tuple(
            rdtype for kind, rdtype in _ADDRESS_RECORD_TYPES.items() if kind in record_types
        )
        # Домен, у которого не отмечен ни один адресный тип, адресов не наблюдает. Запрос A всё
        # равно делается: он даёт цель цепочки CNAME, ради которой флаг и включают (Q-D12).
        queried = wanted or ("A",)
        last_error: str | None = None

        for server in self._servers:
            reply = self._ask(server, name, queried, collect=bool(wanted))
            if reply is None:
                last_error = f"{server}: сервер не ответил"
                continue
            return reply

        return ResolveResult(
            outcome=ResolveOutcome.UNAVAILABLE,
            error=last_error or "ни один сервер не ответил",
        )

    def _ask(
        self, server: str, name: str, queried: Sequence[str], *, collect: bool
    ) -> ResolveResult | None:
        """Один сервер. ``None`` означает «не ответил», то есть нужно идти к следующему."""
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [server]
        resolver.port = self._port
        # Одна попытка на сервер: timeout и lifetime совпадают, повтор по SERVFAIL выключен.
        resolver.timeout = self._timeout
        resolver.lifetime = self._timeout
        resolver.retry_servfail = False

        addresses: set[IPAddress] = set()
        canonical: str | None = None
        replied = False

        for rdtype in queried:
            try:
                answer = resolver.resolve(name, rdtype)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                # Сервер ответил: записей такого типа нет. К следующему серверу не идём (6.1).
                replied = True
                continue
            except dns.exception.DNSException:
                return None
            replied = True
            canonical = canonical or _canonical_name(name, str(answer.canonical_name))
            if collect:
                addresses.update(ipaddress.ip_address(str(record)) for record in answer)

        if not replied:
            return None
        if not addresses:
            # Домен не резолвится — это наблюдение, а не ошибка (6.1). Цель CNAME при этом
            # могла прийти, и она нужна карточке домена.
            return ResolveResult(
                outcome=ResolveOutcome.NO_RECORDS,
                canonical_name=canonical,
                answered_by=server,
            )
        return ResolveResult(
            outcome=ResolveOutcome.ANSWERED,
            answer=frozenset(addresses),
            canonical_name=canonical,
            answered_by=server,
        )


def _canonical_name(name: str, canonical: str) -> str | None:
    """Цель цепочки CNAME. ``None``, если цепочки не было (Q-D12)."""
    target = canonical.rstrip(".")
    return None if target == name.rstrip(".") else target
