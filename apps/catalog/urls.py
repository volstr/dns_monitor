"""Маршруты каталога. Адреса — из прототипа и глоссария."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.catalog.views import (
    allowlist_check,
    domain_check_now,
    domain_detail,
    domain_list,
    domain_save,
)

urlpatterns: list[URLPattern] = [
    path("domains/", domain_list, name="domain-list"),
    # Адрес из 8.4: ссылка в уведомлении ведёт сюда же.
    path("domains/<int:domain_id>/", domain_detail, name="domain-detail"),
    path("domains/<int:domain_id>/save/", domain_save, name="domain-save"),
    path("domains/<int:domain_id>/check/", domain_check_now, name="domain-check"),
    # Построчная проверка списка через HTMX (10.2).
    path("domains/<int:domain_id>/allowlist/", allowlist_check, name="allowlist-check"),
]
