from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0005_people_detail_snapshot_tables_restore"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonContributionSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("contributor_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte legado")),
                ("received_at", models.DateField(blank=True, db_index=True, null=True, verbose_name="data de recebimento")),
                ("received_at_raw", models.CharField(blank=True, max_length=32, verbose_name="data de recebimento bruta")),
                ("competence", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="competencia")),
                ("competence_order", models.IntegerField(db_index=True, default=0, verbose_name="ordem da competencia")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="valor")),
                ("operational_status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status operacional")),
                ("contribution_type_name", models.CharField(blank=True, max_length=160, verbose_name="tipo")),
                ("receipt_method_name", models.CharField(blank=True, max_length=160, verbose_name="forma")),
                ("source_name", models.CharField(blank=True, max_length=240, verbose_name="origem")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contributions",
                        to="people.personsnapshot",
                    ),
                ),
            ],
            options={
                "verbose_name": "espelho de contribuicao da pessoa",
                "verbose_name_plural": "espelhos de contribuicoes da pessoa",
                "db_table": "people_personcontributionsnapshot",
                "ordering": ["person_id", "-competence_order", "-received_at", "-legacy_id"],
            },
        ),
    ]
