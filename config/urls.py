"""Корневые маршруты.

TODO(ТЗ 11.13): проба работоспособности ``/health/`` и страница 503 при недоступном Redis — этап 4,
см. Q-E4.
"""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
