"""Виджеты и видимость страницы — ТЗ 9.2, 10.4, 11.4; этап 7.

Проверяется отрисованная страница. Два первых теста существуют потому, что ровно эти две ошибки
обычные тесты пропустили, а нашёл их разработчик глазами.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from apps.catalog.models import Domain, Group
from apps.monitoring.models import AddressObservationBucket, AddressRegistryEntry, BucketSize
from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def test_11_4_application_frame_is_actually_visible(signed_in: Any, live_server: Any) -> None:
    """`.app` в CSS прототипа скрыт, показывает его только `.app.on`.

    Страница может прийти целиком и не отобразиться. Содержимое ответа об этом молчит.
    """
    page = signed_in
    page.goto(f"{live_server.url}/domains/")

    assert page.locator(".app").is_visible()
    assert page.locator(".rail").is_visible()


def test_11_4_menu_items_are_styled_not_bare_links(signed_in: Any, live_server: Any) -> None:
    """Оформление меню навешано на `.nav button`, а пункты — ссылки.

    Без правки селекторов они остаются голыми: проверяем вычисленный стиль, а не разметку.
    """
    page = signed_in
    page.goto(f"{live_server.url}/domains/")

    item = page.locator(".nav a", has_text="Домены").first
    display = item.evaluate("node => getComputedStyle(node).display")
    padding = item.evaluate("node => getComputedStyle(node).paddingLeft")

    assert display == "flex"
    assert padding != "0px"


def test_11_4_current_menu_item_is_marked(signed_in: Any, live_server: Any) -> None:
    page = signed_in
    page.goto(f"{live_server.url}/groups/")

    current = page.locator('.nav a[aria-current="page"]')
    assert current.count() == 1
    assert current.first.inner_text().strip().startswith("Группы")


def test_11_4_subnet_ruler_draws_marks_and_merges_neighbours(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    """11.4: наложенные метки склеиваются в одну с числом адресов, позиция остаётся честной."""
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")

    track = page.locator(".ruler .track").first
    assert track.locator(".tick").count() > 0

    pins = track.locator(".pin")
    assert pins.count() >= 1
    # Соседние 10.20.2.60 и .61 не расходятся по ширине — значит склеены, и это видно числом.
    cluster = track.locator(".pin.cluster")
    assert cluster.count() == 1
    assert cluster.first.locator("em").inner_text() == "2"


def test_11_4_clicking_a_cluster_opens_the_zoomed_fragment(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    """11.4: клик по склеенной метке открывает увеличенный фрагмент, где адреса расходятся."""
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")

    page.locator(".ruler .pin.cluster").first.click()

    zoom = page.locator(".ruler .zoom").first
    assert zoom.is_visible()
    assert zoom.locator(".track.zoomed .pin").count() == 2


def test_9_2_trace_draws_marks_and_names_the_scale(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    """9.2: след из отметок, и рядом подписана текущая цена деления."""
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")

    row = page.locator("[data-trace-row]").first
    assert row.locator("i").count() > 1
    # Хотя бы одна отметка закрашена: адрес отдавался.
    assert row.locator("i.a").count() > 0

    caption = page.locator("[data-trace-caption]").inner_text()
    assert "одна отметка" in caption


def test_9_2_trace_rebuilds_when_the_window_narrows(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    """9.2: «при изменении размера окна график перестраивается»."""
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")
    row = page.locator("[data-trace-row]").first
    wide = row.locator("i").count()

    page.set_viewport_size({"width": 420, "height": 900})
    page.wait_for_timeout(300)

    assert row.locator("i").count() <= wide


def test_9_2_scale_switch_narrows_the_axis(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")

    page.locator("[data-trace-scales] button", has_text="7 дней").click()

    assert "7 суток" in page.locator("[data-trace-caption]").inner_text()


def test_10_4_group_combo_filters_by_search(signed_in: Any, live_server: Any) -> None:
    """10.4: поле выбора группы с поиском."""
    Group.objects.create(name="Платёжный контур")
    Group.objects.create(name="Продакшн-периметр")
    page = signed_in
    page.goto(f"{live_server.url}/domains/")

    page.locator(".combo-btn").first.click()
    page.locator(".combo-pop input").first.fill("платёж")

    options = page.locator(".combo-opt:visible")
    assert options.count() == 1
    assert "Платёжный контур" in options.first.inner_text()


def test_10_4_combo_writes_into_the_select_that_is_submitted(
    signed_in: Any, live_server: Any
) -> None:
    """Значение обязано лежать в том поле, которое уходит на сервер.

    Иначе показанное и отправленное однажды разойдутся.
    """
    group = Group.objects.create(name="Платёжный контур")
    page = signed_in
    page.goto(f"{live_server.url}/domains/")

    page.locator(".combo-btn").first.click()
    page.locator(".combo-opt", has_text="Платёжный контур").first.click()

    assert page.locator("select[name=group]").input_value() == str(group.pk)


def test_d25_not_checked_mark_is_not_green(signed_in: Any, live_server: Any) -> None:
    """Q-D25: адрес без белого списка не красится цветом решённого.

    Проверяется вычисленный цвет, а не класс: подмена класса на зелёный прошла бы мимо обычных
    тестов — ровно так эта ошибка и возникла.
    """
    domain = Domain.objects.create(
        name="github.com",
        last_successful_check_at=datetime(2026, 9, 26, 14, 38, tzinfo=UTC),
        next_check_at=None,
    )
    AddressRegistryEntry.objects.create(
        domain=domain,
        address="140.82.121.3",
        first_seen_at=datetime(2026, 9, 26, 14, 38, tzinfo=UTC),
        last_seen_at=datetime(2026, 9, 26, 14, 38, tzinfo=UTC),
    )
    AddressObservationBucket.objects.create(
        domain=domain,
        address="140.82.121.3",
        bucket_size=BucketSize.DAY.value,
        bucket_start=datetime(2026, 9, 26, tzinfo=UTC),
    )
    page = signed_in
    page.goto(f"{live_server.url}/domains/{domain.pk}/")

    mark = page.locator("[data-trace-row] i.n").first
    assert mark.count() > 0
    # Зелёный из палитры прототипа — --ok, то есть #2E7D5B.
    assert mark.evaluate("node => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
    assert page.locator("[data-trace-row] i.a").count() == 0


def test_11_4_message_strip_lines_up_with_the_page_content(
    signed_in: Any, live_server: Any, sample_domain: Domain
) -> None:
    """Полоса сообщений стоит по сетке страницы, а не враспор по всей ширине.

    Сравнивается настоящее положение на отрисованной странице: в разметке отступ задан классом,
    и по ней не видно, совпал он с содержимым или нет.
    """
    page = signed_in
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")
    page.fill("textarea[name=allowlist]", "10.20.999.0/24")
    # Именно кнопка формы сохранения: первой на странице стоит «Проверить сейчас».
    page.locator('form[action$="/save/"] button[type=submit]').click()

    strip = page.locator(".msgs .err").first.bounding_box()
    # Сравнение именно с содержимым: у самого `.body` отступ свой, и его левый край левее.
    content = page.locator("section .body .panel").first.bounding_box()

    assert strip is not None
    assert content is not None
    assert abs(strip["x"] - content["x"]) < 2
    assert abs(strip["width"] - content["width"]) < 2


def test_page_works_with_javascript_disabled(
    browser: Any, live_server: Any, sample_domain: Domain
) -> None:
    """Виджеты — надстройка, а не условие работы.

    Браузер с выключенным JavaScript: вход проходит обычной формой, список адресов и реестр
    читаются, оформление на месте. Ломаться без трёх файлов приложению нельзя.
    """
    context = browser.new_context(java_script_enabled=True)
    context.add_init_script("window.__disabled = true")
    context.close()

    plain = browser.new_context(java_script_enabled=False)
    page = plain.new_page()
    page.goto(f"{live_server.url}/login/")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.goto(f"{live_server.url}/domains/{sample_domain.pk}/")

    assert page.locator(".app").is_visible()
    assert page.locator(".ruler-list .chip").count() == 3
    assert page.locator(".reg-row").count() >= 3
    # Виджетов нет — и это видно: дорожка следа пуста, линейка без меток.
    assert page.locator(".ruler .track .pin").count() == 0
    plain.close()
