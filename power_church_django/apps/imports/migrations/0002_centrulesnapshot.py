from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CentRuleSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id publico")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao")),
                ("cent_code", models.CharField(db_index=True, max_length=2, verbose_name="codigo de centavos")),
                ("destination_name", models.CharField(max_length=160, verbose_name="nome da destinacao")),
                ("contribution_type_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="tipo legado")),
                ("contribution_type_name", models.CharField(blank=True, max_length=160, verbose_name="tipo")),
                ("campaign_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="campanha legada")),
                ("campaign_name", models.CharField(blank=True, max_length=160, verbose_name="campanha")),
                ("account_code", models.CharField(blank=True, max_length=64, verbose_name="codigo da conta")),
                ("account_name", models.CharField(blank=True, max_length=160, verbose_name="nome da conta")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "regra nativa de centavos",
                "verbose_name_plural": "regras nativas de centavos",
                "ordering": ["cent_code", "legacy_id"],
            },
        ),
        migrations.AddIndex(
            model_name="centrulesnapshot",
            index=models.Index(fields=["organization_id", "cent_code", "is_active"], name="imports_cen_organiz_3d7c94_idx"),
        ),
    ]
