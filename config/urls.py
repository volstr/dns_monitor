"""Корневые маршруты.

Экраны появляются по мере готовности: карточка домена, группы, журнал, настройки и участники
приходят следующими частями этапа 6, в порядке реализации из ТЗ.
"""

from django.urls import URLPattern, URLResolver, include, path

from apps.monitoring.views import healthcheck

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/", healthcheck, name="healthcheck"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
]
