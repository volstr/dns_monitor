"""Вход — ТЗ 3, 11.11; критерии 32, 33, 50, 51, 52, 56, 57.

Отказ — штатный исход и возвращается результатом с причиной, а не исключением (`CLAUDE.md`).
Причина при этом **не выдаёт, существует ли учётная запись**: счёт неудач идёт по паре
«логин + IP» независимо от того, есть такой участник или нет (Q-C19), и текст отказа одинаков.

**Счётчик живёт в Redis, а не в БД** (11.11): это эфемерные данные с естественным сроком жизни,
и складывать их в таблицу значит заводить строки, которые потом придётся чистить заданием.
Логин в ключе хешируется, иначе содержимое Redis становится перечнем почтовых адресов
участников в открытом виде.

**Окно фиксированное.** Срок ставится только при первом увеличении: если продлевать его при
каждой неудаче, редкие опечатки легитимного человека накопятся за месяцы и однажды заблокируют
вход на ровном месте (11.11, критерий 57). Это же свойство даёт `accumulate` у порта состояния.

Второй фактор включается переменной окружения, а не настройкой (3): администратор приложения не
должен иметь возможности отключить его для всех, войдя под угнанной учётной записью.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

import pyotp
from django.contrib.auth.hashers import check_password

from apps.accounts.models import Member
from apps.catalog.models import GlobalSettings
from core.ports.state_store import StateStore, login_failures, login_lock, used_otp

#: Шаг кода TOTP в секундах. Тот же, что у приложений-аутентификаторов.
OTP_STEP_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Authenticated:
    """Пароль и второй фактор приняты, доступ открыт."""

    member_id: int


@dataclass(frozen=True, slots=True)
class PasswordChangeRequired:
    """Флаг «сменить пароль при первом входе» — критерий 32."""

    member_id: int


@dataclass(frozen=True, slots=True)
class TotpSetupRequired:
    """Пароль верен, но аутентификатор не привязан — критерий 51."""

    member_id: int


@dataclass(frozen=True, slots=True)
class Rejected:
    """Неверная пара или код. Сколько попыток осталось — показывается одинаково всем (Q-C19)."""

    attempts_left: int


@dataclass(frozen=True, slots=True)
class Locked:
    """Вход заблокирован на 15 минут — критерии 33, 56."""


type LoginResult = Authenticated | PasswordChangeRequired | TotpSetupRequired | Rejected | Locked


def authenticate(
    *,
    email: str,
    password: str,
    code: str,
    ip: str,
    at: datetime,
    state_store: StateStore,
    two_factor_enabled: bool,
) -> LoginResult:
    """Проверить вход. Ни одна ветка не сообщает, существует ли учётная запись."""
    login_hash = _hashed(email)
    if state_store.is_held(login_lock(login_hash, ip)):
        return Locked()

    member = Member.objects.filter(email=email.strip().lower()).first()
    if member is None or not member.is_active or not check_password(password, member.password):
        # Хеш всё равно считается у несуществующего участника: иначе время ответа выдало бы,
        # есть такая почта или нет.
        return _failed(login_hash, ip, state_store)

    if two_factor_enabled:
        if not member.totp_secret:
            return TotpSetupRequired(member_id=member.pk)
        if not _accept_code(member, code, at, state_store):
            return _failed(login_hash, ip, state_store)

    if member.must_change_password:
        return PasswordChangeRequired(member_id=member.pk)

    return Authenticated(member_id=member.pk)


def confirm_totp_binding(
    *, member_id: int, secret: str, code: str, at: datetime, state_store: StateStore
) -> bool:
    """Подтвердить привязку аутентификатора первым кодом (ТЗ 3)."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        return False
    if not _valid_code(secret, code, at):
        return False

    _mark_used(member.pk, code, at, state_store)
    Member.objects.filter(pk=member_id).update(totp_secret=secret)
    return True


def new_totp_secret() -> str:
    return str(pyotp.random_base32())


def provisioning_uri(*, secret: str, email: str) -> str:
    """Строка для QR-кода. Рисует её приложение-аутентификатор, не мы."""
    return str(pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="DNS monitoring"))


def _accept_code(member: Member, code: str, at: datetime, state_store: StateStore) -> bool:
    """Код верен и ещё не использован. Критерий 52: один код дважды не принимается."""
    if not _valid_code(member.totp_secret, code, at):
        return False
    return _mark_used(member.pk, code, at, state_store)


def _valid_code(secret: str, code: str, at: datetime) -> bool:
    # valid_window=1 прощает рассинхронизацию часов на один шаг в обе стороны.
    return bool(pyotp.TOTP(secret).verify(code.strip(), for_time=at, valid_window=1))


def _mark_used(member_id: int, code: str, at: datetime, state_store: StateStore) -> bool:
    """Отметить код использованным. ``False`` — его уже предъявляли (11.11, критерий 52)."""
    step = int(at.timestamp()) // OTP_STEP_SECONDS
    return state_store.acquire(used_otp(member_id, step))


def _failed(login_hash: str, ip: str, state_store: StateStore) -> Rejected | Locked:
    """Увеличить счётчик и, если порог достигнут, заблокировать на 15 минут."""
    threshold = GlobalSettings.objects.get().login_failure_threshold
    attempts = state_store.accumulate(login_failures(login_hash, ip), "1")

    if attempts >= threshold:
        # Блокировка снимается сама истечением срока ключа: отдельного задания быть не должно
        # (11.11, критерий 56).
        state_store.acquire(login_lock(login_hash, ip))
        return Locked()

    return Rejected(attempts_left=threshold - attempts)


def _hashed(email: str) -> str:
    """Логин в ключе Redis хешируется (11.11)."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
