"""
Порог актуальности по умолчанию — 14 суток вместо суток (Q-D47).

Смены умолчания мало: строка общих настроек на работающей установке уже создана, и новое
значение до неё не дойдёт. Поэтому здесь ещё и перенос данных.

Переносится **только нетронутое** значение. Если администратор когда-то сам выставил порог, он
сделал это осознанно, и менять его решение миграцией нельзя — даже если прежнее умолчание
совпало с тем, что он выбрал. Различить эти два случая после факта невозможно, и выбор сделан
в пользу меньшего вреда: администратор, специально оставивший сутки, увидит 14 суток и вернёт
своё; администратор, которому молча удвоили бы срок закрытия инцидентов, ничего бы не заметил.
"""

from django.db import migrations, models

WAS = 24 * 60 * 60
NOW = 14 * 24 * 60 * 60


def raise_untouched(apps, schema_editor):
    GlobalSettings = apps.get_model("catalog", "GlobalSettings")
    GlobalSettings.objects.filter(freshness_threshold_seconds=WAS).update(
        freshness_threshold_seconds=NOW
    )


def lower_back(apps, schema_editor):
    GlobalSettings = apps.get_model("catalog", "GlobalSettings")
    GlobalSettings.objects.filter(freshness_threshold_seconds=NOW).update(
        freshness_threshold_seconds=WAS
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0007_globalsettings_last_purge_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="globalsettings",
            name="freshness_threshold_seconds",
            field=models.PositiveIntegerField(default=NOW),
        ),
        migrations.RunPython(raise_untouched, lower_back),
    ]
