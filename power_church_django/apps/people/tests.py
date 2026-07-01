from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from power_church_core.normalization import normalize_query
from django.urls import reverse

from power_church_django.apps.people.models import NativePeopleImportLot, NativePeopleImportPending, PersonSnapshot
from power_church_django.services.legacy import list_people


class NormalizationEncodingTests(SimpleTestCase):
    def test_normalize_query_repairs_cp437_utf8_mojibake(self) -> None:
        self.assertEqual(normalize_query("Gouv├¬a"), "Gouvêa")
        self.assertEqual(normalize_query("Ad├®lia Lass├® da Cruz Ara├║jo"), "Adélia Lassé da Cruz Araújo")
        self.assertEqual(normalize_query("Andr├®a Santiago da Silva"), "Andréa Santiago da Silva")
        self.assertEqual(normalize_query("Andr├® Asevedo Nepomuceno"), "André Asevedo Nepomuceno")
        self.assertEqual(normalize_query("Andr├®a Souza"), "Andréa Souza")
        self.assertEqual(normalize_query("Andr├® Lima"), "André Lima")
        self.assertEqual(normalize_query("Andr├® Luis Carvalho Alves"), "André Luis Carvalho Alves")
        self.assertEqual(normalize_query("Andr├® Marx Vieira Costa"), "André Marx Vieira Costa")
        self.assertEqual(normalize_query("Andr├® Ricardo Lisb├┤a Herdy"), "André Ricardo Lisbôa Herdy")
        self.assertEqual(normalize_query("Andr├® Rios dos Passos"), "André Rios dos Passos")
        self.assertEqual(normalize_query("Jo├úo"), "João")
        self.assertEqual(normalize_query("Jos├®"), "José")
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


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class PeopleImportLotPrintTests(TestCase):
    def test_import_lot_print_mode_renders_only_filtered_pendencies(self) -> None:
        user = get_user_model().objects.create_user(username="operador_teste", password="senha123")
        self.client.force_login(user)
        lot = NativePeopleImportLot.objects.create(
            legacy_id=1,
            import_type="pessoas_membros",
            file_name="membros.xlsx",
            status="confirmado",
            total_lines=10,
            open_pendencies=2,
            created_at_display="01/07/2026 10:00",
        )
        NativePeopleImportPending.objects.create(
            legacy_id=1,
            lot=lot,
            line_number=3,
            severity="aviso",
            issue_type="data_invalida",
            description="Data de nascimento invalida.",
            suggested_action="Conferir aniversario.",
            resolved=False,
            person_name="João da Silva",
        )
        NativePeopleImportPending.objects.create(
            legacy_id=2,
            lot=lot,
            line_number=4,
            severity="aviso",
            issue_type="cpf_invalido_ou_duplicado",
            description="CPF nao foi usado como chave automatica.",
            suggested_action="Conferir CPF.",
            resolved=False,
            person_name="José Souza",
        )

        response = self.client.get(
            reverse("people:import_lot", args=[1]),
            {"tipo": "data_invalida", "pendencia_status": "abertas", "print": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relatorio filtrado do lote de pessoas #1")
        self.assertContains(response, "João da Silva")
        self.assertNotContains(response, "José Souza")
        self.assertContains(response, "Imprimir este relatorio")
        self.assertNotContains(response, "Linhas importadas")
