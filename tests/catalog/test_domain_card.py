"""Карточка домена — ТЗ 9.1, 6.3, 6.4, 11.8; решения Q-C13, Q-13.5, Q-C11, критерий 46."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import (
    AllowlistEntry,
    Domain,
    GlobalSettings,
    Group,
    NotificationMode,
)
from apps.catalog.selectors.domain_card import STALE_ENTRY_AFTER, domain_card
from apps.monitoring.models import AddressRegistryEntry, Event, EventType

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
NAME = "pay-gw.contoso.ru"


@pytest.fixture
def domain() -> Domain:
    return Domain.objects.create(name=NAME, last_successful_check_at=MOMENT)


def allow(domain: Domain, raw: str) -> AllowlistEntry:
    return AllowlistEntry.objects.create(domain=domain, entry=raw, raw=raw)


def seen(
    domain: Domain,
    address: str,
    *,
    first: timedelta = timedelta(days=1),
    last: timedelta = timedelta(minutes=1),
) -> None:
    AddressRegistryEntry.objects.create(
        domain=domain,
        address=address,
        first_seen_at=MOMENT - first,
        last_seen_at=MOMENT - last,
    )


def card(domain: Domain) -> object:
    result = domain_card(domain_id=domain.pk, at=MOMENT)
    assert result is not None
    return result


@pytest.fixture(autouse=True)
def _threshold_of_one_day() -> None:
    """
    Порог актуальности прибит к суткам, а не взят из умолчания.

    Умолчание — 14 суток (Q-D47). Тест, который строит неактуальный адрес или показывает
    порог на экране, обязан называть своё значение сам: иначе он молча меняет смысл вместе с
    настройкой продукта, а падает совсем в другом месте.
    """
    GlobalSettings.objects.filter(pk=1).update(freshness_threshold_seconds=24 * 60 * 60)


def test_missing_domain_is_not_an_error(domain: Domain) -> None:
    """Отсутствие домена — это 404 у обработчика, а не исключение в селекторе."""
    assert domain_card(domain_id=domain.pk + 1000, at=MOMENT) is None


def test_46_exact_entry_never_says_it_contains(domain: Domain) -> None:
    """Критерий 46: у записи без маски «точное совпадение», «входит в» запрещено."""
    allow(domain, "10.20.3.7")
    seen(domain, "10.20.3.7")

    line = card(domain).registry[0]

    assert line.match_text == "точное совпадение с 10.20.3.7"
    assert "входит" not in line.match_text


def test_6_3_subnet_entry_says_it_contains(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14")

    assert card(domain).registry[0].match_text == "входит в 10.20.0.0/22"


def test_6_3_address_without_a_match_says_so(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "185.44.9.201")

    line = card(domain).registry[0]

    assert line.match_text == "нет совпадений"
    assert (line.status_label, line.chip_css) == ("Не решён", "out")


def test_d25_empty_allowlist_is_not_a_breach(domain: Domain) -> None:
    seen(domain, "185.44.9.201")

    result = card(domain)

    assert result.registry[0].status_label == "Не проверяется"
    assert result.registry[0].match_text == "список не задан"
    # Не «сломано», но и не «в порядке»: домен под наблюдением, а сравнивать не с чем
    # (уточнение Q-D15). Красный здесь был бы враньём, зелёный — тоже.
    assert result.summary_tone == "change"


def test_9_1_registry_shows_first_and_last_time(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14", first=timedelta(days=30), last=timedelta(minutes=1))

    line = card(domain).registry[0]

    assert line.first_seen_at == MOMENT - timedelta(days=30)
    assert line.last_seen_at == MOMENT - timedelta(minutes=1)
    assert line.age_text == "отдавался 1 мин назад"


def test_9_1_address_seen_once_is_marked_as_first_time(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "185.44.9.201", first=timedelta(minutes=1), last=timedelta(minutes=1))

    assert card(domain).registry[0].age_text == "появился впервые за всё время"


def test_6_4_stale_address_reports_how_long_it_has_been_missing(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14", first=timedelta(days=40), last=timedelta(days=10))

    line = card(domain).registry[0]

    assert (line.status_label, line.chip_css) == ("Не актуален", "gone")
    assert line.age_text == "не отдаётся 10 дней"


def test_13_5_entry_nobody_answers_with_is_highlighted(domain: Domain) -> None:
    """Q-13.5: запись, под которую давно ничего не приходит, подсвечивается."""
    stale = allow(domain, "10.20.3.7")
    seen(domain, "10.20.3.7", first=timedelta(days=200), last=STALE_ENTRY_AFTER + timedelta(days=5))
    fresh = allow(domain, "10.30.0.0/16")
    seen(domain, "10.30.1.1")

    entries = {line.raw: line for line in card(domain).allowlist}

    assert entries[stale.raw].stale_days == 35
    assert entries[fresh.raw].stale_days is None


def test_13_5_entry_that_never_matched_anything_is_not_called_stale(domain: Domain) -> None:
    """«Не отдаётся 30 дней» про запись, под которую ничего не приходило, было бы неправдой."""
    allow(domain, "10.30.4.0/24")

    line = card(domain).allowlist[0]

    assert line.stale_days is None
    assert line.inside == ()


def test_9_1_subnet_line_carries_its_range_and_size(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14")

    line = card(domain).allowlist[0]

    assert line.is_network is True
    assert line.range_text == "10.20.0.0 — 10.20.3.255 · 1024 адреса"
    assert line.inside == ("10.20.1.14",)


def test_9_1_exact_entry_has_no_range(domain: Domain) -> None:
    allow(domain, "10.20.3.7")

    line = card(domain).allowlist[0]

    assert (line.is_network, line.range_text) == (False, "")


def test_c13_every_inherited_value_shows_where_it_came_from(domain: Domain) -> None:
    """ТЗ 5 и 11.8: происхождение видно у каждой строки, а не у карточки целиком."""
    group = Group.objects.create(name="Платёжный контур", check_period_seconds=5 * 60)
    Domain.objects.filter(pk=domain.pk).update(group=group)

    lines = {line.label: line for line in card(domain).settings}

    assert (lines["Проверка"].value, lines["Проверка"].origin) == ("раз в 5 мин", "group")
    assert lines["Считать пропавшим"].origin == "global"
    assert lines["Считать пропавшим"].value == "через 24 ч 0 мин"


def test_8_5_reminder_interval_of_zero_reads_as_do_not_remind(domain: Domain) -> None:
    GlobalSettings.objects.filter(pk=1).update(reminder_interval_seconds=0)

    lines = {line.label: line for line in card(domain).settings}

    assert lines["Напоминать"].value == "не напоминать"


def test_summary_counts_unmatched_out_of_fresh(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14")
    seen(domain, "10.20.2.61")
    seen(domain, "185.44.9.201")

    result = card(domain)

    assert result.summary_label == "1 из 3 адресов не решён"
    assert result.summary_tone == "breach"
    assert [line.address for line in result.unmatched] == ["185.44.9.201"]


def test_summary_ignores_stale_addresses(domain: Domain) -> None:
    allow(domain, "10.20.0.0/22")
    seen(domain, "10.20.1.14")
    seen(domain, "10.20.9.9", first=timedelta(days=40), last=timedelta(days=10))

    assert card(domain).summary_label == "Адрес решён"


def test_c11_history_uses_the_approved_event_names(domain: Domain) -> None:
    """Q-C11: «Адрес вне разрешённых», а не «Адрес не решён» — «решён» это статус, а не событие."""
    Event.objects.create(
        domain=domain,
        event_type=EventType.NOT_ALLOWED,
        occurred_at=MOMENT,
        payload={"addresses": ["185.44.9.201"]},
    )
    Event.objects.create(
        domain=domain,
        event_type=EventType.ADDRESS_APPEARED,
        occurred_at=MOMENT - timedelta(hours=1),
    )

    events = card(domain).events

    assert [event.label for event in events] == ["Адрес вне разрешённых", "Появился адрес"]
    assert events[0].tone == "breach"
    assert events[0].addresses == ("185.44.9.201",)


def test_headline_says_when_there_were_no_answers_yet() -> None:
    fresh = Domain.objects.create(name="new.contoso.ru")

    assert "ответов ещё не было" in card(fresh).headline


def test_headline_names_group_and_period(domain: Domain) -> None:
    group = Group.objects.create(name="Платёжный контур", check_period_seconds=5 * 60)
    Domain.objects.filter(pk=domain.pk).update(group=group)
    GlobalSettings.objects.filter(pk=1).update(notification_mode=NotificationMode.NOT_ALLOWED)

    # 14:38 UTC — это 17:38 в поясе установки. ТЗ 4.0: хранение в UTC, показ в местном поясе,
    # и подпись карточки — как раз показ.
    expected = "Группа «Платёжный контур» · проверка раз в 5 мин · последний ответ в 17:38"
    assert card(domain).headline == expected
