from __future__ import annotations

from django.db import models


class HouseholdProfile(models.Model):
    signature = models.CharField("assinatura do nucleo", max_length=500, unique=True)
    head_person_id = models.IntegerField("cabeca da familia", null=True, blank=True, db_index=True)
    display_name_override = models.CharField("nome de guerra", max_length=240, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "perfil de familia domiciliar"
        verbose_name_plural = "perfis de familias domiciliares"
        ordering = ["signature"]

    def __str__(self) -> str:
        return self.display_name_override or self.signature
