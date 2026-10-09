"""Маршруты каталога. Адреса — из прототипа и глоссария."""

from __future__ import annotations

from django.urls import URLPattern, path

from apps.catalog.views import (
    AllowlistCheck,
    ChannelDisconnect,
    ChannelProbe,
    DomainCheckNow,
    DomainCreate,
    DomainDelete,
    DomainDetail,
    DomainImport,
    DomainImportApply,
    DomainList,
    DomainMove,
    DomainMoveApply,
    DomainSave,
    DomainWatch,
    GlobalSettingsSave,
    GlobalSettingsScreen,
    GroupCheckNow,
    GroupDomains,
    GroupEdit,
    GroupList,
    GroupSave,
    Journal,
    JournalCsv,
    OverviewScreen,
)

urlpatterns: list[URLPattern] = [
    path('', OverviewScreen.as_view(), name='overview'),
    path('domains/', DomainList.as_view(), name='domain-list'),
    path('domains/new/', DomainCreate.as_view(), name='domain-new'),
    # Импорт списком (10.3): разбор и подтверждение — два разных адреса, два разных шага.
    path('domains/import/', DomainImport.as_view(), name='domain-import'),
    path('domains/import/apply/', DomainImportApply.as_view(), name='domain-import-apply'),
    # Адрес из 8.4: ссылка в уведомлении ведёт сюда же.
    path('domains/<int:domain_id>/', DomainDetail.as_view(), name='domain-detail'),
    path('domains/<int:domain_id>/save/', DomainSave.as_view(), name='domain-save'),
    path('domains/<int:domain_id>/watch/', DomainWatch.as_view(), name='domain-watch'),
    path('domains/<int:domain_id>/delete/', DomainDelete.as_view(), name='domain-delete'),
    # Перенос в другую группу (10.4): сначала сводка изменений, потом подтверждение.
    path('domains/<int:domain_id>/move/', DomainMove.as_view(), name='domain-move'),
    path(
        'domains/<int:domain_id>/move/apply/',
        DomainMoveApply.as_view(),
        name='domain-move-apply',
    ),
    path('domains/<int:domain_id>/check/', DomainCheckNow.as_view(), name='domain-check'),
    # Построчная проверка списка через HTMX (10.2).
    path('domains/<int:domain_id>/allowlist/', AllowlistCheck.as_view(), name='allowlist-check'),
    path('groups/', GroupList.as_view(), name='group-list'),
    path('groups/new/', GroupEdit.as_view(), name='group-new'),
    path('groups/new/save/', GroupSave.as_view(), name='group-create'),
    path('groups/<int:group_id>/', GroupEdit.as_view(), name='group-edit'),
    path('groups/<int:group_id>/save/', GroupSave.as_view(), name='group-save'),
    path('groups/<int:group_id>/domains/', GroupDomains.as_view(), name='group-domains'),
    path('groups/<int:group_id>/check/', GroupCheckNow.as_view(), name='group-check'),
    path('events/', Journal.as_view(), name='event-list'),
    path('events/export.csv', JournalCsv.as_view(), name='event-export'),
    path('settings/', GlobalSettingsScreen.as_view(), name='settings'),
    path('settings/save/', GlobalSettingsSave.as_view(), name='settings-save'),
    # Кнопки блока «Каналы доставки» из макета. Отправка идёт мимо очереди: кнопка обязана
    # ответить сразу, а очередь ждёт окна слияния.
    path('settings/channel/probe/', ChannelProbe.as_view(), name='channel-probe'),
    path('settings/channel/disconnect/', ChannelDisconnect.as_view(), name='channel-disconnect'),
]
