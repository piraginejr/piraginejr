from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from power_church_core.normalization import normalize_query
from power_church_django.apps.people.models import PersonSnapshot
from power_church_django.services.legacy import list_people


class NormalizationEncodingTests(SimpleTestCase):
    def test_normalize_query_repairs_cp437_utf8_mojibake(self) -> None:
        self.assertEqual(normalize_query("Gouv├¬a"), "Gouvêa")
        self.assertEqual(normalize_query("Jo├úo"), "João")
        self.assertEqual(normalize_query("Concei├º├úo"), "Conceição")
        self.assertEqual(normalize_query("S├úo Gon├ºalo"), "São Gonçalo")
        self.assertEqual(normalize_query("Niter├│i"), "Niterói")


class PeopleSnapshotEncodingTests(TestCase):
    def test_list_people_repairs_mojibake_names_from_snapshot(self) -> None:
        PersonSnapshot.objects.create(
            legacy_id=2,
            organization_id=1,
            preferred_unit_id=1,
            internal_code="ABC-2",
            name="Abner Parreira de Gouv├¬a",
            normalized_name="ABNER PARREIRA DE GOUVEA",
            social_name="",
            cpf="",
            rg="",
            birth_date=None,
            birth_date_raw="",
            sex="",
            marital_status="",
            primary_email="abner@example.com",
            normalized_email="abner@example.com",
            primary_phone="21999999999",
            primary_whatsapp="",
            status="membro_ativo",
            is_archived=False,
            is_active=True,
            notes="",
        )

        payload = list_people()

        self.assertEqual(payload["items"][0]["nome"], "Abner Parreira de Gouvêa")
