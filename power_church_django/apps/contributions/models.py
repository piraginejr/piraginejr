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
