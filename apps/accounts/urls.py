"""Маршруты входа — ТЗ 3."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.accounts.member_views import (
    MemberAction,
    MemberInvite,
    MemberList,
    MemberUpdate,
)
from apps.accounts.views import (
    AcceptInvitation,
    Login,
    Logout,
    PasswordChange,
    TotpSetup,
)

urlpatterns: list[URLPattern] = [
    path('login/', Login.as_view(), name='login'),
    path('logout/', Logout.as_view(), name='logout'),
    path('login/authenticator/', TotpSetup.as_view(), name='totp-setup'),
    path('login/password/', PasswordChange.as_view(), name='password-change'),
    # Адрес приглашения взят из прототипа.
    path('join/<str:token>/', AcceptInvitation.as_view(), name='join'),
    path('members/', MemberList.as_view(), name='member-list'),
    path('members/invite/', MemberInvite.as_view(), name='member-invite'),
    path('members/<int:member_id>/', MemberUpdate.as_view(), name='member-update'),
    path('members/<int:member_id>/<str:action>/', MemberAction.as_view(), name='member-action'),
]
