"""
Порт хранилища рабочего состояния — ТЗ 11.11.

**Ключ без срока жизни здесь невозможно выразить.** Запись принимает не строку, а ``StateKey``,
который несёт срок жизни в себе, поэтому «забыть TTL» нельзя даже синтаксически — именно этого
требует `CLAUDE.md` от обёртки над клиентом.

Сроки и имена ключей взяты из таблицы 11.11 дословно. Два ключа добавлены сверх неё осознанно и
записаны решениями: ``auth:invite:*`` (Q-D16) и ``session:*`` (Q-D20).

Это **рабочее состояние**, а не кэш: оно живёт в логической базе ``REDIS_DB_STATE`` с политикой
``noeviction``. Кэш по 11.12 лежит отдельно и сбрасывается целиком, чего с состоянием делать
нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

# Сроки из таблицы 11.11.
LOGIN_WINDOW = timedelta(minutes=15)
OTP_STEP_WINDOW = timedelta(seconds=90)
DOMAIN_COOLDOWN = timedelta(seconds=30)
GROUP_COOLDOWN = timedelta(seconds=60)
SCHEDULER_LOCK = timedelta(seconds=30)
MERGE_WINDOW = timedelta(seconds=90)


@dataclass(frozen=True, slots=True)
class StateKey:
    """Ключ вместе со сроком жизни. Одно без другого не существует (11.11)."""

    name: str
    ttl: timedelta


def login_failures(login_hash: str, ip: str) -> StateKey:
    """Счётчик неудачных попыток входа. Логин хешируется (11.11)."""
    return StateKey(f"auth:fail:{login_hash}:{ip}", LOGIN_WINDOW)


def login_lock(login_hash: str, ip: str) -> StateKey:
    return StateKey(f"auth:lock:{login_hash}:{ip}", LOGIN_WINDOW)


def used_otp(member_id: int, step: int) -> StateKey:
    """Отметка об использованном коде TOTP: один код дважды не принимается (ТЗ 3)."""
    return StateKey(f"auth:otp:{member_id}:{step}", OTP_STEP_WINDOW)


def domain_cooldown(domain_id: int) -> StateKey:
    return StateKey(f"check:cooldown:domain:{domain_id}", DOMAIN_COOLDOWN)


def group_cooldown(group_id: int) -> StateKey:
    return StateKey(f"check:cooldown:group:{group_id}", GROUP_COOLDOWN)


def scheduler_lock(domain_id: int) -> StateKey:
    """Блокировка домена на время проверки: два экземпляра не резолвят его одновременно (11.1)."""
    return StateKey(f"sched:lock:domain:{domain_id}", SCHEDULER_LOCK)


def merge_window(channel: str, event_type: str) -> StateKey:
    return StateKey(f"notify:merge:{channel}:{event_type}", MERGE_WINDOW)


def invitation_token(invitation_id: int, lifetime: timedelta) -> StateKey:
    """Открытый текст токена приглашения — Q-D16. Срок равен сроку действия ссылки."""
    return StateKey(f"auth:invite:{invitation_id}", lifetime)


def session(session_key: str, lifetime: timedelta) -> StateKey:
    """Сессия — Q-D20. Живёт в базе состояния, а не в кэше: сброс `cache:*` её не трогает."""
    return StateKey(f"session:{session_key}", lifetime)


class StateStore(Protocol):
    def acquire(self, key: StateKey) -> bool:
        """
        Занять ключ, если он свободен. ``False`` — уже занят.

        Одна операция обслуживает и блокировку планировщика, и кулдаун: кулдаун — это тот же
        захват, только отказ в нём означает «ещё рано» (11.6).
        """
        ...

    def release(self, key: StateKey) -> None:
        """Освободить явно. Срок жизни остаётся страховкой на случай падения экземпляра (11.11)."""
        ...

    def is_held(self, key: StateKey) -> bool:
        """Занят ли ключ. Для показа кулдауна, без попытки его занять."""
        ...

    def accumulate(self, key: StateKey, item: str) -> int:
        """
        Добавить элемент в накопитель окна и вернуть их общее число.

        Нужен окну слияния (8.6): сводка содержит и число затронутых доменов, и перечисление до
        десяти имён, поэтому одного счётчика мало.

        **Срок жизни ставится только при первом добавлении**, окно фиксированное (11.11). Иначе
        поток событий продлевал бы окно бесконечно, и сводка не ушла бы никогда.
        """
        ...

    def collected(self, key: StateKey) -> tuple[str, ...]:
        """Что накопилось в окне, в порядке добавления."""
        ...

    def remember(self, key: StateKey, value: str) -> None:
        """
        Записать значение под ключом, обновив срок жизни.

        Нужно сессиям (Q-D20): замок хранит только факт, а сессия — содержимое. Срок обновляется
        при каждой записи, и это не оплошность, а требование ТЗ 3: срок сессии отсчитывается от
        бездействия («через сколько бездействия потребуется войти заново»), поэтому окно здесь
        скользящее — в отличие от счётчика входа, где оно фиксированное (11.11).
        """
        ...

    def recall(self, key: StateKey) -> str | None:
        """Прочитать значение. ``None`` — ключа нет или срок истёк."""
        ...

    def ping(self) -> None:
        """Проверка доступности. Бросает исключение, если хранилище недоступно (11.13)."""
        ...
