"""Маршруты входа — ТЗ 3."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.accounts.views import login_view, logout_view, password_change_view, totp_setup_view

urlpatterns: list[URLPattern] = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("login/authenticator/", totp_setup_view, name="totp-setup"),
    path("login/password/", password_change_view, name="password-change"),
]
