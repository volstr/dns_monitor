"""Корневые маршруты.

Пока здесь только проба работоспособности (11.13, Q-E4). Экраны продукта — списки доменов,
карточка, группы, журнал, участники — приходят на этапе 6.
"""

from django.urls import URLPattern, URLResolver, path

from apps.monitoring.views import healthcheck

urlpatterns: list[URLPattern | URLResolver] = [
    path("health/", healthcheck, name="healthcheck"),
]
