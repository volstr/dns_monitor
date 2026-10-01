"""
Экраны входа — ТЗ 3; критерии 32, 33, 50, 51, 52, 56.

Обработчику разрешены разбор формы, один вызов сервиса, выбор шаблона и редирект (`CLAUDE.md`).
Решение о том, пускать ли, принимает `authenticate`, а не эти классы.

Все они наследуют `PublicView`: это единственные экраны, доступные до входа, и отдельное имя
базового класса говорит, что проверку доступа здесь не забыли, а её тут не должно быть.

Промежуточные шаги — привязка аутентификатора и обязательная смена пароля — помнят участника
в сессии. Это не вход: до завершения шага доступа к остальным экранам нет, чего и требуют
критерии 32 и 51.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as start_session
from django.contrib.auth import logout as end_session
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.accounts.access import PublicView
from apps.accounts.forms import LoginForm, PasswordChangeForm, TotpBindingForm
from apps.accounts.models import Member
from apps.accounts.services.authentication import (
    Authenticated,
    LoginResult,
    PasswordChangeRequired,
    Rejected,
    TotpSetupRequired,
    authenticate,
    confirm_totp_binding,
    new_totp_secret,
    provisioning_uri,
)
from apps.accounts.services.members import Refused, accept_invitation, set_own_password
from core.adapters.redis_state import RedisStateStore
from core.adapters.system_clock import SystemClock

#: Ключи промежуточных шагов входа. Полноценной сессией участника они не являются.
PENDING_MEMBER = "pending_member_id"
PENDING_BINDING = "pending_totp_binding"

LOCKED_TEXT = "Слишком много неудачных попыток. Вход заблокирован на 15 минут."


def _store() -> RedisStateStore:
    return RedisStateStore.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)


#: Заголовок, в котором обратный прокси передаёт адрес клиента. Доверять ему можно **только**
#: потому, что `deploy/nginx.conf` перезаписывает его на каждом запросе, а порта наружу у `web`
#: нет: присланное клиентом значение до приложения не доходит. Открыть порт `web` в бою значит
#: сделать заголовок подделываемым — вместе с `X-Forwarded-Proto` из `config/settings/prod.py`.
FORWARDED_FOR_HEADER = "HTTP_X_REAL_IP"


def _client_ip(request: HttpRequest) -> str:
    """
    Счёт неудач идёт по паре «логин + IP» (11.11).

    `REMOTE_ADDR` за обратным прокси — адрес **прокси**, один на всех. Пара вырождается в
    «логин», и счётчик начинает работать не так, как задумано: любой желающий блокирует чужую
    учётную запись, перебирая её пароль откуда угодно, а владелец не входит и со своего адреса.
    Проверено на стенде: в ключе стоял адрес контейнера nginx.

    Без прокси — в режиме разработчика — заголовка нет, и берётся `REMOTE_ADDR`, как раньше.
    """
    return (
        request.META.get(FORWARDED_FOR_HEADER)
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    )


class Login(PublicView):
    """Вход. При выключенном втором факторе поля кода в разметке нет вовсе (критерий 50)."""

    def get(self, request: HttpRequest) -> HttpResponse:
        two_factor = settings.ENV.auth_2fa_enabled
        return render(
            request,
            "accounts/login.html",
            {"form": LoginForm(two_factor_enabled=two_factor), "two_factor": two_factor},
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        two_factor = settings.ENV.auth_2fa_enabled
        form = LoginForm(request.POST, two_factor_enabled=two_factor)
        if not form.is_valid():
            return render(
                request,
                "accounts/login.html",
                {"form": form, "two_factor": two_factor, "error": _form_error(form, two_factor)},
            )

        result = authenticate(
            email=form.login_email(),
            password=form.login_password(),
            code=form.totp_code(),
            ip=_client_ip(request),
            at=SystemClock().now(),
            state_store=_store(),
            two_factor_enabled=two_factor,
        )
        return _after_authenticate(request, result, two_factor)


def _form_error(form: LoginForm, two_factor: bool) -> str:
    """
    Причина отказа формы называется прямо.

    Пустой код при включённом втором факторе — не «проверьте почту и пароль»: человек вводит
    верные почту и пароль, получает этот текст и ищет ошибку не там. Про существование учётной
    записи здесь по-прежнему ничего не сообщается (Q-C19) — речь только о заполнении полей.
    """
    if two_factor and "code" in form.errors:
        return (
            "Нужен код из приложения-аутентификатора: второй фактор включён в окружении "
            "переменной AUTH_2FA_ENABLED."
        )
    return "Проверьте почту и пароль."


def _after_authenticate(
    request: HttpRequest, result: LoginResult, two_factor: bool
) -> HttpResponse:
    if isinstance(result, Authenticated):
        member = Member.objects.get(pk=result.member_id)
        start_session(request, member, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("overview")

    if isinstance(result, PasswordChangeRequired):
        request.session[PENDING_MEMBER] = result.member_id
        return redirect("password-change")

    if isinstance(result, TotpSetupRequired):
        request.session[PENDING_MEMBER] = result.member_id
        return redirect("totp-setup")

    if isinstance(result, Rejected):
        error = f"Неверная почта или пароль. Осталось попыток: {result.attempts_left}."
    else:
        error = LOCKED_TEXT
    return render(
        request,
        "accounts/login.html",
        {
            "form": LoginForm(two_factor_enabled=two_factor),
            "two_factor": two_factor,
            "error": error,
        },
    )


class Logout(PublicView):
    """
    Выход.

    Только `POST`, и это уточнение, а не перенос: функция принимала любой метод, хотя в макете
    выход — кнопка в форме. Выход меняет состояние, и открываться по ссылке ему незачем.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        end_session(request)
        return redirect("login")


class TotpSetup(PublicView):
    """
    Обязательная привязка аутентификатора — критерий 51.

    До её завершения доступа к остальным экранам нет: участник ещё не вошёл, в сессии лежит
    только отметка о пройденном пароле.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        member = _pending_member(request)
        if member is None:
            return redirect("login")
        return _totp_screen(request, member, _secret(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        member = _pending_member(request)
        if member is None:
            return redirect("login")

        secret = _secret(request)
        form = TotpBindingForm(request.POST)
        if not form.is_valid():
            return _totp_screen(
                request, member, secret, error="Введите шесть цифр из приложения."
            )

        bound = confirm_totp_binding(
            member_id=member.pk,
            secret=secret,
            code=form.totp_code(),
            at=SystemClock().now(),
            state_store=_store(),
        )
        if not bound:
            return _totp_screen(
                request, member, secret, error="Код не подошёл. Попробуйте следующий."
            )

        del request.session[PENDING_BINDING]
        del request.session[PENDING_MEMBER]
        start_session(request, member, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("overview")


def _pending_member(request: HttpRequest) -> Member | None:
    """Участник промежуточного шага. ``None`` — шага нет, идти некуда, кроме входа."""
    member_id = request.session.get(PENDING_MEMBER)
    if not member_id:
        return None
    return Member.objects.get(pk=member_id)


def _secret(request: HttpRequest) -> str:
    """Секрет держится в сессии между показом QR-кода и вводом кода: он один и тот же."""
    secret = request.session.get(PENDING_BINDING) or new_totp_secret()
    request.session[PENDING_BINDING] = secret
    return str(secret)


def _totp_screen(
    request: HttpRequest, member: Member, secret: str, error: str = ""
) -> HttpResponse:
    return render(
        request,
        "accounts/totp_setup.html",
        {
            "form": TotpBindingForm(),
            "secret": secret,
            "uri": provisioning_uri(secret=secret, email=member.email),
            "error": error,
        },
    )


class AcceptInvitation(PublicView):
    """
    Открытие ссылки-приглашения — ТЗ 3, критерии 30, 31.

    Открывший задаёт себе пароль сам. Ссылка одноразовая: повторное открытие и просроченная
    ссылка получают отказ с причиной, а не форму.
    """

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        return render(
            request, "accounts/join.html", {"form": PasswordChangeForm(), "token": token}
        )

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        form = PasswordChangeForm(request.POST)
        if not form.is_valid():
            return render(request, "accounts/join.html", {"form": form, "token": token})

        result = accept_invitation(
            token=token,
            password=form.new_password(),
            at=SystemClock().now(),
            state_store=_store(),
        )
        if isinstance(result, Refused):
            return render(request, "accounts/join.html", {"error": result.reason, "token": token})

        member = Member.objects.get(pk=result.member_id)
        start_session(request, member, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Пароль задан. Добро пожаловать.")
        return redirect("overview")


class PasswordChange(PublicView):
    """Смена пароля при первом входе — критерий 32: до смены доступа к экранам нет."""

    def get(self, request: HttpRequest) -> HttpResponse:
        if _pending_member(request) is None:
            return redirect("login")
        return render(request, "accounts/password_change.html", {"form": PasswordChangeForm()})

    def post(self, request: HttpRequest) -> HttpResponse:
        member = _pending_member(request)
        if member is None:
            return redirect("login")

        form = PasswordChangeForm(request.POST)
        if not form.is_valid():
            return render(request, "accounts/password_change.html", {"form": form})

        result = set_own_password(member_id=member.pk, password=form.new_password())
        if isinstance(result, Refused):
            return render(
                request,
                "accounts/password_change.html",
                {"form": form, "error": result.reason},
            )

        del request.session[PENDING_MEMBER]
        start_session(request, member, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Пароль изменён.")
        return redirect("overview")
