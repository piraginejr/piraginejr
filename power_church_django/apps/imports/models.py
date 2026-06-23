from __future__ import annotations

from django.db import models


class StatementImportPilotLot(models.Model):
    class SourceBackend(models.TextChoices):
        LEGACY_CLONE = "legado_clone", "Legado em clone"
        DJANGO_WEB = "django_web", "Fluxo Django atual"
        POSTGRES_NATIVE = "postgres_nativo", "Fluxo Postgres nativo"

    reference_key = models.CharField("chave de referencia", max_length=255, unique=True)
    source_backend = models.CharField("origem do piloto", max_length=32, choices=SourceBackend.choices, db_index=True)
    source_db_path = models.CharField("caminho do banco fonte", max_length=500, blank=True)
    source_lot_id = models.IntegerField("id do lote no banco fonte", null=True, blank=True, db_index=True)
    bank_name = models.CharField("banco", max_length=120, db_index=True)
    layout_code = models.CharField("layout", max_length=80, db_index=True)
    file_name = models.CharField("nome do arquivo", max_length=255)
    file_hash = models.CharField("hash do arquivo", max_length=80, blank=True, db_index=True)
    period_start = models.DateField("periodo inicial", null=True, blank=True)
    period_end = models.DateField("periodo final", null=True, blank=True)
    movement_count = models.PositiveIntegerField("quantidade de movimentos", default=0)
    total_value = models.DecimalField("total do lote", max_digits=14, decimal_places=2, default=0)
    lot_status = models.CharField("status do lote", max_length=64, blank=True, db_index=True)
    pdf_provider = models.CharField("leitor PDF", max_length=40, blank=True)
    comparison_ok = models.BooleanField("comparacao leitor homologado x portavel", default=False, db_index=True)
    comparison_note = models.TextField("nota de comparacao", blank=True)
    report_path = models.CharField("relatorio associado", max_length=500, blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "lote piloto de extrato"
        verbose_name_plural = "lotes piloto de extrato"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["bank_name", "layout_code", "created_at"]),
            models.Index(fields=["source_backend", "file_hash"]),
        ]

    def __str__(self) -> str:
        return f"{self.bank_name} {self.file_name} ({self.source_backend})"


class StatementImportPilotMovement(models.Model):
    lot = models.ForeignKey(StatementImportPilotLot, on_delete=models.CASCADE, related_name="movements")
    source_movement_id = models.IntegerField("id do movimento no banco fonte", null=True, blank=True, db_index=True)
    page_number = models.PositiveIntegerField("pagina", default=1)
    order_in_lot = models.PositiveIntegerField("ordem no lote", default=0)
    movement_date = models.DateField("data do movimento", null=True, blank=True, db_index=True)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    competence_order = models.IntegerField("ordem da competencia", default=0)
    amount = models.DecimalField("valor", max_digits=14, decimal_places=2, default=0)
    cent_code = models.CharField("codigo de centavos", max_length=8, blank=True, db_index=True)
    movement_kind = models.CharField("tipo do movimento", max_length=80, blank=True, db_index=True)
    receiving_code = models.CharField("codigo de recebimento", max_length=80, blank=True)
    bank_document = models.CharField("documento bancario", max_length=120, blank=True)
    document_type = models.CharField("tipo do documento", max_length=32, blank=True)
    prefix = models.CharField("prefixo do historico", max_length=160, blank=True)
    source_name = models.CharField("nome de origem", max_length=240, blank=True)
    source_name_normalized = models.CharField("nome de origem normalizado", max_length=240, blank=True, db_index=True)
    origin_label = models.CharField("rotulo de origem", max_length=240, blank=True)
    confidence = models.CharField("confianca", max_length=64, blank=True, db_index=True)
    match_score = models.DecimalField("score de associacao", max_digits=8, decimal_places=4, default=0)
    suggested_person_legacy_id = models.IntegerField("pessoa sugerida legado", null=True, blank=True, db_index=True)
    resolved_person_legacy_id = models.IntegerField("pessoa resolvida legado", null=True, blank=True, db_index=True)
    suggested_contributor_legacy_id = models.IntegerField("contribuinte sugerido legado", null=True, blank=True, db_index=True)
    resolved_contributor_legacy_id = models.IntegerField("contribuinte resolvido legado", null=True, blank=True, db_index=True)
    review_status = models.CharField("status de revisao", max_length=64, blank=True, db_index=True)
    review_notes = models.TextField("notas de revisao", blank=True)
    imported_contribution_legacy_id = models.IntegerField("contribuicao importada legado", null=True, blank=True, db_index=True)
    duplicate_movement_legacy_id = models.IntegerField("movimento duplicado legado", null=True, blank=True, db_index=True)
    duplicate_contribution_legacy_id = models.IntegerField("contribuicao duplicada legado", null=True, blank=True, db_index=True)
    duplicate_reason = models.TextField("motivo da duplicidade", blank=True)
    fingerprint = models.CharField("fingerprint", max_length=80, blank=True, db_index=True)
    signature_global = models.CharField("assinatura global", max_length=120, blank=True, db_index=True)
    raw_text = models.TextField("texto bruto", blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "movimento piloto de extrato"
        verbose_name_plural = "movimentos piloto de extrato"
        ordering = ["lot_id", "order_in_lot", "id"]
        indexes = [
            models.Index(fields=["lot", "review_status", "order_in_lot"]),
            models.Index(fields=["lot", "movement_date", "amount"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["lot", "source_movement_id"], name="imports_pilot_movement_source_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id}:{self.order_in_lot} {self.amount}"


class CentRuleSnapshot(models.Model):
    legacy_id = models.IntegerField("id publico", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao", db_index=True)
    cent_code = models.CharField("codigo de centavos", max_length=2, db_index=True)
    destination_name = models.CharField("nome da destinacao", max_length=160)
    contribution_type_legacy_id = models.IntegerField("tipo legado", null=True, blank=True, db_index=True)
    contribution_type_name = models.CharField("tipo", max_length=160, blank=True)
    campaign_legacy_id = models.IntegerField("campanha legada", null=True, blank=True, db_index=True)
    campaign_name = models.CharField("campanha", max_length=160, blank=True)
    account_code = models.CharField("codigo da conta", max_length=64, blank=True)
    account_name = models.CharField("nome da conta", max_length=160, blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "regra nativa de centavos"
        verbose_name_plural = "regras nativas de centavos"
        ordering = ["cent_code", "legacy_id"]
        indexes = [
            models.Index(fields=["organization_id", "cent_code", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.cent_code} · {self.destination_name}"
