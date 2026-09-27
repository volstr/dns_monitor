"""
Формы входа — ТЗ 3, критерии 32, 50.

Поле кода не скрывается и не блокируется, а **отсутствует**, когда второй фактор выключен: ТЗ 3
говорит это прямо, а критерий 50 проверяет, что его разметки нет в ответе сервера. Поэтому поле
удаляется из формы, а не прячется стилями.
"""

from __future__ import annotations

from typing import Any

from django import forms

MIN_PASSWORD_LENGTH = 12


class LoginForm(forms.Form):
    """Вход по почте и паролю. Почта служит логином (ТЗ 3)."""

    email = forms.EmailField(max_length=320)
    password = forms.CharField(widget=forms.PasswordInput, max_length=200)
    code = forms.CharField(required=True, max_length=6)

    def __init__(self, *args: Any, two_factor_enabled: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not two_factor_enabled:
            del self.fields["code"]

    def login_email(self) -> str:
        return str(self.cleaned_data.get("email") or "")

    def login_password(self) -> str:
        return str(self.cleaned_data.get("password") or "")

    def totp_code(self) -> str:
        return str(self.cleaned_data.get("code") or "")


class TotpBindingForm(forms.Form):
    """Обязательная привязка аутентификатора — критерий 51."""

    code = forms.CharField(max_length=6)

    def totp_code(self) -> str:
        return str(self.cleaned_data.get("code") or "")


class PasswordChangeForm(forms.Form):
    """Смена пароля при первом входе — критерий 32."""

    password = forms.CharField(widget=forms.PasswordInput, min_length=MIN_PASSWORD_LENGTH)
    repeat = forms.CharField(widget=forms.PasswordInput)

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        if data.get("password") != data.get("repeat"):
            message = "Пароли не совпадают."
            raise forms.ValidationError(message)
        return data

    def new_password(self) -> str:
        return str(self.cleaned_data.get("password") or "")
