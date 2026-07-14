from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0009_restore_people_snapshot_table_names"),
    ]

    operations = [
        migrations.CreateModel(
            name="FinancialIdentityLookup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao legado")),
                ("lookup_kind", models.CharField(db_index=True, max_length=64, verbose_name="tipo de consulta")),
                ("value", models.CharField(max_length=240, verbose_name="valor original")),
                ("normalized_value", models.CharField(db_index=True, max_length=240, verbose_name="valor normalizado")),
                ("source", models.CharField(blank=True, max_length=120, verbose_name="origem")),
                ("priority", models.IntegerField(db_index=True, default=0, verbose_name="prioridade")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="financial_identity_lookups",
                        to="people.personsnapshot",
                    ),
                ),
            ],
            options={
                "verbose_name": "consulta de identidade financeira",
                "verbose_name_plural": "consultas de identidade financeira",
                "db_table": "people_financialidentitylookup",
                "ordering": ["person_id", "-priority", "lookup_kind", "normalized_value", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="financialidentitylookup",
            index=models.Index(fields=["organization_id", "is_active", "normalized_value"], name="people_fina_organiz_470a27_idx"),
        ),
        migrations.AddIndex(
            model_name="financialidentitylookup",
            index=models.Index(fields=["person", "is_active", "lookup_kind"], name="people_fina_person__64666d_idx"),
        ),
        migrations.AddConstraint(
            model_name="financialidentitylookup",
            constraint=models.UniqueConstraint(
                fields=("person", "lookup_kind", "normalized_value", "source"),
                name="people_financialidentitylookup_unique",
            ),
        ),
    ]
