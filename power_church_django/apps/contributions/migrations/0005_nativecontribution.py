from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0004_contributiontypesnapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="NativeContribution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("legacy_id", models.IntegerField(db_index=True, unique=True, verbose_name="id publico")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao")),
                ("person_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="pessoa legada")),
                ("contributor_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte legado")),
                ("contributor_source", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="origem do contribuinte")),
                ("contributor_name", models.CharField(blank=True, max_length=240, verbose_name="nome do contribuinte")),
                ("contributor_document", models.CharField(blank=True, max_length=64, verbose_name="documento do contribuinte")),
                ("contributor_type", models.CharField(blank=True, max_length=64, verbose_name="tipo do contribuinte")),
                ("native_aux_contributor_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte auxiliar nativo")),
                ("received_at", models.DateField(blank=True, db_index=True, null=True, verbose_name="data de recebimento")),
                ("received_at_raw", models.CharField(blank=True, max_length=32, verbose_name="data de recebimento bruta")),
                ("competence", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="competencia")),
                ("competence_order", models.IntegerField(db_index=True, default=0, verbose_name="ordem da competencia")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="valor")),
                ("contribution_type_legacy_id", models.IntegerField(db_index=True, verbose_name="tipo legado")),
                ("contribution_type_name", models.CharField(blank=True, max_length=160, verbose_name="tipo")),
                ("campaign_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="campanha legada")),
                ("campaign_name", models.CharField(blank=True, max_length=160, verbose_name="campanha")),
                ("receipt_method_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="forma legada")),
                ("receipt_method_name", models.CharField(blank=True, max_length=160, verbose_name="forma")),
                ("operational_status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status operacional")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("statement_movement_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="movimento de extrato legado")),
                ("pix_movement_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="movimento PIX legado")),
                ("split_parent_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuicao origem do rateio")),
                ("source", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="origem")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("created_by", models.CharField(blank=True, max_length=160, verbose_name="criado por")),
                ("updated_by", models.CharField(blank=True, max_length=160, verbose_name="atualizado por")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "contribuicao nativa",
                "verbose_name_plural": "contribuicoes nativas",
                "ordering": ["-competence_order", "-received_at", "-legacy_id"],
                "indexes": [
                    models.Index(fields=["organization_id", "person_legacy_id", "is_active"], name="contributio_organiz_9c512a_idx"),
                    models.Index(fields=["organization_id", "operational_status", "competence"], name="contributio_organiz_8e48d1_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NativeAuxContributor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organization_id", models.IntegerField(db_index=True, verbose_name="organizacao")),
                ("legacy_reference_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte legado de origem")),
                ("person_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="pessoa legada vinculada")),
                ("name", models.CharField(max_length=240, verbose_name="nome")),
                ("normalized_name", models.CharField(db_index=True, max_length=240, verbose_name="nome normalizado")),
                ("primary_document", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="documento principal")),
                ("document_type", models.CharField(blank=True, max_length=32, verbose_name="tipo do documento")),
                ("contributor_type", models.CharField(blank=True, max_length=64, verbose_name="tipo")),
                ("origin", models.CharField(blank=True, max_length=120, verbose_name="origem")),
                ("quality", models.CharField(blank=True, max_length=120, verbose_name="qualidade")),
                ("status", models.CharField(blank=True, max_length=64, verbose_name="status")),
                ("notes", models.TextField(blank=True, verbose_name="observacoes")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "contribuinte auxiliar nativo",
                "verbose_name_plural": "contribuintes auxiliares nativos",
                "ordering": ["name", "id"],
                "indexes": [
                    models.Index(fields=["organization_id", "is_active", "name"], name="contributio_organiz_4f7264_idx"),
                    models.Index(fields=["organization_id", "primary_document"], name="contributio_organiz_6960c7_idx"),
                ],
            },
        ),
    ]
