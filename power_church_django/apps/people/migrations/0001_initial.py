from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="HouseholdProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("signature", models.CharField(max_length=500, unique=True, verbose_name="assinatura do nucleo")),
                ("head_person_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="cabeca da familia")),
                ("display_name_override", models.CharField(blank=True, max_length=240, verbose_name="nome de guerra")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "perfil de familia domiciliar",
                "verbose_name_plural": "perfis de familias domiciliares",
                "ordering": ["signature"],
            },
        ),
    ]
