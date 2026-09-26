"""Маршруты каталога. Адреса — из прототипа и глоссария."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.catalog.views import (
    allowlist_check,
    domain_check_now,
    domain_create,
    domain_delete,
    domain_detail,
    domain_list,
    domain_save,
    domain_watch,
    global_settings,
    global_settings_save,
    group_check_now,
    group_domains,
    group_edit,
    group_list,
    group_save,
    journal,
    journal_csv,
    overview_screen,
)

urlpatterns: list[URLPattern] = [
    path("", overview_screen, name="overview"),
    path("domains/", domain_list, name="domain-list"),
    path("domains/new/", domain_create, name="domain-new"),
    # Адрес из 8.4: ссылка в уведомлении ведёт сюда же.
    path("domains/<int:domain_id>/", domain_detail, name="domain-detail"),
    path("domains/<int:domain_id>/save/", domain_save, name="domain-save"),
    path("domains/<int:domain_id>/watch/", domain_watch, name="domain-watch"),
    path("domains/<int:domain_id>/delete/", domain_delete, name="domain-delete"),
    path("domains/<int:domain_id>/check/", domain_check_now, name="domain-check"),
    # Построчная проверка списка через HTMX (10.2).
    path("domains/<int:domain_id>/allowlist/", allowlist_check, name="allowlist-check"),
    path("groups/", group_list, name="group-list"),
    path("groups/new/", group_edit, name="group-new"),
    path("groups/new/save/", group_save, name="group-create"),
    path("groups/<int:group_id>/", group_edit, name="group-edit"),
    path("groups/<int:group_id>/save/", group_save, name="group-save"),
    path("groups/<int:group_id>/domains/", group_domains, name="group-domains"),
    path("groups/<int:group_id>/check/", group_check_now, name="group-check"),
    path("events/", journal, name="event-list"),
    path("events/export.csv", journal_csv, name="event-export"),
    path("settings/", global_settings, name="settings"),
    path("settings/save/", global_settings_save, name="settings-save"),
]
