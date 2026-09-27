"""
Единственная строка общих настроек — ТЗ 4.1.

Установка без этой строки неработоспособна: от общих настроек зависит разрешение каждого
параметра по цепочке наследования (раздел 5). Поэтому строка создаётся миграцией, а не при
первом обращении к интерфейсу — иначе `checker` на пустой базе падал бы раньше, чем администратор
успел открыть экран настроек.

Ключ задан явно: по ограничению `global_settings_single_row` допустима только строка с pk = 1,
и полагаться на текущее значение последовательности нельзя.
"""

from django.db import migrations

GLOBAL_SETTINGS_PK = 1


def create_row(apps, schema_editor):
    global_settings = apps.get_model("catalog", "GlobalSettings")
    # Значения берутся из умолчаний полей модели: они и есть значения по умолчанию установки.
    global_settings.objects.get_or_create(pk=GLOBAL_SETTINGS_PK)


def delete_row(apps, schema_editor):
    global_settings = apps.get_model("catalog", "GlobalSettings")
    global_settings.objects.filter(pk=GLOBAL_SETTINGS_PK).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_row, delete_row),
    ]
