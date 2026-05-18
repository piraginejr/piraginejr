from __future__ import annotations

from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "power_church_django.apps.people"
    verbose_name = "Pessoas"

