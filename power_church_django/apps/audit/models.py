from __future__ import annotations

from django.db import models


class AuditEvent(models.Model):
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    organization_id = models.IntegerField("organizacao", null=True, blank=True, db_index=True)
    actor = models.CharField("operador", max_length=160, blank=True, db_index=True)
    action = models.CharField("acao", max_length=140, db_index=True)
    table_name = models.CharField("tabela", max_length=120, db_index=True)
    record_id = models.IntegerField("registro", null=True, blank=True, db_index=True)
    source = models.CharField("origem", max_length=60, default="django", db_index=True)
    summary = models.CharField("resumo", max_length=255, blank=True)
    before = models.JSONField("antes", null=True, blank=True)
    after = models.JSONField("depois", null=True, blank=True)

    class Meta:
        verbose_name = "evento de auditoria Django"
        verbose_name_plural = "eventos de auditoria Django"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["action", "table_name"]),
            models.Index(fields=["source", "created_at"]),
        ]

    def __str__(self) -> str:
        target = f"{self.table_name} #{self.record_id}" if self.record_id else self.table_name
        return f"{self.action} em {target}"


try:
    from auditlog.registry import auditlog

    auditlog.register(AuditEvent, exclude_fields=["before", "after"])
except Exception:
    # The project can still run management commands before optional apps are ready.
    pass
