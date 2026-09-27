"""
Переименование исхода `no_allowlist` в `not_confirmed` — Q-D37.

Значение прожило меньше суток и только в базе разработчика, но переносить его всё равно нужно:
`history_of_domains` разбирает строку через `CheckOutcome(row["outcome"])`, и оставленное старое
значение уронило бы список доменов на первой же такой строке, а не показало бы её как-нибудь
не так.
"""

from django.db import migrations, models

OLD = "no_allowlist"
NEW = "not_confirmed"


def rename_forward(apps, schema_editor):  # noqa: ANN001, ANN201
    check = apps.get_model("monitoring", "Check")
    check.objects.filter(outcome=OLD).update(outcome=NEW)


def rename_back(apps, schema_editor):  # noqa: ANN001, ANN201
    check = apps.get_model("monitoring", "Check")
    check.objects.filter(outcome=NEW).update(outcome=OLD)


class Migration(migrations.Migration):
    dependencies = [
        ("monitoring", "0006_alter_check_outcome"),
    ]

    operations = [
        migrations.AlterField(
            model_name="check",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("normal", "Норма"),
                    ("address_set_changed", "Набор адресов изменился"),
                    ("has_unmatched", "Есть несовпавший адрес"),
                    ("not_confirmed", "Совпадений нет"),
                    ("resolve_error", "Ошибка резолва"),
                    ("not_performed", "Не состоялась"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(rename_forward, rename_back),
    ]
