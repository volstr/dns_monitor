"""Маршруты каталога. Адреса — из прототипа и глоссария."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.catalog.views import domain_list

urlpatterns: list[URLPattern] = [
    path("domains/", domain_list, name="domain-list"),
]
