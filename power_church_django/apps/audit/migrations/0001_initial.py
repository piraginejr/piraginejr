from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criado em")),
                ("organization_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="organizacao")),
                ("actor", models.CharField(blank=True, db_index=True, max_length=160, verbose_name="operador")),
                ("action", models.CharField(db_index=True, max_length=140, verbose_name="acao")),
                ("table_name", models.CharField(db_index=True, max_length=120, verbose_name="tabela")),
                ("record_id", models.IntegerField(blank=True, db_index=True, null=True, verbose_name="registro")),
                ("source", models.CharField(db_index=True, default="django", max_length=60, verbose_name="origem")),
                ("summary", models.CharField(blank=True, max_length=255, verbose_name="resumo")),
                ("before", models.JSONField(blank=True, null=True, verbose_name="antes")),
                ("after", models.JSONField(blank=True, null=True, verbose_name="depois")),
            ],
            options={
                "verbose_name": "evento de auditoria Django",
                "verbose_name_plural": "eventos de auditoria Django",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["action", "table_name"], name="audit_audit_action_eb38a1_idx"),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["source", "created_at"], name="audit_audit_source_982bd0_idx"),
        ),
    ]
