"""Реестр адресов, корзины наблюдений, инциденты и события — ТЗ 4.4, 4.5, 4.6, 9.2."""

from django.conf import settings
from django.db import models

from apps.catalog.models import Domain
from core.fields import InetField
from core.types.checking import CheckOutcome

CHANNEL_MESSAGE_ID_MAX_LENGTH = 64
BUCKET_SIZE_MAX_LENGTH = 8
ENUM_MAX_LENGTH = 32
NAME_MAX_LENGTH = 160
SERVER_MAX_LENGTH = 45
ERROR_MAX_LENGTH = 500

# Перечисление живёт в core: исход проверки знает и доменный код, и модель.
CHECK_OUTCOME_CHOICES = [
    (CheckOutcome.NORMAL.value, "Норма"),
    (CheckOutcome.ADDRESS_SET_CHANGED.value, "Набор адресов изменился"),
    (CheckOutcome.HAS_UNMATCHED.value, "Есть несовпавший адрес"),
    (CheckOutcome.NOT_CONFIRMED.value, "Совпадений нет"),
    (CheckOutcome.RESOLVE_ERROR.value, "Ошибка резолва"),
    (CheckOutcome.NOT_PERFORMED.value, "Не состоялась"),
]


class BucketSize(models.TextChoices):
    """Размер корзины следа — термин из ключа ``cache:trace:*`` (ТЗ 11.12), Q-E1."""

    HOUR = "hour", "Час"
    DAY = "day", "Сутки"


class AddressRegistryEntry(models.Model):
    """Запись реестра адресов — ТЗ 4.4. Уникальна по паре «домен + адрес»."""

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="address_registry")
    address = InetField()
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    # Q-13.2: сколько подряд ответов (только ANSWERED) пришло без этого адреса. При K = 3
    # инцидент закрывается, не дожидаясь порога актуальности. Обнуляется появлением в ответе.
    consecutive_misses = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "address"], name="address_registry_entry_unique"
            ),
        ]
        indexes = [
            # Актуальный набор — это выборка по домену и границе last_seen_at (ТЗ 6.2).
            models.Index(fields=["domain", "last_seen_at"], name="registry_domain_last_seen"),
        ]

    def __str__(self) -> str:
        return f"{self.domain.name} → {self.address}"


class AddressObservationBucket(models.Model):
    """Корзина наблюдений для графика реестра — ТЗ 9.2, Q-E1.

    Хранится факт: корзина существует, значит адрес отдавался хотя бы в одном ответе за её
    период. Значения нет — по 9.2 отметка либо закрашена, либо нет, а цвет берётся из текущего
    статуса адреса. Часовые корзины живут 30 суток, суточные — бессрочно, свёртку делает
    ``run_checker``.
    """

    domain = models.ForeignKey(
        Domain, on_delete=models.CASCADE, related_name="observation_buckets"
    )
    address = InetField()
    bucket_size = models.CharField(max_length=BUCKET_SIZE_MAX_LENGTH, choices=BucketSize.choices)
    bucket_start = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "address", "bucket_size", "bucket_start"],
                name="observation_bucket_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["domain", "bucket_size", "bucket_start"],
                name="bucket_domain_size_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.address} · {self.bucket_size} · {self.bucket_start:%Y-%m-%d %H}"


class Incident(models.Model):
    """Период, в течение которого у домена есть хотя бы один нерешённый адрес — ТЗ 4.6."""

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="incidents")
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True, default=None)
    # Нужен, чтобы ответить в ту же ветку при закрытии и напоминании (ТЗ 7.3, 8.5).
    channel_message_id = models.CharField(
        max_length=CHANNEL_MESSAGE_ID_MAX_LENGTH, blank=True, default=""
    )
    # От него отсчитывается интервал напоминаний (ТЗ 8.5), а не от момента открытия.
    last_message_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        constraints = [
            # У домена не может быть двух открытых инцидентов одновременно (ТЗ 7.3).
            models.UniqueConstraint(
                fields=["domain"],
                condition=models.Q(closed_at__isnull=True),
                name="incident_single_open_per_domain",
            ),
        ]
        indexes = [
            # Блок «Открытые расхождения» сортирует по возрасту (ТЗ 11.9).
            models.Index(fields=["opened_at"], name="incident_opened_at"),
        ]

    def __str__(self) -> str:
        return f"{self.domain.name} с {self.opened_at:%Y-%m-%d %H:%M}"


class Check(models.Model):
    """Одна проверка одного домена — ТЗ 11.5, Q-D5.

    Ось колонки «История» — последовательность проверок, а не время (11.5), поэтому строки
    берутся последними двадцатью, а не за период.

    Несостоявшаяся проверка (ни один сервер не ответил) пишется сюда с исходом
    ``NOT_PERFORMED`` и **не** попадает в журнал событий: по 6.1 реестр и статусы при ней не
    меняются, а по Q-D5 место такой записи — история проверок и структурный лог.

    Поля ``check`` у модели нет намеренно: у Django есть служебный классовый метод с этим именем.
    """

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="checks")
    checked_at = models.DateTimeField()
    outcome = models.CharField(max_length=ENUM_MAX_LENGTH, choices=CHECK_OUTCOME_CHOICES)
    #: Сервер, который ответил. Пусто у несостоявшейся проверки.
    answered_by = models.CharField(max_length=SERVER_MAX_LENGTH, blank=True, default="")
    #: Почему проверка не состоялась (Q-D5). Уходит и в структурный лог.
    error = models.CharField(max_length=ERROR_MAX_LENGTH, blank=True, default="")

    class Meta:
        indexes = [
            # Колонка «История» берёт ровно 20 последних проверок домена (11.5).
            models.Index(fields=["domain", "-checked_at"], name="check_domain_checked_at"),
        ]

    def __str__(self) -> str:
        return f"{self.domain.name} · {self.checked_at:%Y-%m-%d %H:%M} · {self.outcome}"


class EventType(models.TextChoices):
    """Закрытый список типов событий — ТЗ 7.1. Русские названия утверждены в Q-C11.

    Идентификаторы не меняются никогда — это закрытый список 7.1. Названия обязательны в
    журнале, фильтрах и заголовках сообщений: одно событие называется одним словом.
    """

    ADDRESS_APPEARED = "address_appeared", "Появился адрес"
    ADDRESS_GONE = "address_gone", "Адрес пропал"
    #: Не «Адрес не решён»: «решён» по Q-13.8 — это статус адреса, а событие говорит,
    #: что случилось. «Вне разрешённых» — слова режима 8.1, то есть термин ТЗ.
    NOT_ALLOWED = "not_allowed", "Адрес вне разрешённых"
    BACK_TO_NORMAL = "back_to_normal", "Возврат в норму"
    RESOLVE_FAILED = "resolve_failed", "Домен не резолвится"
    RESOLVE_RECOVERED = "resolve_recovered", "Домен снова резолвится"
    WATCH_STARTED = "watch_started", "Наблюдение начато"


class Event(models.Model):
    """Запись журнала, относящаяся к домену — ТЗ 4.5.

    Q-D4: домена без события не бывает, поле обязательное. Записи, у которых домена нет по
    определению — действия с участниками (3.1) и остановка проверок (11.13), — живут в
    ``apps.accounts.AuditRecord``, а в интерфейсе оба источника склеиваются в один журнал.

    Автор обнуляемый со снимком имени (Q-D17): прототип показывает «Наблюдение включено ·
    Анна Кирилова», а у событий, порождённых проверкой, автора нет вовсе.
    """

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=ENUM_MAX_LENGTH, choices=EventType.choices)
    occurred_at = models.DateTimeField()
    # core.types.events.EventPayload — какие адреса затронуты (4.5).
    payload = models.JSONField(default=dict, blank=True)
    incident = models.ForeignKey(
        Incident, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_authored",
    )
    author_name = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")

    # 4.5: «Ссылка на связанное уведомление, если оно отправлялось». Связь ведёт событие →
    # уведомление, потому что при слиянии (8.6) двенадцать событий уходят одним сводным
    # сообщением. Пусто — уведомление не отправлялось: режим «Не уведомлять», базовая проверка
    # (7.4) или событие, которое по 8.1 уведомления не порождает.
    notification = models.ForeignKey(
        "notifications.Notification",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    class Meta:
        indexes = [
            models.Index(fields=["domain", "occurred_at"], name="event_domain_occurred"),
            # Журнал и выгрузка сортируются по времени с фильтром по типу (ТЗ 11.7).
            models.Index(fields=["occurred_at"], name="event_occurred_at"),
        ]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} · {self.domain.name}"
