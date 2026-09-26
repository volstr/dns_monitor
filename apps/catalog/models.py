"""Домены, группы и общие настройки — ТЗ 4.1, 4.2, 4.3.

Соглашения 4.0: ключи ``bigint`` (``DEFAULT_AUTO_FIELD``), текст — ``varchar(n)`` с явной длиной,
интервалы — целое число секунд, перечисления — ``varchar(32)`` с ограничением значений.
"""

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GistIndex
from django.db import models

from core.fields import CidrField
from core.types.dns import RecordType

DOMAIN_NAME_MAX_LENGTH = 255
GROUP_NAME_MAX_LENGTH = 120
COMMENT_MAX_LENGTH = 2000
ENUM_MAX_LENGTH = 32
IP_ADDRESS_MAX_LENGTH = 45
ALLOWLIST_ENTRY_MAX_LENGTH = 49
#: Идентификатор чата в канале доставки — та же длина, что у идентификатора сообщения (4.0).
CHANNEL_RECIPIENT_MAX_LENGTH = 64
#: Почта участника по 4.0 — предел адреса по RFC.
EMAIL_MAX_LENGTH = 320
#: Ссылка по 4.0.
URL_MAX_LENGTH = 2048


class NotificationMode(models.TextChoices):
    """Режимы уведомлений — ТЗ 8.1."""

    NONE = "none", "Не уведомлять"
    ADDRESS_CHANGE = "address_change", "При изменении адресов"
    NOT_ALLOWED = "not_allowed", "При выходе из разрешённых"


# Перечисление живёт в core: его знают и порт резолвера, и модель, а core про apps не знает.
RECORD_TYPE_CHOICES = [
    (RecordType.A.value, "A"),
    (RecordType.AAAA.value, "AAAA"),
    (RecordType.CNAME.value, "CNAME"),
]


def default_record_types() -> list[str]:
    """A и AAAA — временное решение Q-D13 по карточке домена в прототипе."""
    return [RecordType.A.value, RecordType.AAAA.value]


class GlobalSettings(models.Model):
    """Одна запись на установку — ТЗ 4.1.

    Единственность обеспечена ограничением на первичный ключ: вторая строка не вставится.
    """

    check_period_seconds = models.PositiveIntegerField(default=5 * 60)
    freshness_threshold_seconds = models.PositiveIntegerField(default=24 * 60 * 60)
    notification_mode = models.CharField(
        max_length=ENUM_MAX_LENGTH,
        choices=NotificationMode.choices,
        default=NotificationMode.NOT_ALLOWED,
    )
    notify_back_to_normal = models.BooleanField(default=True)
    # Ноль означает «не напоминать» (ТЗ 8.5). У группы то же поле может быть NULL — и это уже
    # «по умолчанию»: два разных состояния, сливать их нельзя.
    reminder_interval_seconds = models.PositiveIntegerField(default=6 * 60 * 60)

    # Слияние массовых событий — ТЗ 8.6.
    merge_window_seconds = models.PositiveIntegerField(default=60)
    merge_threshold = models.PositiveIntegerField(default=5)

    # Упорядоченный список: первым спрашивают верхний (ТЗ 6.1).
    dns_servers = ArrayField(
        models.CharField(max_length=IP_ADDRESS_MAX_LENGTH),
        default=list,
        blank=True,
    )
    # Сверх перечня 4.1, по решению разработчика (Q-D27). Снятый флаг означает «адресов IPv6
    # не существует»: AAAA не спрашивается ни у одного домена, такие адреса не попадают ни в
    # реестр, ни в события, ни на экраны. Главнее флага AAAA у домена (4.3): иначе «полностью
    # игнорируем» соблюдалось бы не полностью.
    check_ipv6 = models.BooleanField(default=True)
    # Q-C6: это N из 7.1 — сколько неудачных проверок подряд считать сбоем домена.
    resolve_failure_threshold = models.PositiveIntegerField(default=3)

    # Сроки хранения — ТЗ 11.3. NULL у реестра означает «бессрочно».
    event_retention_seconds = models.PositiveIntegerField(default=90 * 24 * 60 * 60)
    # Когда уборка по 11.3 проходила в последний раз. В базе, а не в памяти процесса:
    # перезапуск не должен ни пропускать уборку, ни запускать её заново каждые пять секунд.
    last_purge_at = models.DateTimeField(null=True, blank=True, default=None)
    registry_retention_seconds = models.PositiveIntegerField(null=True, blank=True, default=None)

    # Состав сообщения — ТЗ 8.4, Q-C4: только здесь, наследования нет.
    message_include_full_set = models.BooleanField(default=True)
    message_include_comment = models.BooleanField(default=True)
    message_include_link = models.BooleanField(default=True)

    # Вход — ТЗ 3, Q-C5. Срок скользящий; срок блокировки задан TTL ключа auth:lock:* (11.11).
    session_lifetime_seconds = models.PositiveIntegerField(default=7 * 24 * 60 * 60)
    login_failure_threshold = models.PositiveIntegerField(default=5)

    # Адресаты каналов доставки — ТЗ 8.3, Q-D11: один адресат на вид канала.
    # Здесь только «кому отправлять». Всё, что даёт доступ к чужой инфраструктуре — токен бота,
    # адрес и учётные данные почтового сервера, — живёт в окружении и сюда не попадает: 8.3
    # прямо запрещает администратору приложения направлять исходящее через свой сервер.
    # Пустое значение означает «Не настроен» и является нормальным режимом.
    telegram_chat_id = models.CharField(
        max_length=CHANNEL_RECIPIENT_MAX_LENGTH, blank=True, default=""
    )
    email_recipients = ArrayField(
        models.EmailField(max_length=EMAIL_MAX_LENGTH), default=list, blank=True
    )
    webhook_url = models.CharField(max_length=URL_MAX_LENGTH, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(pk=1),
                name="global_settings_single_row",
            ),
            models.CheckConstraint(
                condition=models.Q(check_period_seconds__gte=60),
                name="global_settings_check_period_at_least_minute",
            ),
        ]

    def __str__(self) -> str:
        return "Общие настройки"


class Group(models.Model):
    """Набор доменов с общим расписанием и режимом уведомлений — ТЗ 4.2.

    NULL в наследуемом поле означает «по умолчанию»: значение берётся из общих настроек.
    Разрешённых адресов группа не содержит (ТЗ 4.2, 12, Q-C1).
    """

    name = models.CharField(max_length=GROUP_NAME_MAX_LENGTH, unique=True)
    comment = models.CharField(max_length=COMMENT_MAX_LENGTH, blank=True, default="")

    check_period_seconds = models.PositiveIntegerField(null=True, blank=True, default=None)
    freshness_threshold_seconds = models.PositiveIntegerField(null=True, blank=True, default=None)
    # NULL означает «по умолчанию» и обязан отличаться от любого заданного значения (ТЗ 4.2, 5).
    # Пустая строка вместо NULL слила бы два состояния в одно — ровно то, что глоссарий
    # запрещает делать с интервалом напоминаний. Поэтому DJ001 здесь подавлен осознанно.
    notification_mode = models.CharField(  # noqa: DJ001
        max_length=ENUM_MAX_LENGTH,
        choices=NotificationMode.choices,
        null=True,
        blank=True,
        default=None,
    )
    notify_back_to_normal = models.BooleanField(null=True, blank=True, default=None)
    reminder_interval_seconds = models.PositiveIntegerField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Domain(models.Model):
    """Наблюдаемое доменное имя — ТЗ 4.3.

    Периода проверки и режима уведомлений у домена нет: они берутся у группы (ТЗ 4.3, 12).
    """

    # Имя хранится нормализованным: нижний регистр, без конечной точки, punycode (ТЗ 10.1).
    name = models.CharField(max_length=DOMAIN_NAME_MAX_LENGTH, unique=True)
    # Удаление группы не удаляет домены — они переходят в «без группы» (ТЗ 11.3, критерий 24).
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="domains",
    )
    comment = models.CharField(max_length=COMMENT_MAX_LENGTH, blank=True, default="")
    record_types = ArrayField(
        models.CharField(max_length=ENUM_MAX_LENGTH, choices=RECORD_TYPE_CHOICES),
        default=default_record_types,
    )
    # Q-D12: цель цепочки CNAME из `canonical_name` ответа. Заполняется, только если у домена
    # включён флаг CNAME и цель отличается от имени. Событий не порождает, в реестр не идёт.
    cname_target = models.CharField(
        max_length=DOMAIN_NAME_MAX_LENGTH, blank=True, default=""
    )
    watch_enabled = models.BooleanField(default=True)
    # Единственное место расписания (ТЗ 11.1, plan.md этап 4). Заполняется при создании домена.
    next_check_at = models.DateTimeField(null=True, blank=True, default=None)
    # Q-D6: от него считается, была ли пауза дольше порога актуальности, то есть базовая ли
    # проверка (7.4). NO_RECORDS успешной проверкой не считается и сюда не пишется.
    last_successful_check_at = models.DateTimeField(null=True, blank=True, default=None)
    # Критерии 10-11: сколько проверок подряд домен не резолвится. RESOLVE_FAILED порождается
    # при достижении порога и не повторяется, пока не было успешного резолва (7.1).
    consecutive_resolve_failures = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(record_types=[]),
                name="domain_has_record_type",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class AllowlistEntry(models.Model):
    """Запись белого списка — ТЗ 4.3, 6.3, 10.2.

    Q-13.3: колонка ``cidr`` отвергает подсеть с ненулевой хостовой частью, а исходный текст
    пользователя хранится в ``raw`` — 6.3 требует показывать запись «как ввёл пользователь».
    Отдельный адрес — это запись с полной маской (12), поэтому «точное совпадение с …» против
    «входит в …» определяется длиной префикса, а не отдельным полем.
    """

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="allowlist")
    entry = CidrField()
    raw = models.CharField(max_length=ALLOWLIST_ENTRY_MAX_LENGTH)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "entry"], name="allowlist_entry_unique"),
        ]
        indexes = [
            # Вхождение адреса в подсеть — один запрос с `>>=`, а не выгрузка списка в Python
            # (plan.md, этап 2).
            GistIndex(fields=["entry"], opclasses=["inet_ops"], name="allowlist_entry_gist"),
        ]

    def __str__(self) -> str:
        return self.raw
