from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0007_personsecuretrashsnapshot_personsecurepurgesnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="NativePeopleImportLot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("import_type", models.CharField(db_index=True, max_length=80, verbose_name="tipo")),
                ("file_name", models.CharField(blank=True, max_length=260, verbose_name="arquivo")),
                ("file_hash", models.CharField(blank=True, max_length=128, verbose_name="hash do arquivo")),
                ("status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status")),
                ("total_lines", models.IntegerField(default=0, verbose_name="linhas totais")),
                ("imported_lines", models.IntegerField(default=0, verbose_name="linhas importadas")),
                ("ignored_lines", models.IntegerField(default=0, verbose_name="linhas ignoradas")),
                ("error_lines", models.IntegerField(default=0, verbose_name="linhas com erro")),
                ("open_pendencies", models.IntegerField(default=0, verbose_name="pendencias abertas")),
                ("active_people", models.IntegerField(default=0, verbose_name="pessoas ativas")),
                ("without_name", models.IntegerField(default=0, verbose_name="fichas sem nome")),
                ("review_mappings", models.IntegerField(default=0, verbose_name="campos para revisar")),
                ("created_at_display", models.CharField(blank=True, max_length=80, verbose_name="criado em exibicao")),
                ("confirmed_at_display", models.CharField(blank=True, max_length=80, verbose_name="confirmado em exibicao")),
                ("status_rows_json", models.JSONField(blank=True, default=list, verbose_name="resumo por status")),
                ("mapping_rows_json", models.JSONField(blank=True, default=list, verbose_name="mapeamentos")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
            ],
            options={
                "verbose_name": "lote nativo de importacao de pessoas",
                "verbose_name_plural": "lotes nativos de importacao de pessoas",
                "ordering": ["-legacy_id"],
                "db_table": "people_nativepeopleimportlot",
            },
        ),
        migrations.CreateModel(
            name="NativePeopleImportLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("line_number", models.IntegerField(db_index=True, default=0, verbose_name="numero da linha")),
                ("status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status")),
                ("original_name", models.CharField(blank=True, max_length=240, verbose_name="nome original")),
                ("normalized_action", models.CharField(blank=True, max_length=160, verbose_name="acao normalizada")),
                ("person_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="pessoa legada")),
                ("person_name", models.CharField(blank=True, max_length=240, verbose_name="nome da ficha")),
                ("person_cpf", models.CharField(blank=True, max_length=32, verbose_name="cpf da ficha")),
                ("person_status", models.CharField(blank=True, max_length=64, verbose_name="status da ficha")),
                ("person_active", models.BooleanField(db_index=True, default=False, verbose_name="ficha ativa")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("lot", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="lines", to="people.nativepeopleimportlot")),
            ],
            options={
                "verbose_name": "linha nativa de importacao de pessoas",
                "verbose_name_plural": "linhas nativas de importacao de pessoas",
                "ordering": ["line_number", "legacy_id"],
                "db_table": "people_nativepeopleimportline",
            },
        ),
        migrations.CreateModel(
            name="NativePeopleImportPending",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id legado")),
                ("line_number", models.IntegerField(db_index=True, default=0, verbose_name="numero da linha")),
                ("severity", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="severidade")),
                ("issue_type", models.CharField(blank=True, db_index=True, max_length=120, verbose_name="tipo")),
                ("description", models.TextField(blank=True, verbose_name="descricao")),
                ("suggested_action", models.TextField(blank=True, verbose_name="acao sugerida")),
                ("resolved", models.BooleanField(db_index=True, default=False, verbose_name="resolvido")),
                ("person_name", models.CharField(blank=True, max_length=240, verbose_name="nome da pessoa")),
                ("synced_at", models.DateTimeField(auto_now=True, verbose_name="sincronizado em")),
                ("lot", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="pendings", to="people.nativepeopleimportlot")),
            ],
            options={
                "verbose_name": "pendencia nativa de importacao de pessoas",
                "verbose_name_plural": "pendencias nativas de importacao de pessoas",
                "ordering": ["resolved", "-severity", "line_number", "legacy_id"],
                "db_table": "people_nativepeopleimportpending",
            },
        ),
    ]
