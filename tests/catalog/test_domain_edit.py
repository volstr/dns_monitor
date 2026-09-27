"""Правка карточки домена — ТЗ 10.2, 11.6, 3; критерии 44, 45, 47, 48, 59."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain
from apps.catalog.services.domains import DomainNotSaved, DomainSaved, save_domain
from apps.monitoring.models import AddressRegistryEntry
from apps.monitoring.selectors.addresses import address_statuses
from core.types.checking import AddressStatus
from core.types.dns import RecordType

pytestmark = pytest.mark.django_db

MOMENT = datetime(2026, 9, 24, 14, 38, tzinfo=UTC)
THRESHOLD = datetime(2026, 9, 24, 14, 38, tzinfo=UTC) - datetime(2026, 9, 23, 14, 38, tzinfo=UTC)


@pytest.fixture
def domain() -> Domain:
    domain = Domain.objects.create(name="pay-gw.contoso.ru")
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")
    return domain


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


def save(domain: Domain, allowlist: str, **extra: object) -> object:
    return save_domain(
        domain_id=domain.pk,
        comment=str(extra.get("comment", "")),
        record_types=tuple(extra.get("record_types", (RecordType.A.value,))),  # type: ignore[arg-type]
        allowlist_text=allowlist,
        watch_enabled=bool(extra.get("watch_enabled", True)),
    )


def test_10_2_saving_replaces_the_whole_list(domain: Domain) -> None:
    result = save(domain, "10.30.4.0/24\n10.20.3.7")

    assert isinstance(result, DomainSaved)
    assert set(AllowlistEntry.objects.filter(domain=domain).values_list("raw", flat=True)) == {
        "10.30.4.0/24",
        "10.20.3.7",
    }


def test_10_2_a_broken_line_writes_nothing(domain: Domain) -> None:
    """Сохранение блокируется целиком: половина списка хуже, чем прежний список."""
    result = save(domain, "10.30.4.0/24\n10.20.999.0/24")

    assert isinstance(result, DomainNotSaved)
    assert list(AllowlistEntry.objects.filter(domain=domain).values_list("raw", flat=True)) == [
        "10.20.0.0/22"
    ]


def test_13_3_normalisation_is_reported_back(domain: Domain) -> None:
    result = save(domain, "10.20.1.5/24")

    assert isinstance(result, DomainSaved)
    assert result.normalized == (("10.20.1.5/24", "10.20.1.0/24"),)
    assert AllowlistEntry.objects.get(domain=domain).raw == "10.20.1.0/24"


def test_saving_updates_comment_record_types_and_watch(domain: Domain) -> None:
    save(
        domain,
        "10.20.0.0/22",
        comment="Платёжный шлюз",
        record_types=(RecordType.A.value, RecordType.AAAA.value),
        watch_enabled=False,
    )

    domain.refresh_from_db()
    assert domain.comment == "Платёжный шлюз"
    assert domain.record_types == ["a", "aaaa"]
    assert domain.watch_enabled is False


def test_59_the_new_list_is_used_right_away(domain: Domain) -> None:
    """Критерий 59: изменённый список действует со следующей проверки, без ожидания кэша."""
    AddressRegistryEntry.objects.create(
        domain=domain, address="185.44.9.201", first_seen_at=MOMENT, last_seen_at=MOMENT
    )
    before = address_statuses(domain.pk, MOMENT, THRESHOLD)
    assert before[0].status == AddressStatus.UNMATCHED

    save(domain, "185.44.9.0/24")

    after = address_statuses(domain.pk, MOMENT, THRESHOLD)
    assert after[0].status == AddressStatus.MATCHED


def test_10_2_empty_list_is_a_legitimate_save(domain: Domain) -> None:
    result = save(domain, "")

    assert isinstance(result, DomainSaved)
    assert AllowlistEntry.objects.filter(domain=domain).count() == 0


def test_4_3_domain_without_a_record_type_is_refused_not_crashed(domain: Domain) -> None:
    """
    Снятые галочки — ошибка ввода, а не пятисотая.

    Инвариант держит ограничение `domain_has_record_type` в базе, но до него дело доходить не
    должно: отказ обязан быть штатным исходом с причиной (`CLAUDE.md`).
    """
    result = save(domain, "10.20.0.0/22", record_types=())

    assert isinstance(result, DomainNotSaved)
    assert result.reasons == ("Нужен хотя бы один тип записи: иначе домен не о чем спрашивать.",)
    domain.refresh_from_db()
    assert domain.record_types == ["a", "aaaa"]


def test_4_3_saving_without_record_types_shows_the_reason(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("domain-save", args=[domain.pk]), {"allowlist": "10.20.0.0/22"}
    )

    assert response.status_code == 200
    assert "Нужен хотя бы один тип записи" in response.content.decode()


def test_3_observer_cannot_save(client: Client, observer: Member, domain: Domain) -> None:
    """ТЗ 3: наблюдатель не меняет ничего. Проверка в обработчике, а не в спрятанной форме."""
    client.force_login(observer)

    response = client.post(
        reverse("domain-save", args=[domain.pk]), {"allowlist": "10.30.4.0/24"}
    )

    assert response.status_code == 403
    assert AllowlistEntry.objects.get(domain=domain).raw == "10.20.0.0/22"


def test_10_2_admin_saves_and_is_redirected(client: Client, admin: Member, domain: Domain) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("domain-save", args=[domain.pk]),
        {
            "allowlist": "10.30.4.0/24",
            "comment": "шлюз",
            "watch_enabled": "on",
            "record_types": ["a"],
        },
    )

    assert response.status_code == 302
    assert AllowlistEntry.objects.get(domain=domain).raw == "10.30.4.0/24"


def test_44_broken_line_returns_the_card_with_the_reason(
    client: Client, admin: Member, domain: Domain
) -> None:
    client.force_login(admin)

    response = client.post(
        reverse("domain-save", args=[domain.pk]), {"allowlist": "10.20.999.0/24"}
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "999 — недопустимый октет" in body
    assert "Пока есть ошибочная строка, сохранить нельзя." in body
    # Введённое не потеряно: человек правит свой текст, а не набирает заново.
    assert "10.20.999.0/24" in body


def test_10_2_line_check_returns_only_the_issues(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Построчная проверка через HTMX отдаёт фрагмент, а не страницу."""
    client.force_login(admin)

    response = client.post(
        reverse("allowlist-check", args=[domain.pk]), {"allowlist": "10.20.0.0/22\n10.20.3.7"}
    )

    body = response.content.decode()
    assert "уже входит в 10.20.0.0/22 из строки 1" in body
    assert "<html" not in body


def test_47_second_manual_check_hits_the_cooldown(
    client: Client, admin: Member, domain: Domain
) -> None:
    """Критерий 47 на карточке: повторная проверка отклоняется кулдауном (11.6)."""
    client.force_login(admin)
    url = reverse("domain-check", args=[domain.pk])

    client.post(url)
    domain.refresh_from_db()
    assert domain.next_check_at is not None

    response = client.post(url, follow=True)

    assert "проверяли только что" in response.content.decode()


def test_48_manual_check_queues_the_domain(client: Client, admin: Member) -> None:
    """Критерий 48: ручная проверка сдвигает расписание — домен идёт в ближайший тик."""
    domain = Domain.objects.create(name="queued.contoso.ru", next_check_at=None)
    client.force_login(admin)

    client.post(reverse("domain-check", args=[domain.pk]))

    domain.refresh_from_db()
    assert domain.next_check_at is not None


def test_3_observer_cannot_start_a_check(client: Client, observer: Member, domain: Domain) -> None:
    client.force_login(observer)

    assert client.post(reverse("domain-check", args=[domain.pk])).status_code == 403
