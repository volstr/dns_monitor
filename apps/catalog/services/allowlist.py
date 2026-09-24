"""Разбор и проверка белого списка — ТЗ 10.2, 13.3; критерии 44, 45.

Одна реализация на все входы. Её зовёт и построчная проверка через HTMX, и сохранение карточки,
и — позже — импорт списком (10.3). Дублировать разбор CIDR в JS запрещено прямо (`CLAUDE.md`), но
и вторая реализация на сервере была бы тем же самым: рано или поздно они разойдутся.

**Ошибка и предупреждение — разные вещи** (10.2). Ошибка блокирует сохранение и говорит человеку,
что именно не так («999 — недопустимый октет»), а не код. Предупреждение сохранению не мешает:
дубликат, покрытая запись и запись семейства, которое у домена не запрашивается, — законный ввод,
о котором стоит сказать.

Причина ошибки формулируется словами, поэтому адрес разбирается вручную, а не одним вызовом
`ip_network`: тот умеет только «does not appear to be an IPv4 or IPv6 network».
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from core.types.dns import IPNetwork, RecordType

#: Длина колонки `raw` (Q-13.3). Самая длинная осмысленная запись — IPv6 с маской.
MAX_ENTRY_LENGTH = 49

IPV4_OCTETS = 4
IPV4_MAX_OCTET = 255
IPV4_MAX_PREFIX = 32
IPV6_MAX_PREFIX = 128


@dataclass(frozen=True, slots=True)
class ParsedLine:
    """Одна строка ввода вместе с тем, что о ней известно."""

    number: int
    text: str
    network: IPNetwork | None
    #: Текст, каким он уйдёт в колонку `raw`. У нормализованной записи — уже приведённый.
    raw: str
    #: Q-13.3: ввод изменён нормализацией, и это показывается до сохранения.
    normalized_from: str | None
    error: str | None
    warning: str | None


@dataclass(frozen=True, slots=True)
class ParsedAllowlist:
    """Результат разбора всего поля."""

    lines: tuple[ParsedLine, ...]

    @property
    def errors(self) -> tuple[ParsedLine, ...]:
        return tuple(line for line in self.lines if line.error)

    @property
    def warnings(self) -> tuple[ParsedLine, ...]:
        return tuple(line for line in self.lines if line.warning)

    @property
    def can_save(self) -> bool:
        """10.2: сохранение блокируется, пока есть хотя бы одна ошибочная строка."""
        return not self.errors

    @property
    def entries(self) -> tuple[ParsedLine, ...]:
        """Строки, годные к сохранению. Дубликаты отброшены, порядок ввода сохранён."""
        seen: set[IPNetwork] = set()
        kept = []
        for line in self.lines:
            if line.network is None or line.network in seen:
                continue
            seen.add(line.network)
            kept.append(line)
        return tuple(kept)


def parse_allowlist(text: str, record_types: tuple[str, ...] = ()) -> ParsedAllowlist:
    """Разобрать поле построчно.

    ``record_types`` нужен единственному предупреждению 10.2 — про запись семейства, которое у
    домена не запрашивается. Пустой кортеж означает «не проверять»: у импорта (10.3) домена ещё
    нет, и предупреждать там не о чем.
    """
    parsed: list[ParsedLine] = []
    for number, original in enumerate(text.splitlines(), start=1):
        stripped = original.strip()
        if not stripped:
            # Пустые строки — не ошибка: человек разделяет ими группы записей.
            continue
        parsed.append(_parse_line(number, stripped))

    return ParsedAllowlist(lines=tuple(_warned(parsed, record_types)))


def _parse_line(number: int, text: str) -> ParsedLine:
    if len(text) > MAX_ENTRY_LENGTH:
        return _failed(number, text, "запись длиннее, чем может быть адрес с маской")

    address_text, _, prefix_text = text.partition("/")
    address, error = _parse_address(address_text)
    if address is None:
        return _failed(number, text, error or "не похоже на адрес")

    prefix, error = _parse_prefix(prefix_text, address)
    if error is not None:
        return _failed(number, text, error)

    network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
    # Ненулевая хостовая часть: Q-13.3 велит нормализовать и показать изменение.
    normalized_from = text if str(network) != text and prefix_text else None
    raw = str(network) if normalized_from else text

    return ParsedLine(
        number=number,
        text=text,
        network=network,
        raw=raw,
        normalized_from=normalized_from,
        error=None,
        warning=None,
    )


def _failed(number: int, text: str, error: str) -> ParsedLine:
    return ParsedLine(
        number=number,
        text=text,
        network=None,
        raw=text,
        normalized_from=None,
        error=error,
        warning=None,
    )


type ParsedAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _parse_address(text: str) -> tuple[ParsedAddress | None, str | None]:
    """Разбор адреса с человеческой причиной отказа (10.2)."""
    if not text:
        return None, "адрес не указан"
    if ":" in text:
        try:
            return ipaddress.IPv6Address(text), None
        except ValueError:
            return None, "не похоже на IPv6-адрес"

    parts = text.split(".")
    if len(parts) != IPV4_OCTETS:
        return None, f"в IPv4-адресе четыре октета, а здесь {len(parts)}"
    for part in parts:
        if not part.isdigit():
            return None, f"«{part}» — не число. Октет записывается цифрами"
        if int(part) > IPV4_MAX_OCTET:
            return None, f"{part} — недопустимый октет. Значение каждого октета от 0 до 255"
    try:
        return ipaddress.IPv4Address(text), None
    except ValueError:
        return None, "не похоже на IPv4-адрес"


def _parse_prefix(text: str, address: ParsedAddress) -> tuple[int, str | None]:
    """Длина маски. Пусто — запись без маски, то есть один адрес (6.3)."""
    limit = IPV4_MAX_PREFIX if address.version == 4 else IPV6_MAX_PREFIX
    if not text:
        return limit, None
    if not text.isdigit():
        return 0, "длина маски записывается числом после косой черты"
    prefix = int(text)
    if prefix > limit:
        return 0, f"длина маски у IPv{address.version} — от 0 до {limit}, а здесь {prefix}"
    return prefix, None


def _warned(lines: list[ParsedLine], record_types: tuple[str, ...]) -> list[ParsedLine]:
    """Предупреждения 10.2. Сохранению не мешают, поэтому считаются после разбора всех строк."""
    result: list[ParsedLine] = []
    for line in lines:
        warning = None if line.network is None else _warning_for(line, lines, record_types)
        result.append(line if warning is None else _with_warning(line, warning))
    return result


def _warning_for(
    line: ParsedLine, lines: list[ParsedLine], record_types: tuple[str, ...]
) -> str | None:
    earlier = [
        other
        for other in lines
        if other.number < line.number and other.network is not None and other.error is None
    ]

    for other in earlier:
        if other.network == line.network:
            return f"дубликат строки {other.number}"

    for other in earlier:
        if _covers(other.network, line.network):
            return (
                f"{line.raw} уже входит в {other.raw} из строки {other.number} — "
                f"запись, скорее всего, лишняя"
            )

    return _family_warning(line, record_types)


def _covers(outer: IPNetwork | None, inner: IPNetwork | None) -> bool:
    """Полностью ли одна запись покрывает другую. Разные семейства не сравниваются."""
    if outer is None or inner is None or outer.version != inner.version:
        return False
    return inner.subnet_of(outer)  # type: ignore[arg-type]


def _family_warning(line: ParsedLine, record_types: tuple[str, ...]) -> str | None:
    """10.2: запись семейства, которое у домена не запрашивается, никогда не совпадёт."""
    if not record_types or line.network is None:
        return None
    needed = RecordType.AAAA if line.network.version == 6 else RecordType.A
    if needed.value in record_types:
        return None
    return f"тип записи {needed.value} у домена выключен — эта запись никогда не совпадёт"


def _with_warning(line: ParsedLine, warning: str) -> ParsedLine:
    return ParsedLine(
        number=line.number,
        text=line.text,
        network=line.network,
        raw=line.raw,
        normalized_from=line.normalized_from,
        error=line.error,
        warning=warning,
    )
