"""Структурные логи — plan.md этап 8; контекст события по ТЗ 11.14.

Формат журнала — часть интерфейса эксплуатации: по нему ищут. Поэтому проверяется не «строка
непустая», а то, что поле называется так, как названо, и что в него попало именно значение, а
не его представление внутри чужой строки.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime

import pytest
from django.utils.timezone import localtime

from apps.catalog.models import Domain
from apps.monitoring.services.runner import TickSkipped, describe
from apps.monitoring.services.tick import tick
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, answered
from core.adapters.fake_state_store import FakeStateStore
from core.observability.logs import JsonFormatter, RunIdFilter, current_run, new_run

MOMENT = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)


def record(message: str = "проверка домена упала", **extra: object) -> logging.LogRecord:
    made = logging.LogRecord(
        name="apps.monitoring.services.tick",
        level=logging.ERROR,
        pathname="tick.py",
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(made, key, value)
    return made


def formatted(made: logging.LogRecord) -> dict[str, object]:
    RunIdFilter().filter(made)
    return json.loads(JsonFormatter().format(made))


def test_one_record_is_one_json_line() -> None:
    line = JsonFormatter().format(record())

    assert "\n" not in line
    assert json.loads(line)["message"] == "проверка домена упала"


def test_level_and_logger_are_separate_fields() -> None:
    """По ним фильтруют. В свободном тексте они искались бы регулярным выражением."""
    payload = formatted(record())

    assert payload["level"] == "ERROR"
    assert payload["logger"] == "apps.monitoring.services.tick"


def test_extra_becomes_a_field_of_its_own() -> None:
    """«Покажи всё по домену» должно быть запросом по полю, а не поиском по подстроке."""
    payload = formatted(record(domain="pay-gw.contoso.ru", component="redis"))

    assert payload["domain"] == "pay-gw.contoso.ru"
    assert payload["component"] == "redis"


def test_time_is_local_and_iso() -> None:
    """Q-D34: местное время, но **со смещением** — метка обязана остаться однозначной.

    Без смещения журнал нельзя ни сравнить с чужим, ни прочитать через полгода, когда пояс
    установки поменяли.
    """
    payload = formatted(record())
    stamp = str(payload["ts"])

    # ISO 8601 со смещением: `…+03:00`, `…+00:00` или `…-05:00`.
    assert re.fullmatch(r".+T.+[+-]\d{2}:\d{2}", stamp), stamp
    assert stamp.endswith(_offset_now())


def _offset_now() -> str:
    """Смещение пояса, в котором запущен процесс. В тестах это пояс `TIME_ZONE` настроек."""
    return datetime.now().astimezone().isoformat()[-6:]


def test_11_14_the_stamp_matches_the_message_scale() -> None:
    """Две шкалы в одной строке — худший вариант: их начинают сверять между собой.

    Метка `ts` и время внутри текста обязаны показывать одно и то же.
    """
    moment = datetime(2026, 9, 27, 9, 33, 10, tzinfo=UTC)

    assert describe(TickSkipped(reason="нечем"), moment) == (
        f"тик пропущен в {localtime(moment):%H:%M:%S}: нечем"
    )


def test_russian_text_stays_readable() -> None:
    """`ensure_ascii` превратил бы каждую строку в сплошные \\uXXXX."""
    line = JsonFormatter().format(record("список DNS-серверов пуст"))

    assert "список DNS-серверов пуст" in line


def test_exception_goes_into_its_own_field() -> None:
    try:
        raise ConnectionError("ни один сервер не ответил")
    except ConnectionError:
        made = record()
        made.exc_info = sys.exc_info()

    payload = formatted(made)

    assert "ConnectionError" in str(payload["error"])
    # Трассировка не должна размазаться по полю `message`.
    assert payload["message"] == "проверка домена упала"


def test_unserializable_value_does_not_break_the_line() -> None:
    """Журнал не имеет права падать из-за того, что в `extra` положили объект."""
    payload = formatted(record(domain=Domain(name="pay-gw.contoso.ru")))

    assert "pay-gw.contoso.ru" in str(payload["domain"])


# ─────────────── идентификатор обхода — 11.14 ───────────────


def test_11_14_run_id_lands_in_every_record() -> None:
    run = new_run()

    payload = formatted(record())

    assert payload["run"] == run


def test_11_14_outside_a_run_the_field_is_empty_not_absent() -> None:
    """Обработчик запроса тоже пишет в журнал, и поле должно быть на месте — пустым.

    Проверяется в отдельном потоке, и это не обход: поток стартует с пустым контекстом, то есть
    ровно в том положении, в каком живёт процесс `web` — он обходов не делает.
    """
    seen: list[object] = []
    thread = threading.Thread(target=lambda: seen.append(formatted(record())["run"]))
    thread.start()
    thread.join()

    assert seen == [""]


@pytest.mark.django_db
def test_11_14_each_tick_gets_its_own_identifier() -> None:
    """Иначе «все ошибки одного цикла» не отличить от ошибок соседнего."""
    clock = FakeClock(MOMENT)
    name = "pay-gw.contoso.ru"
    Domain.objects.create(name=name, next_check_at=MOMENT)
    resolver = FakeResolver({name: [answered("10.20.3.7")]})

    tick(at=MOMENT, resolver=resolver, state_store=FakeStateStore(clock), concurrency=1)
    first = current_run()
    Domain.objects.filter(name=name).update(next_check_at=MOMENT)
    tick(at=MOMENT, resolver=resolver, state_store=FakeStateStore(clock), concurrency=1)

    assert first
    assert current_run() != first
