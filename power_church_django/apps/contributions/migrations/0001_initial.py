from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ReceiptEmailTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=60, unique=True, verbose_name="chave")),
                ("name", models.CharField(max_length=120, verbose_name="nome")),
                ("subject_template", models.CharField(max_length=255, verbose_name="assunto padrao")),
                ("body_template", models.TextField(verbose_name="mensagem padrao")),
                ("default_from_email", models.CharField(blank=True, max_length=254, verbose_name="remetente padrao")),
                ("reply_to_email", models.CharField(blank=True, max_length=254, verbose_name="responder para")),
                ("active", models.BooleanField(db_index=True, default=True, verbose_name="ativo")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
            ],
            options={
                "verbose_name": "modelo de e-mail de recibo",
                "verbose_name_plural": "modelos de e-mail de recibo",
                "ordering": ["name", "id"],
            },
        ),
        migrations.CreateModel(
            name="ReceiptDispatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
                ("organization_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="organizacao")),
                ("legacy_person_id", models.IntegerField(db_index=True, verbose_name="pessoa legada")),
                ("legacy_receipt_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="recibo legado")),
                ("legacy_receipt_number", models.CharField(blank=True, max_length=80, verbose_name="numero do recibo")),
                ("person_name", models.CharField(blank=True, max_length=240, verbose_name="nome da pessoa")),
                ("person_email", models.CharField(blank=True, max_length=254, verbose_name="e-mail da pessoa")),
                ("competence", models.CharField(blank=True, db_index=True, max_length=40, verbose_name="competencia")),
                ("period_label", models.CharField(blank=True, max_length=140, verbose_name="rotulo do periodo")),
                ("period_start", models.DateField(blank=True, null=True, verbose_name="inicio do periodo")),
                ("period_end", models.DateField(blank=True, null=True, verbose_name="fim do periodo")),
                ("mode", models.CharField(choices=[("competencia", "Competencia"), ("intervalo", "Intervalo")], db_index=True, default="competencia", max_length=20, verbose_name="modo")),
                ("trigger", models.CharField(choices=[("manual", "Manual"), ("automatico", "Automatico"), ("retroativo", "Retroativo")], db_index=True, default="manual", max_length=20, verbose_name="origem")),
                ("status", models.CharField(choices=[("pendente", "Pendente"), ("enviado", "Enviado"), ("falhou", "Falhou"), ("cancelado", "Cancelado")], db_index=True, default="pendente", max_length=20, verbose_name="status")),
                ("auto_created", models.BooleanField(db_index=True, default=False, verbose_name="criado automaticamente")),
                ("email_to", models.CharField(blank=True, max_length=254, verbose_name="destinatario")),
                ("email_subject", models.CharField(blank=True, max_length=255, verbose_name="assunto")),
                ("email_body", models.TextField(blank=True, verbose_name="corpo do e-mail")),
                ("pdf_filename", models.CharField(blank=True, max_length=255, verbose_name="nome do pdf")),
                ("send_attempts", models.PositiveIntegerField(default=0, verbose_name="tentativas")),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True, verbose_name="ultima tentativa")),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="enviado em")),
                ("last_error", models.TextField(blank=True, verbose_name="ultimo erro")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="metadados")),
            ],
            options={
                "verbose_name": "fila de envio de recibo",
                "verbose_name_plural": "fila de envio de recibos",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="receiptdispatch",
            index=models.Index(fields=["status", "created_at"], name="contributio_status_84d84e_idx"),
        ),
        migrations.AddIndex(
            model_name="receiptdispatch",
            index=models.Index(fields=["legacy_person_id", "competence", "status"], name="contributio_legacy__b891e0_idx"),
        ),
        migrations.AddIndex(
            model_name="receiptdispatch",
            index=models.Index(fields=["trigger", "auto_created", "created_at"], name="contributio_trigger_47b64a_idx"),
        ),
    ]
