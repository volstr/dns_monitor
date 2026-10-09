"""
Экран «Домены» — ТЗ 11.5, 8.2; решения Q-D15, Q-C22.

Проверяется поведение экрана, а не вёрстка: какие домены попадают в выборку, что написано в
колонках и сколько запросов уходит в базу.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Member, Role
from apps.catalog.models import (
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.catalog.selectors.domains import DomainFilter, domain_list_page
from apps.monitoring.models import AddressRegistryEntry, Check, Incident
from core.types.checking import CheckOutcome

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)


@pytest.fixture
def admin() -> Member:
    return Member.objects.create_member(
        email='a.kirilova@contoso.ru', name='Анна Кирилова', created_at=MOMENT, role=Role.ADMIN
    )


def make_domain(name: str, **extra: object) -> Domain:
    return Domain.objects.create(name=name, **extra)


def seen(domain: Domain, address: str, *, ago: timedelta = timedelta(minutes=1)) -> None:
    AddressRegistryEntry.objects.create(
        domain=domain, address=address, first_seen_at=MOMENT - ago, last_seen_at=MOMENT - ago
    )


def page(**kwargs: object) -> object:
    return domain_list_page(at=MOMENT, **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _threshold_of_one_day() -> None:
    """
    Порог актуальности прибит к суткам, а не взят из умолчания.

    Умолчание — 14 суток (Q-D47). Тест, который строит неактуальный адрес или показывает
    порог на экране, обязан называть своё значение сам: иначе он молча меняет смысл вместе с
    настройкой продукта, а падает совсем в другом месте.
    """
    GlobalSettings.objects.filter(pk=1).update(freshness_threshold_seconds=24 * 60 * 60)


def test_d15_no_allowlist_filter_shows_every_domain_with_an_empty_list() -> None:
    """
    Q-D15: фильтр по пустому списку, а не по ловушке.

    Пустой список в режиме «При изменении адресов» — законная настройка, и она тоже сюда попадает.
    """
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.ADDRESS_CHANGE)
    empty = make_domain('cdn.contoso.ru')
    filled = make_domain('pay-gw.contoso.ru')
    AllowlistEntry.objects.create(domain=filled, entry='10.20.0.0/22', raw='10.20.0.0/22')

    rows = domain_list_page(at=MOMENT, chosen=DomainFilter.NO_ALLOWLIST).rows

    assert [row.name for row in rows] == [empty.name]


def test_8_2_trap_is_marked_only_when_notifications_would_never_come() -> None:
    """ТЗ 8.2: помечается сочетание «пустой список + выход из разрешённых», а не пустой список."""
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NOT_ALLOWED)
    trapped = make_domain('trapped.contoso.ru')
    quiet = Group.objects.create(
        name='Только изменения', notification_mode=NotificationMode.ADDRESS_CHANGE
    )
    legitimate = make_domain('legit.contoso.ru', group=quiet)

    rows = {row.name: row for row in domain_list_page(at=MOMENT).rows}

    assert rows[trapped.name].silent_trap is True
    assert rows[legitimate.name].silent_trap is False


def test_8_2_empty_allowlist_is_not_a_breach() -> None:
    """Q-D25: сравнивать не с чем, поэтому не «не решён», а «ничего не задано»."""
    domain = make_domain('cdn.contoso.ru')
    seen(domain, '185.44.9.201')

    row = domain_list_page(at=MOMENT).rows[0]

    assert row.matching.label == 'Ничего не задано'
    assert row.matching.tone == ''
    assert [chip.css for chip in row.addresses] == ['']


def test_6_4_unmatched_address_is_counted_and_marked() -> None:
    domain = make_domain('pay-gw.contoso.ru')
    AllowlistEntry.objects.create(domain=domain, entry='10.20.0.0/22', raw='10.20.0.0/22')
    seen(domain, '10.20.1.14')
    seen(domain, '185.44.9.201')

    row = domain_list_page(at=MOMENT).rows[0]

    assert row.matching.label == '1 не решён'
    assert row.matching.tone == 'breach'
    assert {chip.address: chip.css for chip in row.addresses} == {
        '10.20.1.14': '',
        '185.44.9.201': 'out',
    }


def test_11_5_single_entry_is_shown_as_is_and_many_are_counted() -> None:
    """Прототип: у одной записи видна она сама, у нескольких — их число."""
    one = make_domain('vpn.hq.contoso.ru')
    AllowlistEntry.objects.create(domain=one, entry='10.30.4.0/24', raw='10.30.4.0/24')
    seen(one, '10.30.4.8')

    many = make_domain('api.contoso.ru')
    for octet in range(4):
        AllowlistEntry.objects.create(
            domain=many, entry=f'10.4{octet}.0.0/16', raw=f'10.4{octet}.0.0/16'
        )
    seen(many, '10.40.1.9')

    rows = {row.name: row for row in domain_list_page(at=MOMENT).rows}

    assert rows[one.name].matching.detail == '10.30.4.0/24'
    assert rows[many.name].matching.detail == '4 записи в списке'


def test_6_2_stale_address_is_struck_through_not_breached() -> None:
    """Порог актуальности прошёл: адрес «Не актуален», вопрос о вхождении не задаётся (6.4)."""
    domain = make_domain('legacy.contoso.ru')
    AllowlistEntry.objects.create(domain=domain, entry='10.20.0.0/22', raw='10.20.0.0/22')
    seen(domain, '185.44.9.201', ago=timedelta(days=2))

    row = domain_list_page(at=MOMENT).rows[0]

    assert [chip.css for chip in row.addresses] == ['gone']
    assert row.matching.tone == 'ok'


def test_c22_address_from_the_latest_check_is_marked_new() -> None:
    domain = make_domain('cdn.contoso.ru', last_successful_check_at=MOMENT - timedelta(minutes=2))
    AllowlistEntry.objects.create(domain=domain, entry='93.184.216.0/24', raw='93.184.216.0/24')
    seen(domain, '93.184.216.34', ago=timedelta(minutes=1))
    seen(domain, '93.184.216.35', ago=timedelta(days=1))

    row = domain_list_page(at=MOMENT).rows[0]

    assert {chip.address: chip.css for chip in row.addresses} == {
        '93.184.216.34': 'new',
        '93.184.216.35': '',
    }


def test_11_5_discrepancies_filter_follows_open_incidents() -> None:
    """«Расхождение» — открытый инцидент, как на обзоре (11.9)."""
    open_one = make_domain('pay-gw.contoso.ru')
    Incident.objects.create(domain=open_one, opened_at=MOMENT)
    closed = make_domain('api.contoso.ru')
    Incident.objects.create(domain=closed, opened_at=MOMENT, closed_at=MOMENT)

    rows = domain_list_page(at=MOMENT, chosen=DomainFilter.DISCREPANCIES).rows

    assert [row.name for row in rows] == [open_one.name]


def test_11_5_disabled_filter_and_marking() -> None:
    make_domain('pay-gw.contoso.ru')
    off = make_domain('status.contoso.ru', watch_enabled=False)

    rows = domain_list_page(at=MOMENT, chosen=DomainFilter.DISABLED).rows

    assert [row.name for row in rows] == [off.name]
    assert rows[0].watch_enabled is False


def test_search_finds_a_domain_by_an_address_from_the_registry() -> None:
    """Подпись поля в прототипе — «домен или IP»."""
    domain = make_domain('pay-gw.contoso.ru')
    seen(domain, '185.44.9.201')
    make_domain('api.contoso.ru')

    rows = domain_list_page(at=MOMENT, search='185.44.9.201').rows

    assert [row.name for row in rows] == [domain.name]


def test_search_by_name_ignores_incomplete_addresses() -> None:
    """`10.20` — не адрес; как часть имени искать осмысленно, как адрес — нет."""
    make_domain('pay-gw.contoso.ru')

    assert domain_list_page(at=MOMENT, search='pay').rows[0].name == 'pay-gw.contoso.ru'
    assert domain_list_page(at=MOMENT, search='10.20').rows == ()


def test_5_inheritance_origin_reaches_the_row() -> None:
    """ТЗ 5: происхождение значения приходит готовым, шаблон его не вычисляет."""
    group = Group.objects.create(name='Платёжный контур', check_period_seconds=5 * 60)
    make_domain('pay-gw.contoso.ru', group=group)
    make_domain('free.contoso.ru')

    rows = {row.name: row for row in domain_list_page(at=MOMENT).rows}

    inherited = rows['pay-gw.contoso.ru']
    assert (inherited.check_period, inherited.check_period_origin) == ('5 мин', 'group')
    assert rows['free.contoso.ru'].check_period_origin == 'global'


def test_11_5_history_keeps_twenty_marks_oldest_first() -> None:
    domain = make_domain('pay-gw.contoso.ru')
    for index, outcome in enumerate([CheckOutcome.NORMAL, CheckOutcome.HAS_UNMATCHED]):
        Check.objects.create(
            domain=domain, checked_at=MOMENT - timedelta(minutes=10 - index), outcome=outcome
        )

    history = domain_list_page(at=MOMENT).rows[0].history

    assert len(history.marks) == 20
    # Недостающие отметки дополняются слева, последняя проверка — справа.
    assert history.marks[-2:] == ('e-ok', 'e-breach')
    assert history.marks[0] == ''
    assert history.last_checked_at == MOMENT - timedelta(minutes=9)


def test_11_5_history_shows_only_the_last_twenty_checks() -> None:
    domain = make_domain('pay-gw.contoso.ru')
    for index in range(25):
        Check.objects.create(
            domain=domain,
            checked_at=MOMENT - timedelta(minutes=index + 1),
            outcome=CheckOutcome.NORMAL if index else CheckOutcome.RESOLVE_ERROR,
        )

    history = domain_list_page(at=MOMENT).rows[0].history

    assert len(history.marks) == 20
    assert history.marks[-1] == 'e-fail'
    assert '' not in history.marks


def test_query_count_does_not_grow_with_the_number_of_domains() -> None:
    """
    Главное требование к списку: запросов поровну на один домен и на два десятка.

    Иначе колонка «История» и наследование настроек дали бы по запросу на строку.
    """
    group = Group.objects.create(name='Платёжный контур', check_period_seconds=5 * 60)
    single = make_domain('only.contoso.ru', group=group)
    AllowlistEntry.objects.create(domain=single, entry='10.20.0.0/22', raw='10.20.0.0/22')
    seen(single, '10.20.1.14')
    Check.objects.create(domain=single, checked_at=MOMENT, outcome=CheckOutcome.NORMAL)

    with CaptureQueriesContext(connection) as first:
        domain_list_page(at=MOMENT)

    for index in range(20):
        extra = make_domain(f'domain-{index}.contoso.ru', group=group)
        AllowlistEntry.objects.create(domain=extra, entry='10.20.0.0/22', raw='10.20.0.0/22')
        seen(extra, f'10.20.1.{index + 20}')
        Check.objects.create(domain=extra, checked_at=MOMENT, outcome=CheckOutcome.NORMAL)

    with CaptureQueriesContext(connection) as second:
        rows = domain_list_page(at=MOMENT).rows

    assert len(rows) == 21
    assert len(second) == len(first)


def test_an_unknown_outcome_does_not_bring_down_the_domain_list() -> None:
    """
    Строка исхода приходит из базы и может быть записана прежней версией кода.

    Так и случилось при переименовании `no_allowlist` в `not_confirmed` (Q-D37): миграция
    перенесла прежние строки, а `run_checker` — долгоживущая команда, она не перезагружается
    сама и продолжала писать старое значение уже после миграции. Один такой ряд валил весь
    список доменов пятисотой ошибкой.
    """
    domain = Domain.objects.create(name='pay-gw.contoso.ru', next_check_at=MOMENT)
    Check.objects.create(domain=domain, checked_at=MOMENT, outcome='no_allowlist')

    page = domain_list_page(at=MOMENT)

    assert len(page.rows) == 1
    # Незнакомое значение показывается пустой отметкой, а не притворяется нормой.
    assert page.rows[0].history.marks[-1] == ''
