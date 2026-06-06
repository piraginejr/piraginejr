from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="StatementImportPilotLot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference_key", models.CharField(max_length=255, unique=True, verbose_name="chave de referencia")),
                (
                    "source_backend",
                    models.CharField(
                        choices=[
                            ("legado_clone", "Legado em clone"),
                            ("django_web", "Fluxo Django atual"),
                            ("postgres_nativo", "Fluxo Postgres nativo"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="origem do piloto",
                    ),
                ),
                ("source_db_path", models.CharField(blank=True, max_length=500, verbose_name="caminho do banco fonte")),
                ("source_lot_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="id do lote no banco fonte")),
                ("bank_name", models.CharField(db_index=True, max_length=120, verbose_name="banco")),
                ("layout_code", models.CharField(db_index=True, max_length=80, verbose_name="layout")),
                ("file_name", models.CharField(max_length=255, verbose_name="nome do arquivo")),
                ("file_hash", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="hash do arquivo")),
                ("period_start", models.DateField(blank=True, null=True, verbose_name="periodo inicial")),
                ("period_end", models.DateField(blank=True, null=True, verbose_name="periodo final")),
                ("movement_count", models.PositiveIntegerField(default=0, verbose_name="quantidade de movimentos")),
                ("total_value", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="total do lote")),
                ("lot_status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status do lote")),
                ("pdf_provider", models.CharField(blank=True, max_length=40, verbose_name="leitor PDF")),
                ("comparison_ok", models.BooleanField(db_index=True, default=False, verbose_name="comparacao leitor homologado x portavel")),
                ("comparison_note", models.TextField(blank=True, verbose_name="nota de comparacao")),
                ("report_path", models.CharField(blank=True, max_length=500, verbose_name="relatorio associado")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="metadados")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "lote piloto de extrato",
                "verbose_name_plural": "lotes piloto de extrato",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="StatementImportPilotMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_movement_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="id do movimento no banco fonte")),
                ("page_number", models.PositiveIntegerField(default=1, verbose_name="pagina")),
                ("order_in_lot", models.PositiveIntegerField(default=0, verbose_name="ordem no lote")),
                ("movement_date", models.DateField(blank=True, db_index=True, null=True, verbose_name="data do movimento")),
                ("competence", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="competencia")),
                ("competence_order", models.IntegerField(default=0, verbose_name="ordem da competencia")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="valor")),
                ("cent_code", models.CharField(blank=True, db_index=True, max_length=8, verbose_name="codigo de centavos")),
                ("movement_kind", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="tipo do movimento")),
                ("receiving_code", models.CharField(blank=True, max_length=80, verbose_name="codigo de recebimento")),
                ("bank_document", models.CharField(blank=True, max_length=120, verbose_name="documento bancario")),
                ("document_type", models.CharField(blank=True, max_length=32, verbose_name="tipo do documento")),
                ("prefix", models.CharField(blank=True, max_length=160, verbose_name="prefixo do historico")),
                ("source_name", models.CharField(blank=True, max_length=240, verbose_name="nome de origem")),
                ("source_name_normalized", models.CharField(blank=True, db_index=True, max_length=240, verbose_name="nome de origem normalizado")),
                ("origin_label", models.CharField(blank=True, max_length=240, verbose_name="rotulo de origem")),
                ("confidence", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="confianca")),
                ("match_score", models.DecimalField(decimal_places=4, default=0, max_digits=8, verbose_name="score de associacao")),
                ("suggested_person_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="pessoa sugerida legado")),
                ("resolved_person_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="pessoa resolvida legado")),
                ("suggested_contributor_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte sugerido legado")),
                ("resolved_contributor_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuinte resolvido legado")),
                ("review_status", models.CharField(blank=True, db_index=True, max_length=64, verbose_name="status de revisao")),
                ("review_notes", models.TextField(blank=True, verbose_name="notas de revisao")),
                ("imported_contribution_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuicao importada legado")),
                ("duplicate_movement_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="movimento duplicado legado")),
                ("duplicate_contribution_legacy_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="contribuicao duplicada legado")),
                ("duplicate_reason", models.TextField(blank=True, verbose_name="motivo da duplicidade")),
                ("fingerprint", models.CharField(blank=True, db_index=True, max_length=80, verbose_name="fingerprint")),
                ("signature_global", models.CharField(blank=True, db_index=True, max_length=120, verbose_name="assinatura global")),
                ("raw_text", models.TextField(blank=True, verbose_name="texto bruto")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="metadados")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="movements",
                        to="imports.statementimportpilotlot",
                    ),
                ),
            ],
            options={
                "verbose_name": "movimento piloto de extrato",
                "verbose_name_plural": "movimentos piloto de extrato",
                "ordering": ["lot_id", "order_in_lot", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="statementimportpilotlot",
            index=models.Index(fields=["bank_name", "layout_code", "created_at"], name="imports_pilot_bank_layout_created_idx"),
        ),
        migrations.AddIndex(
            model_name="statementimportpilotlot",
            index=models.Index(fields=["source_backend", "file_hash"], name="imports_pilot_source_hash_idx"),
        ),
        migrations.AddIndex(
            model_name="statementimportpilotmovement",
            index=models.Index(fields=["lot", "review_status", "order_in_lot"], name="imports_pilot_lot_review_order_idx"),
        ),
        migrations.AddIndex(
            model_name="statementimportpilotmovement",
            index=models.Index(fields=["lot", "movement_date", "amount"], name="imports_pilot_lot_date_amount_idx"),
        ),
        migrations.AddConstraint(
            model_name="statementimportpilotmovement",
            constraint=models.UniqueConstraint(fields=("lot", "source_movement_id"), name="imports_pilot_movement_source_unique"),
        ),
    ]
