# Глоссарий: термин ТЗ → идентификатор в коде

Источник терминов — раздел 2 ТЗ. Термины обязательны в коде, URL и интерфейсе, синонимы не вводятся.

Правила записи:

- Классы и перечисления — `PascalCase`, поля и функции — `snake_case`, значения перечислений в БД —
  строчные (`varchar(32)` по 4.0).
- Интервалы хранятся в секундах, суффикс `_seconds` обязателен (4.0).
- Моменты времени — суффикс `_at`.
- Ячейка с пометкой **⚠ Q-…** зависит от открытого вопроса в `docs/questions.md` и может измениться.

---

## 1. Раздел 2 ТЗ

| Термин ТЗ | Класс / перечисление | Поле, переменная, модуль | URL | Примечание |
|---|---|---|---|---|
| Домен | `Domain` (`apps.catalog`) | `domain`, `domain_id`; имя — `Domain.name` | `/domains/`, `/domains/<id>/` | Имя хранится нормализованным (10.1). |
| Группа | `Group` (`apps.catalog`) | `group`, `group_id` | `/groups/`, `/groups/<id>/` | Не путать с `django.contrib.auth.models.Group`. Группы Django в проекте не используются, импорт только из `apps.catalog.models`. |
| Проверка | `Check` (`apps.monitoring`) | `check`, `checked_at`; сервис `run_check()` | — | Ключи Redis `check:cooldown:*` (11.11). У модели Django есть служебный классовый метод `check()`, поле или метод с этим именем на модели не заводить. **«Проверка вхождения» в белый список (6.3) — другое понятие**, см. `AllowlistMatch` и раздел 3. Q-D23: на домен хранятся 20 последних успешных и 200 последних неудачных проверок, чистка — в `checker`. |
| Ответ | — | `answer: frozenset[IPAddress]` | — | ⚠ Q-E3: в `plan.md` это поле называется `ResolveResult.addresses`. |
| Разрешённые адреса и подсети | — | `allowlist` (`domain.allowlist`) | `/domains/<id>/allowlist/` | **Только `allowlist`**. Слова `allowed`/`resolved` в этом значении не использовать. |
| Запись списка | `AllowlistEntry` | `entry`, `raw`, `allowlist_entries` | — | Отдельный адрес — запись с полной маской (6.3, 12). Q-13.3 закрыт: `entry cidr` с индексом GiST `(entry inet_ops)`, исходный текст пользователя — `raw varchar(49)`. |
| Актуальный адрес | — | `is_fresh` | — | `actual` не использовать: по-английски это «фактический». `current` не использовать: занят «текущим ответом». |
| Порог актуальности | — | `freshness_threshold_seconds`; в вычислениях `freshness_threshold: timedelta` | — | В формулах ТЗ обозначен `W`. |
| Актуальный набор | — | `fresh_set: frozenset[IPAddress]` | — | |
| Статус адреса | `AddressStatus` | `status` | `?status=` | Вычисляется, не хранится (6.4). **Только `status`**, не путать с `allowlist`. Названия статусов изменены по Q-13.8: раздел 2 ТЗ ещё говорит «Решён». |
| ↳ «Совпал» | `AddressStatus.MATCHED` = `"matched"` | — | — | Q-13.8 закрыт. Корень `match` уже занят нужным смыслом (`AllowlistMatch`, `matched_entry`) и не сталкивается ни с `resolve*`, ни с `allowlist`. |
| ↳ «Не совпал» | `AddressStatus.UNMATCHED` = `"unmatched"` | — | — | Q-13.8 |
| ↳ «Не актуален» | `AddressStatus.STALE` = `"stale"` | — | — | |
| Реестр адресов | — | `address_registry` (обратная связь `domain.address_registry`) | `/domains/<id>/registry/` | Просто `registry` не использовать: занято `django.apps.registry`. |
| Запись реестра адресов (4.4) | `AddressRegistryEntry`, `AddressObservationBucket` | `first_seen_at`, `last_seen_at`, `consecutive_misses`; у корзины — `bucket_start`, `bucket_size` | — | Q-E1 закрыт: часовые корзины за 30 суток и суточные бессрочно, свёртка в `checker`. Q-13.2 закрыт: `consecutive_misses` считает подряд идущие `ANSWERED` без этого адреса, инцидент закрывается при K = 3 (константа). |
| Инцидент | `Incident` (`apps.monitoring`) | `incident`, `opened_at`, `closed_at` | — | |
| Событие | `Event`, `EventType` (`apps.monitoring`) | `event`, `event_type`, `payload`, `author`, `author_name` | `/events/` | Значения `EventType` — ровно имена из 7.1: `ADDRESS_APPEARED`, `ADDRESS_GONE`, `NOT_ALLOWED`, `BACK_TO_NORMAL`, `RESOLVE_FAILED`, `RESOLVE_RECOVERED`, `WATCH_STARTED`. Q-D4 закрыт: домен обязателен, автор обнуляемый со снимком имени (Q-D17). Записи без домена — в `AuditRecord`. |
| Уведомление | `Notification` (`apps.notifications`) | `notification`, `delivery_status` | — | Строка очереди исходящих: `attempt`, `next_attempt_at`, `last_error`. |

---

## 2. Термины из остальных разделов ТЗ

Предложение, требует подтверждения. Все термины взяты из текста ТЗ, `CLAUDE.md` или `plan.md`, новых
понятий нет. Отдельно отмечено, где разные слова обозначают одно и то же.

### Настройки и наследование (4, 5)

| Термин | Класс / перечисление | Поле, переменная | URL | Примечание |
|---|---|---|---|---|
| Общие настройки | `GlobalSettings` | `global_settings` | `/settings/` | Одна запись на установку (4.1). |
| Происхождение значения | `SettingOrigin` | `origin` | — | |
| ↳ «общая» / «от группы» / «своё» | `GLOBAL` / `GROUP` / `OWN` | — | — | |
| Значение «по умолчанию» у группы | — | `None` в поле группы | — | |
| Значение настройки, вычисленное по цепочке наследования | `EffectiveSetting[T]` | `value`, `origin`; функция `effective_settings()` | — | Не `ResolvedSetting`: слово `resolve` занято резолвом и статусом (13.8). |
| Период проверки | — | `check_period_seconds` | — | |
| Режим уведомлений | `NotificationMode` | `notification_mode` | — | |
| ↳ «Не уведомлять» | `NotificationMode.NONE` = `"none"` | — | — | |
| ↳ «При изменении адресов» | `NotificationMode.ADDRESS_CHANGE` = `"address_change"` | — | — | |
| ↳ «При выходе из разрешённых» | `NotificationMode.NOT_ALLOWED` = `"not_allowed"` | — | — | Имя совпадает с `EventType.NOT_ALLOWED`, так и задумано. |
| Сообщать о возврате в норму | — | `notify_back_to_normal` | — | |
| Интервал напоминаний | — | `reminder_interval_seconds` | — | «Не напоминать» и «по умолчанию» — разные состояния, в схеме их нельзя слить в `NULL`. |
| Окно слияния / порог слияния | — | `merge_window_seconds`, `merge_threshold` | — | Ключ Redis `notify:merge:*`. |
| Список DNS-серверов | — | `dns_servers` | — | Упорядоченный. |
| Количество повторов при ошибке резолва | — | `resolve_failure_threshold` | — | Q-C6 закрыт: это N из 7.1, по умолчанию 3, сетевых повторов нет. Подпись в интерфейсе — «Сколько неудачных проверок подряд считать сбоем» (исключение в Q-C14). |
| Срок хранения журнала / реестра | — | `event_retention_seconds`, `registry_retention_seconds` | — | `NULL` у реестра — бессрочно. |
| Типы записей | `RecordType.A` / `AAAA` / `CNAME` | `record_types`, `cname_target` | — | Q-D12 закрыт: флаг CNAME означает «показывать цель». Поле `cname_target varchar(255)` перезаписывается значением `canonical_name` из ответа на A-запрос — отдельного запроса не нужно. Событий не порождает. |
| Наблюдение включено | — | `watch_enabled` | — | Согласовано с `WATCH_STARTED`. |
| Комментарий | — | `comment` | — | |

### Проверка и статусы (6, 7, 11.1, 11.5, 11.6)

| Термин | Класс / перечисление | Поле, переменная | URL | Примечание |
|---|---|---|---|---|
| Резолв | `Resolver` (порт), `ResolveResult` | `resolve()` | — | Единственное место с `import dns`. |
| Исход резолва | `ResolveOutcome.ANSWERED` / `NO_RECORDS` / `UNAVAILABLE` | `outcome` | — | В `plan.md` класс назван `Outcome`, переименован, чтобы не путать с исходом проверки. ⚠ Q-E3 |
| Состояние домена | `DomainState` | `state` | `?state=` | |
| ↳ норма / расхождение / ошибка резолва | `NORMAL` / `DISCREPANCY` / `RESOLVE_ERROR` | — | — | |
| Базовая проверка | — | `is_baseline` | — | 7.4 |
| Совпавшая запись, причина совпадения | `AllowlistMatch` | `matched_entry` | — | «входит в …» / «точное совпадение с …» (6.3). |
| Нормализованная запись | — | `normalized` | — | 10.2 |
| Исход проверки (колонка «История») | `CheckOutcome` | `outcome` | — | |
| ↳ норма / набор изменился / есть несовпавший / ошибка резолва | `NORMAL` / `ADDRESS_SET_CHANGED` / `HAS_UNMATCHED` / `RESOLVE_ERROR` | — | — | Исход несостоявшейся проверки — ⚠ Q-D5. `HAS_UNMATCHED` вместо прежнего `UNRESOLVED` по Q-13.8: одно слово больше не стоит в двух перечислениях. |
| Колонка «История» | — | `check_history` | — | 20 отметок, ось — последовательность проверок. |
| Планировщик | — | `scheduler`; команда `run_checker` | — | Ключ `sched:lock:domain:{id}`. |
| Время следующей проверки | — | `next_check_at` | — | Единственное место расписания. |
| Тик планировщика / обход | — | `tick`, `tick_id` | — | «Идентификатор обхода» из 11.14 — это `tick_id`. |
| Ручная проверка | `CheckTrigger.SCHEDULED` / `MANUAL` | `trigger` | `/domains/<id>/check/`, `/groups/<id>/check/` | |
| Кулдаун | — | `cooldown` | — | |

### Уведомления (8)

| Термин | Класс / перечисление | Поле, переменная | URL | Примечание |
|---|---|---|---|---|
| Канал доставки | `NotificationChannel` (порт), `ChannelKind.TELEGRAM` / `EMAIL` / `WEBHOOK` | `channel` | — | |
| Подключение канала | — | `connection` | — | Только из окружения (8.3). |
| Адресат | — | `recipient` | — | В прототипе «Получатель» ⚠ Q-C14. |
| Состояние канала «Настроен» / «Не настроен» | — | `is_configured` | — | |
| Статус (результат) доставки | `DeliveryStatus` | `delivery_status` | — | |
| Очередь исходящих | — | selector `outbox` над `Notification` | — | Команда `run_notifier`. |
| Идентификатор сообщения в канале доставки | — | `channel_message_id` | — | `varchar(64)` |
| Напоминание | — | `reminder`, `last_message_at` | — | 8.5 |
| Сводное сообщение (сводка) | — | `merge_summary` | — | 8.6 |
| Состав сообщения (8.4) | — | `message_include_full_set`, `message_include_comment`, `message_include_link` | — | Q-C4 закрыт: только общие настройки, наследования нет. Значение «полного набора» — ⚠ Q-D10. |

### Участники и доступ (3, 3.1)

| Термин | Класс / перечисление | Поле, переменная | URL | Примечание |
|---|---|---|---|---|
| Участник | `Member` (`AUTH_USER_MODEL`) | `member` | `/members/` | ТЗ и прототип называют вкладку «Пользователи» ⚠ Q-C14. |
| Роль | `Role.ADMIN` / `Role.OBSERVER` | `role` | — | «Администратор» / «Наблюдатель». |
| Приглашение | `Invitation` | `invitation`, `token_hash` | `/join/<token>/` | URL из прототипа. Q-D16 закрыт: в БД хранится только хеш, открытый текст — ключ `auth:invite:{id}` в базе состояния со сроком жизни приглашения. |
| Сессия | — | ключи `session:{ключ}` и `session:member:{id}` | — | Q-D20 закрыт: Redis, база **состояния**, не кэш. Набор `session:member:*` нужен для «Завершить все сессии» (3.1). |
| Сброс пароля | — | `reset_password()` | `/reset/<token>/` | URL из прототипа. |
| Потребовать смену при первом входе | — | `must_change_password` | — | |
| Второй фактор / аутентификатор | — | `totp_secret`, `unbind_authenticator()` | — | Включается `AUTH_2FA_ENABLED`. |
| Блокировка входа / счётчик неудачных попыток | — | `login_lock`, `login_fail` | — | Ключи `auth:lock:*`, `auth:fail:*`. |
| Отключение участника | — | `is_active`, `deactivate()` | — | |
| Состояние доступа участника | `MemberAccessState` | `access_state()` (`apps.accounts.selectors`) | — | Q-D21 закрыт: вычисляется, не хранится. Приоритет `DISABLED` → `INVITATION_PENDING` → `PASSWORD_ISSUED` → `ACTIVE` (ТЗ 3, Q-C17). |
| Действие в журнале без домена | `AuditRecord`, `AuditAction` (`apps.accounts`) | `audit_records` | `/events/` | Q-D4: изменения участников (3.1) и остановка проверок (11.13). Пользователю как отдельный термин не показывается. |
| Срок сессии | — | `session_lifetime_seconds`, `login_failure_threshold` | — | Q-C5 закрыт: оба поля в `GlobalSettings`, срок скользящий, потолка нет. Срок блокировки 15 минут — не настройка, а TTL ключа `auth:lock:*`. |

### Экраны, ввод, эксплуатация (9–11)

| Термин | Класс / перечисление | Поле, переменная | URL | Примечание |
|---|---|---|---|---|
| Обзор | — | `overview` | `/` | Ключ `cache:view:overview`. |
| Открытые расхождения | — | `open_incidents` | — | 11.9 |
| Журнал событий | `AuditRecord` (`apps.accounts`) | `events`, `audit_records` | `/events/` | Отдельного идентификатора `journal` нет, чтобы не заводить синоним. Q-D4: в интерфейсе журнал **один**, под ним две таблицы — `Event` (домен обязателен) и `AuditRecord` (действия с участниками по 3.1 и остановка проверок по 11.13). Склейка — в селекторе, `AuditRecord` пользователю как отдельный термин не показывается. |
| Выгрузка журнала | — | `export_events_csv()` | `/events/export.csv` | |
| Импорт списком | — | модуль `domain_import` | `/domains/import/` | `import` — ключевое слово Python. |
| Перенос домена в другую группу | — | `move_domain()` | `/domains/<id>/move/` | |
| График реестра, след | — | `registry_trace`, `trace` | — | Ключ `cache:trace:*`. |
| Корзина / отметка / масштаб | `BucketSize.HOUR` / `DAY` | `bucket`, `bucket_size`, `mark`, `scale` | — | 9.2. «Размер корзины» — термин из ключа `cache:trace:*` (11.12), не новое понятие. |
| Линейка подсети | — | `subnet_ruler` | — | 11.4 |
| Хранилище рабочего состояния | `StateStore` (порт), `StateKey` | `acquire()`, `release()`, `is_held()`, `ping()` | — | 11.11. Ключ без срока жизни выразить нельзя: `StateKey` несёт TTL в себе, записи по голой строке в порте нет. Боевая реализация — `RedisStateStore` (база `REDIS_DB_STATE`, `noeviction`), тестовая — `FakeStateStore` на часах. Имена и сроки ключей дословно из таблицы 11.11 плюс `auth:invite:*` (Q-D16) и `session:*` (Q-D20). |
| Проба работоспособности | — | `healthcheck` | `/health/` | 11.13 |
| Авария | — | `outage` | — | 11.13 |

---

## 3. Запрещённые синонимы

| Нельзя | Нужно | Почему |
|---|---|---|
| `whitelist`, `allowed_ips`, `allowed` для списка | `allowlist` | Раздел 2 |
| `resolved` для записи списка | `allowlist_entry` | Раздел 2 |
| `resolved`, `unresolved` для статуса адреса | `matched`, `unmatched` | Q-13.8. Относится и к локальным переменным: `resolved = effective_settings(...)` — уже нарушение |
| `actual`, `current` для актуальности | `fresh` | Ложный друг переводчика / занято «текущим ответом» |
| `check` для вхождения в белый список | `match`, `contains`, `AllowlistMatch` | Раздел 2 называет проверкой цикл резолва (`Check`), а 6.3 тем же словом — вхождение адреса в список. Разные вещи |
| голые `entry`, `record` | `allowlist_entry`, `registry_entry`, `record_type`, `event` | Слово «запись» в ТЗ означает четыре разных понятия: 4.0, 4.3, 4.4 и 7.1 |
| `user` в доменном коде | `member` | ⚠ Q-C14. Запрет распространяется на доменный код и наши идентификаторы; штатные `request.user`, `AUTH_USER_MODEL`, `get_user_model()` остаются как есть |
| `journal`, `log` для журнала событий | `events` | Один термин на одно понятие |
| `registry` без уточнения | `address_registry` | Конфликт с реестром приложений Django |
| `Outcome` без уточнения | `ResolveOutcome` / `CheckOutcome` | Два разных исхода |
