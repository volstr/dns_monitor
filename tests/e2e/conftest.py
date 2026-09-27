"""Проверки отрисованной страницы — этап 7.

Единственное место, где проверяется не содержимое ответа, а **то, что видно**. Нужда доказана
делом: невидимый каркас и меню без оформления прошли мимо полутысячи обычных тестов, потому что
`assert "Домены" in body` одинаково зелёный и когда страница отрисована, и когда она скрыта
правилом CSS.

База здесь транзакционная: живой сервер ходит в другом потоке и незакоммиченных данных не видит.
Из-за этого таблицы чистятся целиком, и строку общих настроек приходится восстанавливать — для
этого есть общая фикстура `global_settings`.
"""

from __future__ import annotations

import os

# Синхронный API Playwright крутит цикл событий в том же потоке, и Django считает обращение к
# базе из него небезопасным. Здесь это безопасно: к базе обращаются фикстуры и проверки, а не
# обработчики — те работают в потоке живого сервера. Разрешение выставляется до импорта Django.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts.models import Member, Role
from apps.catalog.models import AllowlistEntry, Domain
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    BucketSize,
)

#: Момент, от которого строятся данные браузерных тестов. **Привязан к настоящему времени, а не
#: к константе**, и это не небрежность.
#:
#: Браузерный тест идёт через обработчик, а тот берёт время у `SystemClock` — подменить его
#: нечем, порта в этом месте нет. Значит данные с фиксированной датой живут ровно до порога
#: актуальности: через сутки адрес становится «не актуален», статусы меняются, и тесты начинают
#: падать сами по себе, без единой правки в коде. Так и случилось 2026-09-27 в 14:38.
#:
#: В остальных тестах момент по-прежнему передаётся явно и фиксирован: там его принимает сам
#: селектор, и настоящие часы ни при чём.
MOMENT = timezone.now() - timedelta(minutes=5)
PASSWORD = "e2e-parol-dlya-vhoda"
EMAIL = "e2e@contoso.ru"

#: Сколько суток следа кладём. Больше тридцати — чтобы все три масштаба (7 дней, 30 дней, всё
#: наблюдение) отличались друг от друга: на коротком следе они совпали бы, и переключатель
#: проверялся бы вхолостую.
TRACE_DAYS = 40


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "locale": "ru-RU", "viewport": {"width": 1280, "height": 900}}


@pytest.fixture
def admin_member(transactional_db: None, global_settings: object) -> Member:
    return Member.objects.create_member(
        email=EMAIL, name="Анна Кирилова", created_at=MOMENT, role=Role.ADMIN, password=PASSWORD
    )


@pytest.fixture
def sample_domain(admin_member: Member) -> Domain:
    """Домен с записью списка, тремя адресами и следом за несколько суток.

    Два адреса соседние — на них проверяется склейка меток линейки (11.4), третий далеко.
    """
    domain = Domain.objects.create(
        name="pay-gw.contoso.ru", last_successful_check_at=MOMENT, next_check_at=MOMENT
    )
    AllowlistEntry.objects.create(domain=domain, entry="10.20.0.0/22", raw="10.20.0.0/22")

    for address in ("10.20.2.60", "10.20.2.61", "10.20.1.14"):
        AddressRegistryEntry.objects.create(
            domain=domain, address=address, first_seen_at=MOMENT, last_seen_at=MOMENT
        )
        for day in range(TRACE_DAYS):
            AddressObservationBucket.objects.create(
                domain=domain,
                address=address,
                bucket_size=BucketSize.DAY.value,
                bucket_start=MOMENT - timedelta(days=TRACE_DAYS - day),
            )
    return domain


@pytest.fixture
def signed_in(page: Any, live_server: Any, admin_member: Member) -> Any:
    """Вход через настоящую форму — так же, как это делает человек."""
    page.goto(f"{live_server.url}/login/")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server.url}/")
    return page
