from __future__ import annotations

from django.apps import AppConfig


class ImportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "power_church_django.apps.imports"
    verbose_name = "Importacoes"

