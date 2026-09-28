"""
Связь «событие → уведомление» становится многие-ко-многим — Q-D11, вариант 1.

Порядок операций здесь важнее их состава. Автогенерация предложила сначала удалить внешний
ключ, потом создать связь: это потеряло бы все существующие связи событий с уведомлениями, то
есть колонку «Результат доставки» в журнале за всю историю. Поэтому: сначала новая связь, потом
перенос данных, и только затем удаление старого поля.

Обратный перенос тоже написан — иначе откат миграции молча оставил бы события без уведомлений.
Он берёт **первое** уведомление события: у одиночного поля другого выхода нет, и это честная
потеря, о которой лучше знать заранее.
"""

from django.db import migrations, models


def copy_forward(apps, schema_editor):
    Event = apps.get_model("monitoring", "Event")
    through = Event.notifications.through
    through.objects.bulk_create(
        [
            through(event_id=event_id, notification_id=notification_id)
            for event_id, notification_id in Event.objects.filter(
                notification__isnull=False
            ).values_list("pk", "notification_id")
        ],
        batch_size=1000,
    )


def copy_back(apps, schema_editor):
    Event = apps.get_model("monitoring", "Event")
    through = Event.notifications.through
    seen: set[int] = set()
    for event_id, notification_id in through.objects.order_by("pk").values_list(
        "event_id", "notification_id"
    ):
        if event_id in seen:
            continue
        seen.add(event_id)
        Event.objects.filter(pk=event_id).update(notification_id=notification_id)


class Migration(migrations.Migration):
    dependencies = [
        ("monitoring", "0007_alter_check_outcome"),
        ("notifications", "0002_notification_merge_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="notifications",
            field=models.ManyToManyField(
                blank=True, related_name="events", to="notifications.notification"
            ),
        ),
        migrations.RunPython(copy_forward, copy_back),
        migrations.RemoveField(
            model_name="event",
            name="notification",
        ),
    ]
