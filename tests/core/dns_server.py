"""
Фиктивный DNS-сервер на loopback — для критериев 12 и 13.

Критерий 13 («первый сервер не ответил, второй ответил») проверяет сам адаптер, то есть уровень
ниже порта. Подменять транспорт через `mock.patch` по путям импорта `CLAUDE.md` запрещает,
поэтому здесь поднимается настоящий сервер на UDP: dnspython работает с ним как с любым другим.

Имя файла не начинается с `test_`, поэтому pytest его не собирает.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from types import TracebackType

import dns.message
import dns.name
import dns.rcode
import dns.rrset

Handler = Callable[[dns.message.Message], dns.message.Message | None]

RECEIVE_TIMEOUT = 0.2
BUFFER_SIZE = 4096
TTL = 300


def free_port() -> int:
    """Свободный порт. Оба сервера критерия 13 слушают его на разных адресах loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port: int = probe.getsockname()[1]
    return port


def answers(*addresses: str, cname: str | None = None) -> Handler:
    """Обычный ответ. С `cname` отдаётся цепочка: имя → цель → адреса."""

    def handle(query: dns.message.Message) -> dns.message.Message:
        response = dns.message.make_response(query)
        name = query.question[0].name
        target = name
        if cname is not None:
            target = dns.name.from_text(cname)
            response.answer.append(dns.rrset.from_text(name, TTL, 'IN', 'CNAME', cname))
        if addresses:
            record_type = 'AAAA' if ':' in addresses[0] else 'A'
            response.answer.append(
                dns.rrset.from_text(target, TTL, 'IN', record_type, *addresses)
            )
        return response

    return handle


def nxdomain() -> Handler:
    def handle(query: dns.message.Message) -> dns.message.Message:
        response = dns.message.make_response(query)
        response.set_rcode(dns.rcode.NXDOMAIN)
        return response

    return handle


def empty() -> Handler:
    """`NOERROR` без записей — по 6.1 это результат, а не ошибка сети."""

    def handle(query: dns.message.Message) -> dns.message.Message:
        return dns.message.make_response(query)

    return handle


def silence() -> Handler:
    """Сервер молчит: клиент упрётся в таймаут и должен пойти к следующему."""

    def handle(query: dns.message.Message) -> dns.message.Message | None:
        return None

    return handle


class FakeDnsServer:
    def __init__(self, handler: Handler, address: str = '127.0.0.1', port: int = 0) -> None:
        self.handler = handler
        self.address = address
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((address, port))
        self._socket.settimeout(RECEIVE_TIMEOUT)
        self.port: int = self._socket.getsockname()[1]
        #: Сколько запросов дошло — по нему видно, спрашивали сервер или нет.
        self.queries = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeDnsServer:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._socket.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self._socket.recvfrom(BUFFER_SIZE)
            except TimeoutError:
                continue
            except OSError:
                break
            self.queries += 1
            response = self.handler(dns.message.from_wire(data))
            if response is not None:
                self._socket.sendto(response.to_wire(), peer)
