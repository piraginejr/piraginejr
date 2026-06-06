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
