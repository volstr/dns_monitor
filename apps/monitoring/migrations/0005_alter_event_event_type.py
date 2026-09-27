"""
Названия типов событий приведены к утверждённой таблице Q-C11.

Идентификаторы 7.1 не менялись — это закрытый список, и данные в колонке остаются прежними.
Меняются только подписи, поэтому на уровне базы миграция ничего не делает: она нужна, чтобы
состояние моделей и миграций сошлось.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitoring', '0004_event_notification'),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='event_type',
            field=models.CharField(choices=[('address_appeared', 'Появился адрес'), ('address_gone', 'Адрес пропал'), ('not_allowed', 'Адрес вне разрешённых'), ('back_to_normal', 'Возврат в норму'), ('resolve_failed', 'Домен не резолвится'), ('resolve_recovered', 'Домен снова резолвится'), ('watch_started', 'Наблюдение начато')], max_length=32),
        ),
    ]
