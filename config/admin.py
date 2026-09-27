"""Админка Django — **только режим разработчика**. Частью продукта не является.

Подключается в `config/settings/dev.py`, в `prod` её нет вовсе, и маршрут `admin/` появляется
только когда приложение установлено. Это осознанное ограничение, а не забывчивость: админка —
второй путь к тем же таблицам, минующий сервисы, и на боевой установке через него обошлись бы
предохранители 3.1 («нельзя отключить последнего администратора», критерий 84), запись в журнал
с автором, завершение сессий при смене почты (критерий 80) и разбор белого списка (10.2).

В режиме разработчика это допустимо и полезно: правишь данные на своём примере, не выдумывая
сценарий в интерфейсе. Но помнить про обход стоит — расхождение вроде «участник отключён, а
записи в журнале нет» после правки через админку будет объясняться именно этим.

Живёт в `config/`, а не в `apps/*/admin.py`, по той же причине: приложения описывают продукт,
а это инструмент разработчика, и путать их не нужно.

**Права участника здесь не проверяются таблицей разрешений Django.** `Member` наследует
`AbstractBaseUser` без `PermissionsMixin`, у него нет ни `is_staff`, ни `has_perm` — роли из ТЗ 3
хватает, а добавлять поля в сущность продукта ради инструмента разработчика было бы неправильно.
Поэтому доступ решает сам сайт, по роли.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import AuditRecord, Invitation, Member, Role
from apps.catalog.models import AllowlistEntry, Domain, GlobalSettings, Group
from apps.monitoring.models import (
    AddressObservationBucket,
    AddressRegistryEntry,
    Check,
    Event,
    Incident,
)
from apps.notifications.models import Notification


def _is_admin(request: HttpRequest) -> bool:
    user = request.user
    return bool(user.is_authenticated and user.is_active and user.role == Role.ADMIN)


class DevAdminSite(AdminSite):
    """Сайт админки, который пускает по роли участника — ТЗ 3."""

    site_header = "DNS monitoring · данные установки"
    site_title = "DNS monitoring"
    index_title = "Таблицы"

    def has_permission(self, request: HttpRequest) -> bool:
        return _is_admin(request)

    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Своей формы входа у админки нет — ведём на общий экран входа.

        Причина не в удобстве: штатная `AdminAuthenticationForm` требует `is_staff`, которого у
        `Member` нет, и отвергла бы даже администратора. Один вход на всё приложение вместо двух
        — и честнее, и работает.

        Три исхода, и каждый из них нужен:

        - уже пущен — сюда его привёл `admin_view` до входа, возвращаем на страницу админки;
        - вошёл, но не администратор — отказ, как и на любом другом закрытом экране: предлагать
          наблюдателю войти снова значило бы соврать о причине (так же ведёт себя `AdminView`);
        - не вошёл — общий экран входа.
        """
        del extra_context
        if self.has_permission(request):
            return redirect(reverse("admin:index", current_app=self.name))
        if request.user.is_authenticated:
            raise PermissionDenied
        # Адрес берётся из `next`, а не из полного пути: иначе он вложился бы сам в себя —
        # `/login/?next=/admin/login/%3Fnext%3D%252Fadmin%252F`.
        target = request.GET.get(REDIRECT_FIELD_NAME) or reverse("admin:index")
        return redirect_to_login(target, reverse("login"))


site = DevAdminSite(name="dev-admin")


class DevAdmin(admin.ModelAdmin):
    """Базовый класс: права решает сайт, поэтому здесь они открыты администратору целиком."""

    def has_module_permission(self, request: HttpRequest) -> bool:
        return _is_admin(request)

    def has_view_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return _is_admin(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return _is_admin(request)

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return _is_admin(request)

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return _is_admin(request)


class DomainAdmin(DevAdmin):
    list_display = ("name", "group", "watch_enabled", "next_check_at", "last_successful_check_at")
    list_filter = ("watch_enabled", "group")
    search_fields = ("name", "comment")
    list_select_related = ("group",)


class GroupAdmin(DevAdmin):
    list_display = ("name", "check_period_seconds", "notification_mode")
    search_fields = ("name",)


class GlobalSettingsAdmin(DevAdmin):
    """Строка настроек одна на установку (ТЗ 4.1), поэтому добавление и удаление закрыты.

    Это не урезание прав, а защита от `GlobalSettings.objects.get()`, который упадёт на второй
    строке и на нулевой — а зовут его из каждой проверки.
    """

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


class AllowlistEntryAdmin(DevAdmin):
    list_display = ("domain", "raw", "entry")
    search_fields = ("raw", "domain__name")
    list_select_related = ("domain",)


class MemberAdmin(DevAdmin):
    """Хеш пароля показан, но не редактируется.

    Иначе введённое в поле легло бы в базу как есть, то есть паролем стал бы открытый текст,
    а войти с ним всё равно не получилось бы. Пароль меняется через интерфейс или
    `manage.py create_admin`.
    """

    list_display = ("email", "name", "role", "is_active", "must_change_password", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("email", "name")
    readonly_fields = ("password", "last_login", "session_epoch")


class AuditRecordAdmin(DevAdmin):
    list_display = ("occurred_at", "action", "author_name")
    list_filter = ("action",)
    date_hierarchy = "occurred_at"


class InvitationAdmin(DevAdmin):
    list_display = ("member", "created_at", "expires_at", "opened_at")
    list_select_related = ("member",)


class EventAdmin(DevAdmin):
    list_display = ("occurred_at", "domain", "event_type", "author_name")
    list_filter = ("event_type",)
    search_fields = ("domain__name",)
    date_hierarchy = "occurred_at"
    list_select_related = ("domain",)


class CheckAdmin(DevAdmin):
    list_display = ("checked_at", "domain", "outcome", "answered_by")
    list_filter = ("outcome",)
    search_fields = ("domain__name",)
    date_hierarchy = "checked_at"
    list_select_related = ("domain",)


class AddressRegistryEntryAdmin(DevAdmin):
    list_display = ("domain", "address", "first_seen_at", "last_seen_at", "consecutive_misses")
    search_fields = ("domain__name",)
    list_select_related = ("domain",)


class AddressObservationBucketAdmin(DevAdmin):
    list_display = ("domain", "address", "bucket_size", "bucket_start")
    list_filter = ("bucket_size",)
    search_fields = ("domain__name",)
    list_select_related = ("domain",)


class IncidentAdmin(DevAdmin):
    list_display = ("domain", "opened_at", "closed_at", "last_message_at")
    search_fields = ("domain__name",)
    list_select_related = ("domain",)


class NotificationAdmin(DevAdmin):
    list_display = ("created_at", "channel", "status", "attempt", "sent_at")
    list_filter = ("channel", "status")
    date_hierarchy = "created_at"


site.register(Domain, DomainAdmin)
site.register(Group, GroupAdmin)
site.register(GlobalSettings, GlobalSettingsAdmin)
site.register(AllowlistEntry, AllowlistEntryAdmin)
site.register(Member, MemberAdmin)
site.register(AuditRecord, AuditRecordAdmin)
site.register(Invitation, InvitationAdmin)
site.register(Event, EventAdmin)
site.register(Check, CheckAdmin)
site.register(AddressRegistryEntry, AddressRegistryEntryAdmin)
site.register(AddressObservationBucket, AddressObservationBucketAdmin)
site.register(Incident, IncidentAdmin)
site.register(Notification, NotificationAdmin)
