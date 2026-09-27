"""
Отправка ошибок в Sentry — ТЗ 11.14; критерии 67, 69-74.

Критерий 68 (неверный формат DSN) закрыт проверкой окружения — `tests/config/test_env.py`:
приложение не стартует и называет переменную раньше, чем дело доходит до клиента.

Главная часть раздела 11.14 — перечень того, что **не отправляется никогда**. Поэтому больше
половины тестов здесь утверждают, что событий нет: «продукт про мониторинг устроен так, что
отказов в нём много, и почти все они не являются ошибками приложения».
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sentry_sdk
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import Domain, GlobalSettings
from apps.monitoring.models import Event, EventType
from apps.monitoring.services.runner import TickSkipped, run_one_tick
from apps.monitoring.services.tick import report_failures, tick
from core.adapters.fake_clock import FakeClock
from core.adapters.fake_resolver import FakeResolver, no_records
from core.adapters.fake_state_store import FakeStateStore
from core.observability.report import Failure, report
from core.observability.setup import configure_sentry
from core.ports.resolver import Resolver, ResolveResult
from core.types.dns import RecordType
from tests.observability.conftest import Recorder

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 26, 14, 38, tzinfo=UTC)
BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MOMENT)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


def flush() -> None:
    sentry_sdk.get_client().flush()


# ─────────────── 67: подключения нет ───────────────


def test_67_without_a_dsn_nothing_is_sent_and_nothing_breaks() -> None:
    """Критерий 67: приложение стартует и работает, попыток отправки нет."""
    enabled = configure_sentry(dsn=None, environment="test", traces_sample_rate=0.0)

    # Вызов отправки при выключенном Sentry — обычное дело: код не обязан знать о подключении.
    report(Failure(cause="check:ValueError", message="упало"))

    assert enabled is False


def test_67_empty_string_counts_as_not_configured() -> None:
    """Пустая строка в переменной — это «не задано», а не «задано пустым»."""
    assert configure_sentry(dsn="", environment="test", traces_sample_rate=0.0) is False


# ─────────────── 69: авария инфраструктуры ───────────────


def test_69_infrastructure_outage_reaches_sentry_with_the_component(
    sentry: Recorder, clock: FakeClock
) -> None:
    """Критерий 69: событие ушло с меткой отказавшего компонента."""
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["127.0.0.1"])
    Domain.objects.create(name="pay-gw.contoso.ru", next_check_at=MOMENT)
    store = FakeStateStore(clock, available=False)

    outcome = run_one_tick(clock=clock, state_store=store)
    flush()

    assert isinstance(outcome, TickSkipped)
    assert sentry.last["tags"]["cause"].startswith("infrastructure:")
    assert sentry.last["tags"]["component"] == "redis"


def test_69_outage_is_reported_once_per_incident_not_every_tick(
    sentry: Recorder, clock: FakeClock
) -> None:
    """Тик идёт раз в пять секунд. Событие на каждый был бы шумом, а не сигналом."""
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["127.0.0.1"])
    store = FakeStateStore(clock, available=False)

    healthy = True
    for _ in range(3):
        outcome = run_one_tick(clock=clock, state_store=store, previously_healthy=healthy)
        healthy = outcome.healthy if isinstance(outcome, TickSkipped) else True
    flush()

    assert len(sentry.events) == 1


# ─────────────── 70, 71: доменные отказы в Sentry не идут ───────────────


def test_70_nxdomain_goes_to_the_journal_and_not_to_sentry(
    sentry: Recorder, clock: FakeClock
) -> None:
    """Критерий 70: «это результат наблюдения, ради которого сервис и существует»."""
    GlobalSettings.objects.filter(pk=1).update(resolve_failure_threshold=1)
    name = "no-such-host.contoso.ru"
    Domain.objects.create(name=name, next_check_at=MOMENT)

    tick(
        at=MOMENT,
        resolver=FakeResolver({name: [no_records()]}),
        state_store=FakeStateStore(clock),
        concurrency=1,
    )
    flush()

    assert Event.objects.filter(event_type=EventType.RESOLVE_FAILED).exists()
    assert sentry.events == []


def test_71_bad_cidr_does_not_reach_sentry(
    sentry: Recorder, client: Client, admin: Member
) -> None:
    """Критерий 71: ошибка ввода — не ошибка приложения."""
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    client.force_login(admin)

    client.post(
        reverse("domain-save", args=[domain.pk]),
        {"allowlist": "10.20.999.0/24", "record_types": ["a"]},
        follow=True,
    )
    flush()

    assert sentry.events == []


def test_71_rejected_import_does_not_reach_sentry(
    sentry: Recorder, client: Client, admin: Member
) -> None:
    client.force_login(admin)

    client.post(reverse("domain-import-apply"), {"text": "cdn-2.contoso.ru; 10.20.999.0/24"})
    flush()

    assert sentry.events == []


def test_71_failed_login_does_not_reach_sentry(sentry: Recorder, client: Client) -> None:
    Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )

    client.post(reverse("login"), {"email": "boss@contoso.ru", "password": "неверный"})
    flush()

    assert sentry.events == []


# ─────────────── 72: группировка по причине ───────────────


class BrokenResolver(Resolver):
    """Резолвер, который не отвечает, а падает: так выглядит отказ всех DNS-серверов разом."""

    def resolve(self, name: str, record_types: frozenset[RecordType]) -> ResolveResult:
        del record_types
        raise ConnectionError(f"ни один сервер не ответил по {name}")


def test_72_a_tick_of_broken_domains_gives_one_event(sentry: Recorder, clock: FakeClock) -> None:
    """Критерий 72: обход, в котором упали все домены, даёт одно событие с их числом."""
    for number in range(5):
        Domain.objects.create(name=f"host-{number}.contoso.ru", next_check_at=MOMENT)

    outcome = tick(
        at=MOMENT,
        resolver=BrokenResolver(),
        state_store=FakeStateStore(clock),
        concurrency=1,
        limit=5,
    )
    flush()

    assert outcome.failed == 5
    assert len(sentry.events) == 1
    assert sentry.last["contexts"]["affected"]["count"] == 5


def test_72_five_hundred_domains_still_give_one_event(sentry: Recorder) -> None:
    """Критерий 72 дословно, с его числом. Группировка от числа доменов не зависит."""
    report_failures([ConnectionError("резолвер отказал") for _ in range(500)])
    flush()

    assert len(sentry.events) == 1
    assert sentry.last["contexts"]["affected"]["count"] == 500


def test_72_fingerprint_is_the_cause_not_the_object(sentry: Recorder) -> None:
    """Отпечаток не должен зависеть от домена: иначе группировка разъедется по объектам."""
    report(Failure(cause="check:Timeout", message="таймаут", tags={"domain": "a.contoso.ru"}))
    report(Failure(cause="check:Timeout", message="таймаут", tags={"domain": "b.contoso.ru"}))
    flush()

    fingerprints = {tuple(event["fingerprint"]) for event in sentry.events}
    assert fingerprints == {("check:Timeout",)}


def test_72_different_causes_stay_apart(sentry: Recorder) -> None:
    report(Failure(cause="check:Timeout", message="таймаут"))
    report(Failure(cause="infrastructure:redis", message="redis недоступен"))
    flush()

    fingerprints = {tuple(event["fingerprint"]) for event in sentry.events}
    assert len(fingerprints) == 2


# ─────────────── 73: недоступность Sentry ───────────────


@pytest.mark.usefixtures("broken_sentry")
def test_73_a_broken_sentry_does_not_break_the_caller() -> None:
    """Критерий 73: «Sentry никогда не влияет на работу приложения»."""
    # Ни исключения, ни зависания: вызвавший код продолжает работать.
    report(Failure(cause="check:ValueError", message="упало"))
    flush()


@pytest.mark.usefixtures("broken_sentry")
def test_73_checks_keep_running_while_sentry_refuses(clock: FakeClock) -> None:
    """Тот же критерий по существу: проверки идут, отчёт о тике приходит."""
    GlobalSettings.objects.filter(pk=1).update(dns_servers=["127.0.0.1"])
    Domain.objects.create(name="pay-gw.contoso.ru", next_check_at=MOMENT)

    outcome = run_one_tick(clock=clock, state_store=FakeStateStore(clock))
    flush()

    assert not isinstance(outcome, TickSkipped)
    assert outcome.claimed == 1


# ─────────────── 74: секретов в событии нет ───────────────


def test_74_secrets_never_leave_the_process(sentry: Recorder) -> None:
    """Критерий 74: ни токена бота, ни строк подключения, ни хешей, ни адресов участников."""
    report(
        Failure(
            cause="delivery:gave-up",
            message=(
                f"отказ отправки на api.telegram.org/bot{BOT_TOKEN}/sendMessage, "
                "подключение postgres://dns:s3cret@db:5432/dns, "
                "redis://:r3dis@cache:6379/0"
            ),
            tags={"notification": "17"},
        )
    )
    flush()

    sent = sentry.text()
    assert BOT_TOKEN not in sent
    assert "s3cret" not in sent
    assert "r3dis" not in sent
    # А имя хоста и номер уведомления остаются: без них ошибка нечитаема (11.14).
    assert "api.telegram.org" in sent
    assert "17" in sent


def test_74_member_email_is_not_sent(sentry: Recorder, admin: Member) -> None:
    """«Участник обозначается идентификатором, а не почтовым адресом» (11.14)."""
    with sentry_sdk.new_scope() as scope:
        scope.set_user({"id": str(admin.pk), "email": admin.email, "username": admin.name})
        sentry_sdk.capture_message("что-то пошло не так", level="error")
    flush()

    assert sentry.last["user"] == {"id": str(admin.pk)}
    assert admin.email not in sentry.text()


def test_74_login_body_does_not_ride_along_on_the_next_event(
    sentry: Recorder, client: Client, admin: Member
) -> None:
    """
    11.14: тело запроса к входу вырезается целиком — вместе с логином, а не только паролем.

    Тест написан по найденной утечке. Правило сначала смотрело на адрес обработчика, но в
    событие от Django поле `url` попадает не всегда: тогда оно молча не срабатывало, и почтовый
    адрес участника уезжал в Sentry внутри тела запроса к `/login/`.
    """
    client.post(reverse("login"), {"email": admin.email, "password": "неверный"})
    sentry_sdk.capture_message("что-то пошло не так", level="error")
    flush()

    assert admin.email not in sentry.text()
    assert "data" not in sentry.last.get("request", {})


def test_74_an_ordinary_request_body_is_kept(sentry: Recorder) -> None:
    """Обратная сторона: тело без учётных данных нужно для разбора ошибки и остаётся."""
    with sentry_sdk.new_scope() as scope:
        scope.add_event_processor(
            lambda event, hint: {
                **event,
                "request": {"url": "http://testserver/domains/", "data": {"names": "a.ru"}},
            }
        )
        sentry_sdk.capture_message("что-то пошло не так", level="error")
    flush()

    assert sentry.last["request"]["data"] == {"names": "a.ru"}


def test_74_domain_names_and_addresses_are_kept(sentry: Recorder) -> None:
    """Обратное правило 11.14: имена доменов и адреса не вырезаются."""
    report(
        Failure(
            cause="check:Timeout",
            message="pay-gw.contoso.ru не ответил, ожидался 10.20.3.7",
            tags={"domain": "pay-gw.contoso.ru"},
        )
    )
    flush()

    sent = sentry.text()
    assert "pay-gw.contoso.ru" in sent
    assert "10.20.3.7" in sent
