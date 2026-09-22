"""Реестр адресов, корзины наблюдений и инциденты — ТЗ 4.4, 4.6, 9.2.

Событий здесь пока нет: по Q-D4 у события появляется автор, то есть внешний ключ на участника,
а состав модели участника держат Q-D16, Q-D17, Q-D20 и Q-D21. TODO(ТЗ 4.5): ``Event`` вместе
с ``apps.accounts``.
"""

from django.db import models

from apps.catalog.models import Domain
from core.fields import InetField

CHANNEL_MESSAGE_ID_MAX_LENGTH = 64
BUCKET_SIZE_MAX_LENGTH = 8


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
