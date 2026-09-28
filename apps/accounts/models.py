"""
Участники, приглашения и журнал действий — ТЗ 3, 3.1, 11.13.

Состояние доступа участника («Активен», «Ссылка не открыта», «Пароль выдан, не менялся»,
«Отключён») колонкой не хранится: по Q-D21 оно вычисляется из четырёх источников, см.
``apps.accounts.selectors.access``. Хранимое поле пришлось бы держать в согласии со всеми
четырьмя сразу, и любая правка мимо сервиса оставляла бы его врать.

Сессии здесь тоже не хранятся: по Q-D20 они живут в Redis, в базе состояния.
"""

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils.crypto import salted_hmac

EMAIL_MAX_LENGTH = 320
NAME_MAX_LENGTH = 160
PASSWORD_MAX_LENGTH = 255
ENUM_MAX_LENGTH = 32
TOKEN_HASH_MAX_LENGTH = 64
TOTP_SECRET_MAX_LENGTH = 64


class Role(models.TextChoices):
    """Две роли — ТЗ 3."""

    ADMIN = "admin", "Администратор"
    OBSERVER = "observer", "Наблюдатель"


class MemberManager(BaseUserManager["Member"]):
    """
    Тонкий менеджер: хеширование пароля и нормализация почты.

    Полные сценарии создания участника — приглашение письмом, ссылкой и выдачей пароля (ТЗ 3) —
    живут в `apps.accounts.services.members`, а не здесь.
    """

    def create_superuser(self, email: str, name: str, password: str) -> "Member":
        """
        Поддержка `manage.py createsuperuser` — ТЗ 3.

        «Суперпользователя» в этом продукте нет: роли ровно две, «Администратор» и
        «Наблюдатель» (раздел 3). Метод существует потому, что `createsuperuser` — рефлекс любого,
        кто работал с Django, и падение с `AttributeError` вместо объяснения никому не помогает.
        Создаётся обычный администратор, теми же правилами, что и у `manage.py create_admin`:
        второго администратора так завести нельзя, для этого есть вкладка «Участники».
        """
        # Импорт внутри метода: сервис знает про модель, и на уровне модуля вышел бы цикл.
        from datetime import UTC, datetime

        from apps.accounts.services.bootstrap import AdminNotCreated, create_first_admin

        result = create_first_admin(
            email=email, name=name, password=password, at=datetime.now(UTC)
        )
        if isinstance(result, AdminNotCreated):
            raise ValueError(result.reason)
        return self.get(pk=result.member_id)

    def create_member(
        self,
        *,
        email: str,
        name: str,
        role: str = Role.OBSERVER,
        password: str | None = None,
        **extra: object,
    ) -> "Member":
        member: Member = self.model(
            email=self.normalize_email(email), name=name, role=role, **extra
        )
        # Пароль хранится только хешем современного алгоритма с солью (ТЗ 3).
        member.set_password(password)
        member.save(using=self._db)
        return member


class Member(AbstractBaseUser):
    """Участник — ТЗ 3. Почта служит логином."""

    # varchar(320) — предел адреса по RFC (4.0). Уникальность в пределах установки.
    email = models.EmailField(max_length=EMAIL_MAX_LENGTH, unique=True)
    name = models.CharField(max_length=NAME_MAX_LENGTH)
    role = models.CharField(max_length=ENUM_MAX_LENGTH, choices=Role.choices, default=Role.OBSERVER)
    # Отключение — основной способ прекратить доступ (3.1). Удаление доступно только тем,
    # кто ни разу не входил, и проверяется сервисом (критерии 85, 86).
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=False)
    # Привязка аутентификатора. Пустая строка — не привязан; отвязка по 3.1 очищает поле.
    totp_secret = models.CharField(max_length=TOTP_SECRET_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField()
    created_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_members"
    )

    # Хеш пароля — varchar(255) с запасом на смену алгоритма (4.0). Поле переопределяет
    # AbstractBaseUser, где оно короче.
    password = models.CharField(max_length=PASSWORD_MAX_LENGTH)

    # Поколение сессий. Меняется, когда все сессии участника нужно завершить: смена почты (3.1,
    # критерий 80), сброс пароля (81), кнопка «Завершить все сессии». Участвует в подписи
    # сессии, поэтому смена поколения делает недействительными сразу все выданные сессии,
    # включая чужие устройства. Ждать истечения ключа в Redis при этом не нужно.
    session_epoch = models.PositiveIntegerField(default=0)

    objects = MemberManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_session_auth_hash(self) -> str:
        """
        Подпись сессии, зависящая и от пароля, и от поколения.

        Штатная реализация считает подпись только от хеша пароля, поэтому смена почты сессии бы
        не завершила — а 3.1 требует именно этого. Поколение добавлено сюда, чтобы завершение
        сессий работало и без смены пароля.
        """
        return salted_hmac(
            "apps.accounts.models.Member.get_session_auth_hash",
            f"{self.password}{self.session_epoch}",
            algorithm="sha256",
        ).hexdigest()


class Invitation(models.Model):
    """
    Одноразовая ссылка-приглашение — ТЗ 3.

    Q-D16: в базе лежит только хеш токена, открытый текст — ключ `auth:invite:{id}` в Redis со
    сроком жизни приглашения. Потеря Redis не теряет приглашение: участник остаётся видимым как
    непринявший (критерий 31), ссылку администратор выпускает заново.

    TODO(Q-D22): ссылка сброса пароля (3.1) пока не моделируется — решается отдельно, поле вида
    «назначение» добавляется миграцией без переделки.
    """

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="invitations")
    token_hash = models.CharField(max_length=TOKEN_HASH_MAX_LENGTH, unique=True)
    created_at = models.DateTimeField()
    # Срок жизни задаётся при создании: 24 часа, 7 дней или 30 дней (ТЗ 3).
    expires_at = models.DateTimeField()
    # Пока пусто — ссылка не открыта. Открытая ссылка недействительна повторно (критерий 30).
    opened_at = models.DateTimeField(null=True, blank=True, default=None)
    created_by = models.ForeignKey(
        Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_invitations"
    )

    class Meta:
        indexes = [
            models.Index(fields=["member", "opened_at"], name="invitation_member_opened"),
        ]

    def __str__(self) -> str:
        return f"приглашение для {self.member.email}"


class AuditAction(models.TextChoices):
    """
    Действия, которые ТЗ требует записывать в журнал.

    Список закрытый и собран по тексту: всё до `CHECKS_STOPPED` — из 3.1, последнее — из 11.13.
    Русские названия в интерфейсе — ⚠ Q-C11, к схеме отношения не имеют.
    """

    MEMBER_INVITED = "member_invited", "Участник приглашён"
    MEMBER_UPDATED = "member_updated", "Участник изменён"
    EMAIL_CHANGED = "email_changed", "Почта изменена"
    ROLE_CHANGED = "role_changed", "Роль изменена"
    PASSWORD_RESET = "password_reset", "Пароль сброшен"
    SESSIONS_TERMINATED = "sessions_terminated", "Сессии завершены"
    AUTHENTICATOR_UNBOUND = "authenticator_unbound", "Аутентификатор отвязан"
    MEMBER_DEACTIVATED = "member_deactivated", "Участник отключён"
    # Зеркало предыдущего значения, а не новый термин: 3.1 называет отключение обратимым
    # («роль можно вернуть»), и обратный ход обязан попадать в журнал наравне с прямым —
    # иначе доступ вернулся бы без следа. Q-D42.
    MEMBER_ACTIVATED = "member_activated", "Участник включён"
    MEMBER_DELETED = "member_deleted", "Участник удалён"
    CHECKS_STOPPED = "checks_stopped", "Проверки остановлены"


class AuditRecord(models.Model):
    """
    Запись журнала, у которой нет домена — Q-D4.

    В интерфейсе журнал остаётся один: выдача склеивает `AuditRecord` и `Event` в селекторе,
    отдельного термина пользователю не показывается.

    Ссылки на участников обнуляемые, рядом с каждой — снимок имени (Q-D17): удаление
    непринявшего участника не должно лишать историю авторства.
    """

    action = models.CharField(max_length=ENUM_MAX_LENGTH, choices=AuditAction.choices)
    occurred_at = models.DateTimeField()

    # Кто выполнил. У записи об остановке проверок (11.13) автора нет.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_records_authored",
    )
    author_name = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")

    # Кого коснулось. У инфраструктурных записей пусто.
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_records_targeted",
    )
    target_name = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["occurred_at"], name="audit_record_occurred_at"),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} · {self.occurred_at:%Y-%m-%d %H:%M}"
