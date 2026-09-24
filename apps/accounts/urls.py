"""Маршруты входа — ТЗ 3."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.accounts.member_views import (
    member_action,
    member_invite,
    member_list,
    member_update,
)
from apps.accounts.views import (
    accept_invitation_view,
    login_view,
    logout_view,
    password_change_view,
    totp_setup_view,
)

urlpatterns: list[URLPattern] = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("login/authenticator/", totp_setup_view, name="totp-setup"),
    path("login/password/", password_change_view, name="password-change"),
    # Адрес приглашения взят из прототипа.
    path("join/<str:token>/", accept_invitation_view, name="join"),
    path("members/", member_list, name="member-list"),
    path("members/invite/", member_invite, name="member-invite"),
    path("members/<int:member_id>/", member_update, name="member-update"),
    path("members/<int:member_id>/<str:action>/", member_action, name="member-action"),
]
