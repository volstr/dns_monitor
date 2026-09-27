"""
Каждый экран просто открывается — ответ 200 и ничего больше.

Тест намеренно глупый. Он не проверяет содержимое, не знает про требования ТЗ и ничего не
утверждает о правильности — только то, что страница не упала. Именно поэтому он полезен:
подробные тесты проверяют своё и молчат, когда ломается что-то общее — селектор, шаблон,
маршрут, подпись функции. Такая поломка видна человеку сразу, а набору тестов — не всегда.

**Чего этот тест не ловит.** Он не увидит поломку работающего процесса: `pytest` импортирует
модули заново, а `runserver` держит те, что загрузил при старте. Если правка оставила файл в
несогласованном состоянии и перезагрузка поймала его в этот момент, тесты будут зелёными, а
живое приложение — лежать. Такое уже случалось трижды; лечится это не тестом, а взглядом в
`docker compose logs web` после правок, трогающих имена и подписи.

Вкладки и фильтры перечислены из самих перечислений, а не списком строк: добавится значение —
попадёт под проверку само, без правки теста.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain, Group
from apps.catalog.selectors.domain_card import HistoryFilter, RegistryFilter
from apps.catalog.selectors.domains import DomainFilter
from apps.monitoring.models import AddressRegistryEntry, Check, Event, EventType
from apps.monitoring.selectors.journal import JournalFilter, JournalPeriod
from core.types.checking import CheckOutcome

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)
OK = 200


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email="boss@contoso.ru", name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN
    )


@pytest.fixture
def observer() -> Member:
    return Member.objects.create_member(
        email="watch@contoso.ru", name="Наблюдатель", created_at=MOMENT, role=Role.OBSERVER
    )


@pytest.fixture
def domain() -> Domain:
    """
    Домен с заполненной обвязкой: список, реестр, проверка, событие.

    Пустой домен проходит слишком много ветвлений мимо — а ломаются как раз те, где есть данные.
    Отметки времени привязаны к настоящему моменту: обработчик берёт время у `SystemClock`, и с
    фиксированной датой адрес однажды пересёк бы порог актуальности и сменил статус.
    """
    seen = timezone.now() - timedelta(minutes=5)
    group = Group.objects.create(name="Платёжный контур")
    domain = Domain.objects.create(
        name="pay-gw.contoso.ru", group=group, last_successful_check_at=seen
    )
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    # Два адреса: один внутри списка, другой вне — чтобы ожили обе ветки реестра.
    for address in ("10.20.1.14", "185.44.9.201"):
        AddressRegistryEntry.objects.create(
            domain=domain, address=address, first_seen_at=seen, last_seen_at=seen
        )
    Check.objects.create(domain=domain, checked_at=seen, outcome=CheckOutcome.NORMAL)
    Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=seen,
        payload={"addresses": ["185.44.9.201"]},
    )
    return domain


def screens(domain: Domain) -> list[tuple[str, str]]:
    """Пары «название — адрес». Название попадает в сообщение о падении."""
    group_id = domain.group_id
    return [
        ("Обзор", reverse("overview")),
        ("Домены", reverse("domain-list")),
        ("Добавить домен", reverse("domain-new")),
        ("Импорт списком", reverse("domain-import")),
        ("Карточка домена", reverse("domain-detail", args=[domain.pk])),
        ("Удаление домена", reverse("domain-delete", args=[domain.pk])),
        ("Перенос домена", reverse("domain-move", args=[domain.pk])),
        ("Группы", reverse("group-list")),
        ("Новая группа", reverse("group-new")),
        ("Настройка группы", reverse("group-edit", args=[group_id])),
        ("Домены группы", reverse("group-domains", args=[group_id])),
        ("Журнал", reverse("event-list")),
        ("Выгрузка журнала", reverse("event-export")),
        ("Настройки", reverse("settings")),
        ("Участники", reverse("member-list")),
    ]


def test_every_screen_opens_for_an_administrator(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    broken = [
        f"{name} ({url}) → {client.get(url).status_code}"
        for name, url in screens(domain)
        if client.get(url).status_code != OK
    ]

    assert broken == []


def test_screens_open_for_an_observer_or_refuse_him_cleanly(
    client: Client, observer: Member, domain: Domain
) -> None:
    """
    Наблюдателю часть экранов закрыта (ТЗ 3) — но закрыта отказом, а не поломкой.

    Ровно это и проверяется: 200 либо 403, но никогда 500.
    """
    client.force_login(observer)
    allowed = {OK, 403}

    broken = [
        f"{name} ({url}) → {client.get(url).status_code}"
        for name, url in screens(domain)
        if client.get(url).status_code not in allowed
    ]

    assert broken == []


def test_public_screens_open_without_signing_in(client: Client) -> None:
    """Экраны до входа: без них не войти вовсе."""
    for url in (reverse("login"), reverse("healthcheck")):
        assert client.get(url).status_code == OK, url


@pytest.mark.parametrize("chosen", list(DomainFilter))
def test_every_domain_list_filter_opens(
    client: Client, admin: Member, domain: Domain, chosen: DomainFilter
) -> None:
    client.force_login(admin)

    assert client.get(reverse("domain-list"), {"chosen": chosen.value}).status_code == OK


@pytest.mark.parametrize("registry", list(RegistryFilter))
def test_every_registry_tab_opens(
    client: Client, admin: Member, domain: Domain, registry: RegistryFilter
) -> None:
    client.force_login(admin)
    url = reverse("domain-detail", args=[domain.pk])

    assert client.get(url, {"registry": registry.value}).status_code == OK


@pytest.mark.parametrize("history", list(HistoryFilter))
def test_every_history_tab_opens(
    client: Client, admin: Member, domain: Domain, history: HistoryFilter
) -> None:
    client.force_login(admin)
    url = reverse("domain-detail", args=[domain.pk])

    assert client.get(url, {"history": history.value}).status_code == OK


@pytest.mark.parametrize("chosen", list(JournalFilter))
def test_every_journal_filter_opens(
    client: Client, admin: Member, domain: Domain, chosen: JournalFilter
) -> None:
    client.force_login(admin)

    assert client.get(reverse("event-list"), {"chosen": chosen.value}).status_code == OK


@pytest.mark.parametrize("period", list(JournalPeriod))
def test_every_journal_period_opens(
    client: Client, admin: Member, domain: Domain, period: JournalPeriod
) -> None:
    client.force_login(admin)

    assert client.get(reverse("event-list"), {"period": period.value}).status_code == OK


def test_an_empty_installation_opens_too(client: Client, admin: Member) -> None:
    """
    Свежая установка — отдельный случай: ни доменов, ни групп, ни событий.

    Ветки «показать пусто» ломаются не реже остальных, а заметить это некому: на установке с
    данными они не выполняются вовсе.
    """
    client.force_login(admin)

    for name, url in [
        ("Обзор", reverse("overview")),
        ("Домены", reverse("domain-list")),
        ("Группы", reverse("group-list")),
        ("Журнал", reverse("event-list")),
        ("Настройки", reverse("settings")),
        ("Участники", reverse("member-list")),
    ]:
        assert client.get(url).status_code == OK, name
