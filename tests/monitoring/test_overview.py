"""
Верхняя сводка обзора — разметка прототипа (строки 738–743), ТЗ 11.9.

Четыре карточки про **состояние**, а не про опись установки. Проверяется и обратное: при
спокойном состоянии карточки не должны краснеть, иначе цвет перестаёт что-либо значить.
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.catalog.models import Domain, GlobalSettings, Group
from apps.monitoring.models import Event, EventType, Incident
from apps.monitoring.selectors.overview import overview

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 27, 14, 38, tzinfo=UTC)


def cards(at: datetime = NOW) -> dict[str, tuple[int, str, str]]:
    """Карточки по подписи: значение, пояснение, тон."""
    return {stat.label: (stat.value, stat.note, stat.tone) for stat in overview(at=at).stats}


def test_11_9_four_cards_in_the_order_of_the_prototype() -> None:
    labels = [stat.label for stat in overview(at=NOW).stats]

    assert labels == ["Под наблюдением", "Не решены", "Адреса изменились", "Не резолвится"]


def test_watched_counts_only_domains_under_watch() -> None:
    """Приостановленный домен не наблюдается — значит и в счётчике ему не место (11.1)."""
    Group.objects.create(name="Платёжный контур")
    Domain.objects.create(name="pay-gw.contoso.ru")
    Domain.objects.create(name="old.contoso.ru", watch_enabled=False)

    value, note, _ = cards()["Под наблюдением"]

    assert value == 1
    assert note == "в 1 группе"


def test_11_9_the_unresolved_card_names_the_domain() -> None:
    """
    11.9: «счётчик не говорит ни какой домен, ни как давно».

    Число само по себе — ровно то, что раздел называет недостаточным, поэтому под ним имя.
    """
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    Incident.objects.create(domain=domain, opened_at=NOW - timedelta(hours=3))

    value, note, tone = cards()["Не решены"]

    assert (value, note, tone) == (1, "pay-gw.contoso.ru", "s-breach")


def test_the_unresolved_card_names_the_oldest_and_counts_the_rest() -> None:
    """Разбирать начинают со старшего, поэтому назван он, а не случайный."""
    old = Domain.objects.create(name="pay-gw.contoso.ru")
    fresh = Domain.objects.create(name="api.contoso.ru")
    Incident.objects.create(domain=old, opened_at=NOW - timedelta(hours=5))
    Incident.objects.create(domain=fresh, opened_at=NOW - timedelta(minutes=5))

    value, note, _ = cards()["Не решены"]

    assert (value, note) == (2, "pay-gw.contoso.ru и ещё 1")


def test_changed_card_counts_domains_not_events() -> None:
    """Домен, у которого за сутки сменилось пять адресов, — это один изменившийся домен."""
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    for _ in range(5):
        Event.objects.create(
            domain=domain, event_type=EventType.ADDRESS_APPEARED, occurred_at=NOW
        )

    value, note, tone = cards()["Адреса изменились"]

    assert (value, note, tone) == (1, "за последние сутки", "s-change")


def test_changed_card_ignores_what_happened_earlier_than_a_day_ago() -> None:
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    Event.objects.create(
        domain=domain,
        event_type=EventType.ADDRESS_APPEARED,
        occurred_at=NOW - timedelta(days=2),
    )

    assert cards()["Адреса изменились"][0] == 0


def test_failing_card_uses_the_threshold_from_settings() -> None:
    """Домен считается нерезолвящимся по тому же порогу, что и событие `RESOLVE_FAILED` (7.1)."""
    GlobalSettings.objects.filter(pk=1).update(resolve_failure_threshold=3)
    Domain.objects.create(name="gone.contoso.ru", consecutive_resolve_failures=4)
    Domain.objects.create(name="ok.contoso.ru", consecutive_resolve_failures=1)

    value, note, tone = cards()["Не резолвится"]

    assert (value, note, tone) == (1, "4 попытки подряд", "s-fail")


def test_a_calm_installation_has_no_alarming_colours() -> None:
    """Ноль не должен кричать: иначе цвет перестаёт отличать беду от покоя."""
    Domain.objects.create(name="pay-gw.contoso.ru")

    tones = {stat.label: stat.tone for stat in overview(at=NOW).stats}

    assert set(tones.values()) == {""}
    assert cards()["Не решены"][1] == "расхождений нет"
    assert cards()["Не резолвится"][1] == "все домены отвечают"


def test_an_empty_installation_does_not_break_the_summary() -> None:
    """Свежая установка: ни доменов, ни групп, ни событий."""
    stats = overview(at=NOW).stats

    assert [stat.value for stat in stats] == [0, 0, 0, 0]
