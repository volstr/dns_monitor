"""Маршруты каталога. Адреса — из прототипа и глоссария."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.catalog.views import domain_detail, domain_list

urlpatterns: list[URLPattern] = [
    path("domains/", domain_list, name="domain-list"),
    # Адрес из 8.4: ссылка в уведомлении ведёт сюда же.
    path("domains/<int:domain_id>/", domain_detail, name="domain-detail"),
]
