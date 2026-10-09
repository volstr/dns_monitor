"""
Верхняя сводка обзора — разметка прототипа (строки 738–743), ТЗ 11.9.

Четыре карточки про **состояние**, а не про опись установки. Проверяется и обратное: при
спокойном состоянии карточки не должны краснеть, иначе цвет перестаёт что-либо значить.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group
from apps.monitoring.models import AddressRegistryEntry, Event, EventType, Incident
from apps.monitoring.selectors.overview import overview

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)


def cards(at: datetime = NOW) -> dict[str, tuple[int, str, str]]:
    """Карточки по подписи: значение, пояснение, тон."""
    return {stat.label: (stat.value, stat.note, stat.tone) for stat in overview(at=at).stats}


def with_discrepancy(
    name: str = 'pay-gw.contoso.ru', opened: timedelta = timedelta(hours=3)
) -> Domain:
    """
    Домен с настоящим расхождением: список задан, а отданный адрес в него не входит.

    Список обязателен. Без него адрес получает статус «не проверяется», а не «не решён» (Q-D25),
    и расхождения не возникает вовсе — на этом и спотыкались первые версии тестов.
    """
    domain = Domain.objects.create(name=name, last_successful_check_at=NOW)
    AllowlistEntry.objects.create(domain=domain, entry='10.20.0.0/22', raw='10.20.0.0/22')
    AddressRegistryEntry.objects.create(
        domain=domain, address='185.44.9.201', first_seen_at=NOW, last_seen_at=NOW
    )
    Incident.objects.create(domain=domain, opened_at=NOW - opened)
    return domain


def test_11_9_four_cards_in_the_order_of_the_prototype() -> None:
    labels = [stat.label for stat in overview(at=NOW).stats]

    assert labels == ['Под наблюдением', 'Не решены', 'Адреса изменились', 'Не резолвится']


def test_watched_counts_only_domains_under_watch() -> None:
    """Приостановленный домен не наблюдается — значит и в счётчике ему не место (11.1)."""
    Group.objects.create(name='Платёжный контур')
    Domain.objects.create(name='pay-gw.contoso.ru')
    Domain.objects.create(name='old.contoso.ru', watch_enabled=False)

    value, note, _ = cards()['Под наблюдением']

    assert value == 1
    assert note == 'в 1 группе'


def test_11_9_the_unresolved_card_names_the_domain() -> None:
    """
    11.9: «счётчик не говорит ни какой домен, ни как давно».

    Число само по себе — ровно то, что раздел называет недостаточным, поэтому под ним имя.
    """
    with_discrepancy()

    value, note, tone = cards()['Не решены']

    assert (value, note, tone) == (1, 'pay-gw.contoso.ru', 's-breach')


def test_the_unresolved_card_names_the_oldest_and_counts_the_rest() -> None:
    """Разбирать начинают со старшего, поэтому назван он, а не случайный."""
    with_discrepancy('pay-gw.contoso.ru', opened=timedelta(hours=5))
    with_discrepancy('api.contoso.ru', opened=timedelta(minutes=5))

    value, note, _ = cards()['Не решены']

    assert (value, note) == (2, 'pay-gw.contoso.ru и ещё 1')


def test_changed_card_counts_domains_not_events() -> None:
    """Домен, у которого за сутки сменилось пять адресов, — это один изменившийся домен."""
    domain = Domain.objects.create(name='pay-gw.contoso.ru')
    for _ in range(5):
        Event.objects.create(
            domain=domain, event_type=EventType.ADDRESS_APPEARED, occurred_at=NOW
        )

    value, note, tone = cards()['Адреса изменились']

    assert (value, note, tone) == (1, 'за последние сутки', 's-change')


def test_changed_card_ignores_what_happened_earlier_than_a_day_ago() -> None:
    domain = Domain.objects.create(name='pay-gw.contoso.ru')
    Event.objects.create(
        domain=domain,
        event_type=EventType.ADDRESS_APPEARED,
        occurred_at=NOW - timedelta(days=2),
    )

    assert cards()['Адреса изменились'][0] == 0


def test_failing_card_uses_the_threshold_from_settings() -> None:
    """Домен считается нерезолвящимся по тому же порогу, что и событие `RESOLVE_FAILED` (7.1)."""
    GlobalSettings.objects.filter(pk=1).update(resolve_failure_threshold=3)
    Domain.objects.create(name='gone.contoso.ru', consecutive_resolve_failures=4)
    Domain.objects.create(name='ok.contoso.ru', consecutive_resolve_failures=1)

    value, note, tone = cards()['Не резолвится']

    assert (value, note, tone) == (1, '4 попытки подряд', 's-fail')


def test_a_calm_installation_has_no_alarming_colours() -> None:
    """Ноль не должен кричать: иначе цвет перестаёт отличать беду от покоя."""
    Domain.objects.create(name='pay-gw.contoso.ru')

    tones = {stat.label: stat.tone for stat in overview(at=NOW).stats}

    assert set(tones.values()) == {''}
    assert cards()['Не решены'][1] == 'расхождений нет'
    assert cards()['Не резолвится'][1] == 'все домены отвечают'


def test_an_empty_installation_does_not_break_the_summary() -> None:
    """Свежая установка: ни доменов, ни групп, ни событий."""
    stats = overview(at=NOW).stats

    assert [stat.value for stat in stats] == [0, 0, 0, 0]


# ─────────────── расхождение «сейчас» против открытой записи — Q-D39 ───────────────


def resolved_domain() -> Domain:
    """
    Домен с открытым инцидентом, у которого нерешённых адресов уже не осталось.

    Так выглядит установка сразу после того, как администратор разрешил непрошеный адрес:
    вхождение считается на лету, а запись инцидента закроет проверка (Q-13.2).
    """
    domain = Domain.objects.create(name='pay-gw.contoso.ru', last_successful_check_at=NOW)
    AllowlistEntry.objects.create(domain=domain, entry='185.44.9.201/32', raw='185.44.9.201')
    AddressRegistryEntry.objects.create(
        domain=domain, address='185.44.9.201', first_seen_at=NOW, last_seen_at=NOW
    )
    Incident.objects.create(domain=domain, opened_at=NOW - timedelta(hours=1))
    return domain


def test_d39_a_fixed_incident_leaves_the_discrepancy_block() -> None:
    """
    Блок называется «Открытые расхождения», а расхождения больше нет.

    Раньше строка оставалась и не могла назвать ни одного адреса: обзор утверждал расхождение,
    а список доменов в то же время показывал «Решён».
    """
    resolved_domain()

    page = overview(at=NOW)

    assert page.open_items == ()
    assert page.awaiting_close == 1


def test_d39_the_unresolved_card_drops_to_zero_immediately() -> None:
    """Счётчик считает расхождения, а не записи в таблице инцидентов."""
    resolved_domain()

    value, note, tone = cards()['Не решены']

    assert (value, note, tone) == (0, 'расхождений нет', '')


def test_d39_a_real_discrepancy_still_shows() -> None:
    """Обратная сторона: пока адрес не разрешён, строка на месте."""
    with_discrepancy()

    page = overview(at=NOW)

    assert len(page.open_items) == 1
    assert page.open_items[0].addresses == ('185.44.9.201',)
    assert page.awaiting_close == 0


def test_d39_a_fixed_incident_does_not_claim_it_was_closed() -> None:
    """`last_closed_at` относится к закрытым записям, а эта ещё открыта."""
    resolved_domain()

    assert overview(at=NOW).last_closed_at is None
