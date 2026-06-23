from __future__ import annotations

from django.db import models


class ReceiptEmailTemplate(models.Model):
    key = models.CharField("chave", max_length=60, unique=True)
    name = models.CharField("nome", max_length=120)
    subject_template = models.CharField("assunto padrao", max_length=255)
    body_template = models.TextField("mensagem padrao")
    default_from_email = models.CharField("remetente padrao", max_length=254, blank=True)
    reply_to_email = models.CharField("responder para", max_length=254, blank=True)
    active = models.BooleanField("ativo", default=True, db_index=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "modelo de e-mail de recibo"
        verbose_name_plural = "modelos de e-mail de recibo"
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class ReceiptDispatch(models.Model):
    class Mode(models.TextChoices):
        COMPETENCE = "competencia", "Competencia"
        DATE_RANGE = "intervalo", "Intervalo"

    class Trigger(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTOMATIC = "automatico", "Automatico"
        RETROACTIVE = "retroativo", "Retroativo"

    class Status(models.TextChoices):
        PENDING = "pendente", "Pendente"
        SENT = "enviado", "Enviado"
        FAILED = "falhou", "Falhou"
        CANCELLED = "cancelado", "Cancelado"

    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)
    organization_id = models.IntegerField("organizacao", null=True, blank=True, db_index=True)
    legacy_person_id = models.IntegerField("pessoa legada", db_index=True)
    legacy_receipt_id = models.IntegerField("recibo legado", null=True, blank=True, db_index=True)
    legacy_receipt_number = models.CharField("numero do recibo", max_length=80, blank=True)
    person_name = models.CharField("nome da pessoa", max_length=240, blank=True)
    person_email = models.CharField("e-mail da pessoa", max_length=254, blank=True)
    competence = models.CharField("competencia", max_length=40, blank=True, db_index=True)
    period_label = models.CharField("rotulo do periodo", max_length=140, blank=True)
    period_start = models.DateField("inicio do periodo", null=True, blank=True)
    period_end = models.DateField("fim do periodo", null=True, blank=True)
    mode = models.CharField("modo", max_length=20, choices=Mode.choices, default=Mode.COMPETENCE, db_index=True)
    trigger = models.CharField("origem", max_length=20, choices=Trigger.choices, default=Trigger.MANUAL, db_index=True)
    status = models.CharField("status", max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    auto_created = models.BooleanField("criado automaticamente", default=False, db_index=True)
    email_to = models.CharField("destinatario", max_length=254, blank=True)
    email_subject = models.CharField("assunto", max_length=255, blank=True)
    email_body = models.TextField("corpo do e-mail", blank=True)
    pdf_filename = models.CharField("nome do pdf", max_length=255, blank=True)
    send_attempts = models.PositiveIntegerField("tentativas", default=0)
    last_attempt_at = models.DateTimeField("ultima tentativa", null=True, blank=True)
    sent_at = models.DateTimeField("enviado em", null=True, blank=True)
    last_error = models.TextField("ultimo erro", blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)

    class Meta:
        verbose_name = "fila de envio de recibo"
        verbose_name_plural = "fila de envio de recibos"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["legacy_person_id", "competence", "status"]),
            models.Index(fields=["trigger", "auto_created", "created_at"]),
        ]

    def __str__(self) -> str:
        label = self.legacy_receipt_number or self.period_label or f"pessoa #{self.legacy_person_id}"
        return f"{label} -> {self.email_to or self.person_email or 'sem e-mail'}"


class ReceiptSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada", db_index=True)
    receipt_number = models.CharField("numero", max_length=80, blank=True)
    status = models.CharField("status", max_length=40, blank=True, db_index=True)
    organization_name = models.CharField("organizacao", max_length=240, blank=True)
    person_name = models.CharField("nome da pessoa", max_length=240, blank=True)
    person_code = models.CharField("codigo da pessoa", max_length=80, blank=True)
    person_cpf = models.CharField("cpf", max_length=32, blank=True)
    person_email = models.CharField("e-mail", max_length=254, blank=True, db_index=True)
    person_phone = models.CharField("telefone", max_length=64, blank=True)
    emission_date = models.DateField("data de emissao", null=True, blank=True, db_index=True)
    emission_date_raw = models.CharField("data de emissao bruta", max_length=32, blank=True)
    period_start = models.DateField("inicio do periodo", null=True, blank=True)
    period_start_raw = models.CharField("inicio do periodo bruto", max_length=32, blank=True)
    period_end = models.DateField("fim do periodo", null=True, blank=True)
    period_end_raw = models.CharField("fim do periodo bruto", max_length=32, blank=True)
    total_value = models.DecimalField("valor total", max_digits=14, decimal_places=2, default=0)
    notes = models.TextField("observacoes", blank=True)
    is_cancelled = models.BooleanField("cancelado", default=False, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de recibo"
        verbose_name_plural = "espelhos de recibos"
        ordering = ["-emission_date", "-legacy_id"]
        indexes = [
            models.Index(fields=["person_legacy_id", "status", "emission_date"]),
            models.Index(fields=["person_legacy_id", "is_cancelled"]),
        ]

    def __str__(self) -> str:
        return self.receipt_number or f"recibo #{self.legacy_id}"


class ReceiptItemSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    receipt = models.ForeignKey(ReceiptSnapshot, on_delete=models.CASCADE, related_name="items")
    contribution_legacy_id = models.IntegerField("contribuicao legada", db_index=True)
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    received_at = models.DateField("data recebimento", null=True, blank=True, db_index=True)
    received_at_raw = models.CharField("data recebimento bruta", max_length=32, blank=True)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    contribution_type_name = models.CharField("tipo", max_length=160, blank=True)
    receipt_method_name = models.CharField("forma", max_length=160, blank=True)
    notes = models.TextField("observacoes", blank=True)
    amount = models.DecimalField("valor", max_digits=14, decimal_places=2, default=0)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de item de recibo"
        verbose_name_plural = "espelhos de itens de recibo"
        ordering = ["receipt_id", "received_at", "legacy_id"]
        indexes = [
            models.Index(fields=["receipt", "competence", "received_at"]),
            models.Index(fields=["contribution_legacy_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.receipt_id}:{self.contribution_legacy_id}"


class ContributionTypeSnapshot(models.Model):
    legacy_id = models.IntegerField("id legado", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao legado", db_index=True)
    code = models.CharField("codigo", max_length=80, blank=True, db_index=True)
    name = models.CharField("nome", max_length=160)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    synced_at = models.DateTimeField("sincronizado em", auto_now=True)

    class Meta:
        verbose_name = "espelho de tipo de contribuicao"
        verbose_name_plural = "espelhos de tipos de contribuicao"
        ordering = ["organization_id", "name", "legacy_id"]
        indexes = [
            models.Index(fields=["organization_id", "is_active", "name"]),
        ]

    def __str__(self) -> str:
        return self.name


class NativeContribution(models.Model):
    legacy_id = models.IntegerField("id publico", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao", db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada", null=True, blank=True, db_index=True)
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    contributor_source = models.CharField("origem do contribuinte", max_length=32, blank=True, db_index=True)
    contributor_name = models.CharField("nome do contribuinte", max_length=240, blank=True)
    contributor_document = models.CharField("documento do contribuinte", max_length=64, blank=True)
    contributor_type = models.CharField("tipo do contribuinte", max_length=64, blank=True)
    native_aux_contributor_id = models.IntegerField("contribuinte auxiliar nativo", null=True, blank=True, db_index=True)
    received_at = models.DateField("data de recebimento", null=True, blank=True, db_index=True)
    received_at_raw = models.CharField("data de recebimento bruta", max_length=32, blank=True)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    competence_order = models.IntegerField("ordem da competencia", default=0, db_index=True)
    amount = models.DecimalField("valor", max_digits=14, decimal_places=2, default=0)
    contribution_type_legacy_id = models.IntegerField("tipo legado", db_index=True)
    contribution_type_name = models.CharField("tipo", max_length=160, blank=True)
    campaign_legacy_id = models.IntegerField("campanha legada", null=True, blank=True, db_index=True)
    campaign_name = models.CharField("campanha", max_length=160, blank=True)
    receipt_method_legacy_id = models.IntegerField("forma legada", null=True, blank=True, db_index=True)
    receipt_method_name = models.CharField("forma", max_length=160, blank=True)
    operational_status = models.CharField("status operacional", max_length=64, blank=True, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    statement_movement_legacy_id = models.IntegerField("movimento de extrato legado", null=True, blank=True, db_index=True)
    pix_movement_legacy_id = models.IntegerField("movimento PIX legado", null=True, blank=True, db_index=True)
    split_parent_legacy_id = models.IntegerField("contribuicao origem do rateio", null=True, blank=True, db_index=True)
    source = models.CharField("origem", max_length=80, blank=True, db_index=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_by = models.CharField("criado por", max_length=160, blank=True)
    updated_by = models.CharField("atualizado por", max_length=160, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "contribuicao nativa"
        verbose_name_plural = "contribuicoes nativas"
        ordering = ["-competence_order", "-received_at", "-legacy_id"]
        indexes = [
            models.Index(fields=["organization_id", "person_legacy_id", "is_active"]),
            models.Index(fields=["organization_id", "operational_status", "competence"]),
        ]

    def __str__(self) -> str:
        return f"{self.legacy_id}:{self.amount}"


class NativeAuxContributor(models.Model):
    organization_id = models.IntegerField("organizacao", db_index=True)
    legacy_reference_id = models.IntegerField("contribuinte legado de origem", null=True, blank=True, db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada vinculada", null=True, blank=True, db_index=True)
    name = models.CharField("nome", max_length=240)
    normalized_name = models.CharField("nome normalizado", max_length=240, db_index=True)
    primary_document = models.CharField("documento principal", max_length=64, blank=True, db_index=True)
    document_type = models.CharField("tipo do documento", max_length=32, blank=True)
    contributor_type = models.CharField("tipo", max_length=64, blank=True)
    origin = models.CharField("origem", max_length=120, blank=True)
    quality = models.CharField("qualidade", max_length=120, blank=True)
    status = models.CharField("status", max_length=64, blank=True)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "contribuinte auxiliar nativo"
        verbose_name_plural = "contribuintes auxiliares nativos"
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["organization_id", "is_active", "name"]),
            models.Index(fields=["organization_id", "primary_document"]),
        ]

    def __str__(self) -> str:
        return self.name


class NativeEnvelope(models.Model):
    legacy_id = models.IntegerField("id publico", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao", db_index=True)
    native_lot_legacy_id = models.IntegerField("lote nativo", null=True, blank=True, db_index=True)
    lot_name = models.CharField("nome do lote", max_length=180, blank=True)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    competence_order = models.IntegerField("ordem da competencia", default=0, db_index=True)
    received_at = models.DateField("data de recebimento", null=True, blank=True, db_index=True)
    received_at_raw = models.CharField("data de recebimento bruta", max_length=32, blank=True)
    total_informed = models.DecimalField("total informado", max_digits=14, decimal_places=2, default=0)
    total_lines = models.DecimalField("total das linhas", max_digits=14, decimal_places=2, default=0)
    informed_name = models.CharField("nome informado", max_length=240, blank=True)
    informed_phone = models.CharField("telefone informado", max_length=64, blank=True)
    informed_address = models.CharField("endereco informado", max_length=255, blank=True)
    person_legacy_id = models.IntegerField("pessoa legada", null=True, blank=True, db_index=True)
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    native_aux_contributor_id = models.IntegerField("contribuinte auxiliar nativo", null=True, blank=True, db_index=True)
    receipt_method_legacy_id = models.IntegerField("forma legado", null=True, blank=True, db_index=True)
    receipt_method_name = models.CharField("forma", max_length=160, blank=True)
    operational_status = models.CharField("status operacional", max_length=64, blank=True, db_index=True)
    source = models.CharField("origem operacional", max_length=160, blank=True)
    status = models.CharField("status", max_length=40, blank=True, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    justification = models.TextField("justificativa", blank=True)
    image_original_name = models.CharField("nome original da imagem", max_length=255, blank=True)
    image_hash = models.CharField("hash da imagem", max_length=120, blank=True, db_index=True)
    image_content_type = models.CharField("content type da imagem", max_length=120, blank=True)
    image_size = models.IntegerField("tamanho da imagem", default=0)
    image_path = models.CharField("caminho da imagem", max_length=500, blank=True)
    traceability_form = models.CharField("forma identificada", max_length=64, blank=True)
    traceability_provider = models.CharField("banco operadora", max_length=120, blank=True)
    traceability_check_number = models.CharField("numero do cheque", max_length=64, blank=True)
    traceability_operation_number = models.CharField("numero da operacao", max_length=64, blank=True)
    traceability_nsu_tid = models.CharField("nsu tid", max_length=64, blank=True)
    traceability_card_suffix = models.CharField("final do cartao", max_length=32, blank=True)
    traceability_operation_date = models.DateField("data da operacao", null=True, blank=True)
    traceability_operation_date_raw = models.CharField("data da operacao bruta", max_length=32, blank=True)
    traceability_operation_amount = models.DecimalField("valor da operacao", max_digits=14, decimal_places=2, null=True, blank=True)
    traceability_status = models.CharField("status de conciliacao", max_length=64, blank=True)
    traceability_notes = models.TextField("observacoes de conciliacao", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_by = models.CharField("criado por", max_length=160, blank=True)
    updated_by = models.CharField("atualizado por", max_length=160, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "envelope nativo"
        verbose_name_plural = "envelopes nativos"
        ordering = ["-competence_order", "-received_at", "-legacy_id"]
        indexes = [
            models.Index(fields=["organization_id", "competence", "status"]),
            models.Index(fields=["organization_id", "person_legacy_id", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.legacy_id}:{self.total_informed}"


class NativeEnvelopeLot(models.Model):
    legacy_id = models.IntegerField("id publico", unique=True, db_index=True)
    organization_id = models.IntegerField("organizacao", db_index=True)
    name = models.CharField("nome", max_length=180)
    competence = models.CharField("competencia", max_length=32, blank=True, db_index=True)
    competence_order = models.IntegerField("ordem da competencia", default=0, db_index=True)
    default_received_at = models.DateField("data padrao", null=True, blank=True)
    default_received_at_raw = models.CharField("data padrao bruta", max_length=32, blank=True)
    default_source = models.CharField("origem operacional padrao", max_length=160, blank=True)
    default_contribution_type_legacy_id = models.IntegerField("tipo legado padrao", null=True, blank=True, db_index=True)
    default_campaign_legacy_id = models.IntegerField("campanha legada padrao", null=True, blank=True, db_index=True)
    default_receipt_method_legacy_id = models.IntegerField("forma legada padrao", null=True, blank=True, db_index=True)
    folder_path = models.CharField("pasta", max_length=500, blank=True)
    notes = models.TextField("observacoes", blank=True)
    status = models.CharField("status", max_length=40, blank=True, db_index=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_by = models.CharField("criado por", max_length=160, blank=True)
    updated_by = models.CharField("atualizado por", max_length=160, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "lote nativo de envelope"
        verbose_name_plural = "lotes nativos de envelope"
        ordering = ["-competence_order", "-legacy_id"]
        indexes = [
            models.Index(fields=["organization_id", "competence", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class NativeEnvelopeItem(models.Model):
    legacy_id = models.IntegerField("id publico", unique=True, db_index=True)
    envelope = models.ForeignKey(NativeEnvelope, on_delete=models.CASCADE, related_name="items")
    person_legacy_id = models.IntegerField("pessoa legada", null=True, blank=True, db_index=True)
    contributor_legacy_id = models.IntegerField("contribuinte legado", null=True, blank=True, db_index=True)
    native_aux_contributor_id = models.IntegerField("contribuinte auxiliar nativo", null=True, blank=True, db_index=True)
    contributor_name = models.CharField("nome do contribuinte", max_length=240, blank=True)
    contributor_document = models.CharField("documento do contribuinte", max_length=64, blank=True)
    contribution_legacy_id = models.IntegerField("contribuicao legada", null=True, blank=True, db_index=True)
    contribution_type_legacy_id = models.IntegerField("tipo legado", db_index=True)
    contribution_type_name = models.CharField("tipo", max_length=160, blank=True)
    campaign_legacy_id = models.IntegerField("campanha legada", null=True, blank=True, db_index=True)
    campaign_name = models.CharField("campanha", max_length=160, blank=True)
    amount = models.DecimalField("valor", max_digits=14, decimal_places=2, default=0)
    notes = models.TextField("observacoes", blank=True)
    is_active = models.BooleanField("ativo", default=True, db_index=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "item de envelope nativo"
        verbose_name_plural = "itens de envelope nativo"
        ordering = ["envelope_id", "legacy_id"]
        indexes = [
            models.Index(fields=["envelope", "is_active"]),
            models.Index(fields=["contribution_legacy_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.envelope_id}:{self.amount}"


class NativeEnvelopeProfileUpdate(models.Model):
    class Status(models.TextChoices):
        PENDING = "pendente", "Pendente"
        APPLIED = "aplicado", "Aplicado"
        IGNORED = "ignorado", "Ignorado"

    envelope = models.ForeignKey(NativeEnvelope, on_delete=models.CASCADE, related_name="profile_updates")
    organization_id = models.IntegerField("organizacao", db_index=True)
    person_legacy_id = models.IntegerField("pessoa legada", db_index=True)
    field_name = models.CharField("campo", max_length=64, db_index=True)
    current_value = models.TextField("valor da ficha", blank=True)
    envelope_value = models.TextField("valor do envelope", blank=True)
    status = models.CharField("status", max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    notes = models.TextField("observacoes", blank=True)
    created_by = models.CharField("criado por", max_length=160, blank=True)
    updated_by = models.CharField("atualizado por", max_length=160, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "pendencia cadastral de envelope"
        verbose_name_plural = "pendencias cadastrais de envelope"
        ordering = ["envelope_id", "status", "field_name", "id"]
        indexes = [
            models.Index(fields=["envelope", "status"]),
            models.Index(fields=["person_legacy_id", "status", "field_name"]),
            models.Index(fields=["organization_id", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.envelope_id}:{self.field_name}:{self.status}"
