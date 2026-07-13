from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from power_church_django.apps.contributions.models import (
    ContributionTypeSnapshot,
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeLot,
    NativeEnvelopeProfileUpdate,
    ReceiptDispatch,
    ReceiptSnapshot,
)
from power_church_django.apps.people.models import PersonContributionSnapshot, PersonSnapshot
from power_church_django.services.contributions_native import person_statement_data_postgres
from power_church_django.services.envelopes_native import (
    ENVELOPE_IN_PROGRESS_STATUS,
    ENVELOPE_IN_PROGRESS_TIMEOUT,
    ENVELOPE_PENDING_STATUS,
    _native_envelope_line_payloads,
    create_envelope_contribution_batch_postgres,
    create_envelope_image_lot_postgres,
    get_envelope_detail_postgres,
    get_envelope_lot_detail_postgres,
    get_next_pending_envelope_id_postgres,
    ignore_pending_envelope_postgres,
    launch_pending_envelope_postgres,
    pending_envelope_contribution_context_postgres,
    update_launched_envelope_postgres,
)
from power_church_django.services.receipt_delivery import backfill_native_event_receipts
from power_church_django.services.runtime_errors import LegacyWriteError


class EnvelopeDigitizationLockTests(TestCase):
    def setUp(self) -> None:
        self.lot = NativeEnvelopeLot.objects.create(
            legacy_id=1,
            organization_id=1,
            name="Lote Teste",
            competence="jun/2026",
            competence_order=202606,
            status="aberto",
            is_active=True,
        )

    def test_next_pending_skips_other_operator_active_lock(self) -> None:
        locked = self._envelope(legacy_id=11, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")
        available = self._envelope(legacy_id=12, status=ENVELOPE_PENDING_STATUS)

        envelope_id = get_next_pending_envelope_id_postgres(self.lot.legacy_id, actor="operador_b")

        self.assertEqual(envelope_id, 12)
        locked.refresh_from_db()
        available.refresh_from_db()
        self.assertEqual(locked.status, ENVELOPE_IN_PROGRESS_STATUS)
        self.assertEqual(locked.updated_by, "operador_a")
        self.assertEqual(available.status, ENVELOPE_IN_PROGRESS_STATUS)
        self.assertEqual(available.updated_by, "operador_b")

    def test_next_pending_returns_same_envelope_for_same_operator(self) -> None:
        current = self._envelope(legacy_id=21, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")
        self._envelope(legacy_id=22, status=ENVELOPE_PENDING_STATUS)

        envelope_id = get_next_pending_envelope_id_postgres(self.lot.legacy_id, actor="operador_a")

        self.assertEqual(envelope_id, 21)
        current.refresh_from_db()
        self.assertEqual(current.status, ENVELOPE_IN_PROGRESS_STATUS)
        self.assertEqual(current.updated_by, "operador_a")

    def test_stale_lock_is_reclaimed_by_next_pending(self) -> None:
        stale = self._envelope(legacy_id=31, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")
        NativeEnvelope.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - ENVELOPE_IN_PROGRESS_TIMEOUT - timedelta(minutes=1)
        )

        envelope_id = get_next_pending_envelope_id_postgres(self.lot.legacy_id, actor="operador_b")

        self.assertEqual(envelope_id, 31)
        stale.refresh_from_db()
        self.assertEqual(stale.status, ENVELOPE_IN_PROGRESS_STATUS)
        self.assertEqual(stale.updated_by, "operador_b")

    def test_pending_context_blocks_other_operator_on_active_lock(self) -> None:
        self._envelope(legacy_id=41, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")

        with self.assertRaisesMessage(LegacyWriteError, "Envelope em digitacao por operador_a. Abra o proximo disponivel."):
            pending_envelope_contribution_context_postgres(41, actor="operador_b")

    def test_pending_context_reclaims_stale_lock(self) -> None:
        stale = self._envelope(legacy_id=51, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")
        NativeEnvelope.objects.filter(pk=stale.pk).update(
            updated_at=timezone.now() - ENVELOPE_IN_PROGRESS_TIMEOUT - timedelta(minutes=1)
        )

        context = pending_envelope_contribution_context_postgres(51, actor="operador_b")

        self.assertIsNotNone(context)
        stale.refresh_from_db()
        self.assertEqual(stale.status, ENVELOPE_IN_PROGRESS_STATUS)
        self.assertEqual(stale.updated_by, "operador_b")

    def test_launch_and_ignore_block_other_operator_active_lock(self) -> None:
        self._envelope(legacy_id=61, status=ENVELOPE_IN_PROGRESS_STATUS, updated_by="operador_a")

        with self.assertRaisesMessage(LegacyWriteError, "Envelope em digitacao por operador_a. Abra o proximo disponivel."):
            launch_pending_envelope_postgres(61, payload={}, actor="operador_b")
        with self.assertRaisesMessage(LegacyWriteError, "Envelope em digitacao por operador_a. Abra o proximo disponivel."):
            ignore_pending_envelope_postgres(61, justification="Ignorar agora", actor="operador_b")

    def _envelope(self, *, legacy_id: int, status: str, updated_by: str = "") -> NativeEnvelope:
        return NativeEnvelope.objects.create(
            legacy_id=legacy_id,
            organization_id=1,
            native_lot_legacy_id=self.lot.legacy_id,
            lot_name=self.lot.name,
            competence=self.lot.competence,
            competence_order=self.lot.competence_order,
            status=status,
            source="Teste",
            is_active=True,
            updated_by=updated_by,
        )


class EnvelopeLinePayloadTests(TestCase):
    def setUp(self) -> None:
        self.person_a = PersonSnapshot.objects.create(
            legacy_id=101,
            organization_id=1,
            name="Pessoa A",
            normalized_name="PESSOA A",
            social_name="",
            cpf="",
            primary_email="",
            normalized_email="",
            primary_phone="",
            primary_whatsapp="",
            status="membro_ativo",
            is_active=True,
            is_archived=False,
            notes="",
        )
        self.person_b = PersonSnapshot.objects.create(
            legacy_id=102,
            organization_id=1,
            name="Pessoa B",
            normalized_name="PESSOA B",
            social_name="",
            cpf="",
            primary_email="",
            normalized_email="",
            primary_phone="",
            primary_whatsapp="",
            status="membro_ativo",
            is_active=True,
            is_archived=False,
            notes="",
        )

    def test_single_main_contribution_does_not_require_blank_rateio_lines(self) -> None:
        payload = {
            "tipo_contribuicao_id_padrao": "7",
            "campanha_id_padrao": "",
            "line_count": "10",
            "linha_participante_ref_1": "",
            "linha_documento_1": "",
            "linha_tipo_contribuicao_id_1": "",
            "linha_campanha_id_1": "",
            "linha_valor_1": "",
            "linha_observacoes_1": "",
        }
        main_identity = {
            "person_legacy_id": 0,
            "contributor_legacy_id": 0,
            "native_aux_contributor_id": 0,
            "contributor_source": "",
            "contributor_name": "Membro Teste",
            "contributor_document": "",
            "contributor_type": "",
            "stored_name": "Membro Teste",
        }

        rows = _native_envelope_line_payloads(payload, 1, 100.0, main_identity)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["index"], 1)
        self.assertEqual(rows[0]["type_id"], 7)
        self.assertEqual(rows[0]["value"], 100.0)
        self.assertEqual(rows[0]["contributor_name"], "Membro Teste")

    def test_single_explicit_split_line_uses_main_identity_without_forcing_other_lines(self) -> None:
        payload = {
            "tipo_contribuicao_id_padrao": "7",
            "campanha_id_padrao": "",
            "line_count": "10",
            "linha_valor_1": "100,00",
            "linha_tipo_contribuicao_id_1": "",
            "linha_campanha_id_1": "",
            "linha_observacoes_1": "Dizimo integral",
        }
        main_identity = {
            "person_legacy_id": self.person_a.legacy_id,
            "contributor_legacy_id": 0,
            "native_aux_contributor_id": 0,
            "contributor_source": "person",
            "contributor_name": self.person_a.name,
            "contributor_document": "",
            "contributor_type": "person",
            "stored_name": self.person_a.name,
        }

        rows = _native_envelope_line_payloads(payload, 1, 100.0, main_identity)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_legacy_id"], self.person_a.legacy_id)
        self.assertEqual(rows[0]["type_id"], 7)
        self.assertEqual(rows[0]["value"], 100.0)
        self.assertEqual(rows[0]["notes"], "Dizimo integral")

    def test_two_split_lines_support_two_different_people(self) -> None:
        payload = {
            "tipo_contribuicao_id_padrao": "7",
            "campanha_id_padrao": "",
            "line_count": "10",
            "linha_participante_ref_1": f"Pessoa #{self.person_a.legacy_id}",
            "linha_valor_1": "60,00",
            "linha_tipo_contribuicao_id_1": "",
            "linha_campanha_id_1": "",
            "linha_observacoes_1": "Dizimo Pessoa A",
            "linha_participante_ref_2": f"Pessoa #{self.person_b.legacy_id}",
            "linha_valor_2": "40,00",
            "linha_tipo_contribuicao_id_2": "9",
            "linha_campanha_id_2": "3",
            "linha_observacoes_2": "Oferta Pessoa B",
        }
        main_identity = {
            "person_legacy_id": self.person_a.legacy_id,
            "contributor_legacy_id": 0,
            "native_aux_contributor_id": 0,
            "contributor_source": "person",
            "contributor_name": self.person_a.name,
            "contributor_document": "",
            "contributor_type": "person",
            "stored_name": self.person_a.name,
        }

        rows = _native_envelope_line_payloads(payload, 1, 100.0, main_identity)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["person_legacy_id"], self.person_a.legacy_id)
        self.assertEqual(rows[0]["type_id"], 7)
        self.assertEqual(rows[0]["value"], 60.0)
        self.assertEqual(rows[1]["person_legacy_id"], self.person_b.legacy_id)
        self.assertEqual(rows[1]["type_id"], 9)
        self.assertEqual(rows[1]["campaign_id"], 3)
        self.assertEqual(rows[1]["value"], 40.0)


class EnvelopeOperationalFlowTests(TestCase):
    def setUp(self) -> None:
        self.person = PersonSnapshot.objects.create(
            legacy_id=201,
            organization_id=1,
            name="Pessoa Envelope",
            normalized_name="PESSOA ENVELOPE",
            social_name="",
            cpf="",
            primary_email="",
            normalized_email="",
            primary_phone="2122222222",
            primary_whatsapp="",
            status="membro_ativo",
            is_active=True,
            is_archived=False,
            notes="",
        )
        self.type_snapshot = ContributionTypeSnapshot.objects.create(
            legacy_id=7,
            organization_id=1,
            code="DIZIMO",
            name="Dizimo",
            is_active=True,
        )

    def test_create_envelope_lot_accepts_local_folder_path(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as runtime_dir:
            source_path = Path(source_dir)
            (source_path / "001.jpg").write_bytes(b"fake-image-a")
            (source_path / "002.png").write_bytes(b"fake-image-b")

            with patch.dict(os.environ, {"POWER_CHURCH_ENVELOPE_DIR": runtime_dir}, clear=False):
                result = create_envelope_image_lot_postgres(
                    {
                        "nome_lote": "Lote local",
                        "competencia_mes": "2026-07",
                        "data_padrao_recebimento": "2026-07-01",
                        "origem_operacional": "Scanner local",
                        "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                        "pasta_origem": str(source_path),
                    },
                    actor="tester",
                )

        self.assertEqual(len(result["envelope_ids"]), 2)
        self.assertEqual(NativeEnvelope.objects.filter(native_lot_legacy_id=result["lot_id"]).count(), 2)

    def test_create_envelope_lot_accepts_zip_upload(self) -> None:
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("GAZOFILACIO/.DS_Store", b"ignored")
            archive.writestr("GAZOFILACIO/001.jpg", b"fake-image-a")
            archive.writestr("GAZOFILACIO/002.png", b"fake-image-b")
            archive.writestr("__MACOSX/ignored.jpg", b"ignored")
            archive.writestr("GAZOFILACIO/readme.txt", b"ignored")
        zip_upload = SimpleUploadedFile("gazofilacio.zip", archive_buffer.getvalue(), content_type="application/zip")

        with TemporaryDirectory() as runtime_dir:
            with patch.dict(os.environ, {"POWER_CHURCH_ENVELOPE_DIR": runtime_dir}, clear=False):
                result = create_envelope_image_lot_postgres(
                    {
                        "nome_lote": "Lote zipado",
                        "competencia_mes": "2026-07",
                        "data_padrao_recebimento": "2026-07-01",
                        "origem_operacional": "Upload zipado",
                        "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                    },
                    zip_upload=zip_upload,
                    actor="tester",
                )

        created = list(NativeEnvelope.objects.filter(native_lot_legacy_id=result["lot_id"]).order_by("legacy_id"))
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0].image_original_name, "GAZOFILACIO/001.jpg")
        self.assertEqual(created[1].image_original_name, "GAZOFILACIO/002.png")

    def test_create_envelope_lot_rejects_zip_without_supported_files(self) -> None:
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("GAZOFILACIO/.DS_Store", b"ignored")
            archive.writestr("GAZOFILACIO/readme.txt", b"ignored")
        zip_upload = SimpleUploadedFile("vazio.zip", archive_buffer.getvalue(), content_type="application/zip")

        with TemporaryDirectory() as runtime_dir:
            with patch.dict(os.environ, {"POWER_CHURCH_ENVELOPE_DIR": runtime_dir}, clear=False):
                with self.assertRaisesMessage(LegacyWriteError, "O .zip informado nao contem imagens ou PDFs validos de envelopes."):
                    create_envelope_image_lot_postgres(
                        {
                            "nome_lote": "Lote zipado",
                            "competencia_mes": "2026-07",
                            "data_padrao_recebimento": "2026-07-01",
                            "origem_operacional": "Upload zipado",
                            "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                        },
                        zip_upload=zip_upload,
                        actor="tester",
                    )

    def test_create_manual_envelope_accepts_local_file_path_and_updates_phone_immediately(self) -> None:
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as runtime_dir:
            source_file = Path(source_dir) / "manual.jpg"
            source_file.write_bytes(b"fake-manual-envelope")

            with patch.dict(os.environ, {"POWER_CHURCH_ENVELOPE_DIR": runtime_dir}, clear=False):
                result = create_envelope_contribution_batch_postgres(
                    {
                        "data_recebimento": "2026-07-02",
                        "competencia_mes": "2026-07",
                        "nome_lote": "Envelope manual local",
                        "valor_total": "150,00",
                        "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                        "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                        "telefone_informado": "21999998888",
                        "aplicar_telefone_na_ficha": "1",
                        "justificativa": "Teste operacional de envelope.",
                        "origem_operacional": "Fluxo local",
                        "imagem_envelope_path": str(source_file),
                    },
                    None,
                    actor="tester",
                )

        envelope = NativeEnvelope.objects.get(legacy_id=result["envelope_id"])
        self.person.refresh_from_db()
        self.assertEqual(envelope.status, "lancado")
        self.assertEqual(self.person.primary_phone, "21999998888")
        self.assertFalse(
            NativeEnvelopeProfileUpdate.objects.filter(
                envelope=envelope,
                field_name="telefone",
                status=NativeEnvelopeProfileUpdate.Status.PENDING,
            ).exists()
        )

    def test_lot_detail_exposes_edit_url_for_launched_envelope(self) -> None:
        lot = NativeEnvelopeLot.objects.create(
            legacy_id=2,
            organization_id=1,
            name="Lote com lancado",
            competence="jul/2026",
            competence_order=202607,
            status="digitado",
            is_active=True,
        )
        NativeEnvelope.objects.create(
            legacy_id=301,
            organization_id=1,
            native_lot_legacy_id=lot.legacy_id,
            lot_name=lot.name,
            competence=lot.competence,
            competence_order=lot.competence_order,
            status="lancado",
            total_informed=Decimal("50.00"),
            is_active=True,
        )

        detail = get_envelope_lot_detail_postgres(lot.legacy_id)

        self.assertIsNotNone(detail)
        item = detail["items"][0]
        self.assertEqual(item["edit_url"], "/contributions/envelopes/301/edit/")
        self.assertEqual(item["launch_url"], "")

    def test_launch_pending_envelope_creates_contribution_and_closes_lot(self) -> None:
        lot = NativeEnvelopeLot.objects.create(
            legacy_id=3,
            organization_id=1,
            name="Lote pendente",
            competence="jul/2026",
            competence_order=202607,
            status="aberto",
            is_active=True,
        )
        envelope = NativeEnvelope.objects.create(
            legacy_id=302,
            organization_id=1,
            native_lot_legacy_id=lot.legacy_id,
            lot_name=lot.name,
            competence=lot.competence,
            competence_order=lot.competence_order,
            status=ENVELOPE_PENDING_STATUS,
            is_active=True,
        )

        result = launch_pending_envelope_postgres(
            envelope.legacy_id,
            {
                "data_recebimento": "2026-07-03",
                "valor_total": "80,00",
                "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                "justificativa": "Lancamento operacional do envelope.",
                "origem_operacional": "Teste de lote",
            },
            actor="tester",
        )

        envelope.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(envelope.status, "lancado")
        self.assertEqual(len(result["contribution_ids"]), 1)
        self.assertEqual(lot.status, "digitado")
        self.assertTrue(NativeContribution.objects.filter(legacy_id=result["contribution_ids"][0], is_active=True).exists())

    def test_ignore_pending_envelope_updates_status_and_lot(self) -> None:
        lot = NativeEnvelopeLot.objects.create(
            legacy_id=4,
            organization_id=1,
            name="Lote para ignorar",
            competence="jul/2026",
            competence_order=202607,
            status="aberto",
            is_active=True,
        )
        envelope = NativeEnvelope.objects.create(
            legacy_id=303,
            organization_id=1,
            native_lot_legacy_id=lot.legacy_id,
            lot_name=lot.name,
            competence=lot.competence,
            competence_order=lot.competence_order,
            status=ENVELOPE_PENDING_STATUS,
            is_active=True,
        )

        ignore_pending_envelope_postgres(envelope.legacy_id, justification="Imagem em branco", actor="tester")

        envelope.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(envelope.status, "ignorado")
        self.assertEqual(lot.status, "digitado")

    def test_update_launched_envelope_recreates_active_contribution(self) -> None:
        envelope = NativeEnvelope.objects.create(
            legacy_id=304,
            organization_id=1,
            lot_name="Envelope corrigido",
            competence="jul/2026",
            competence_order=202607,
            status=ENVELOPE_PENDING_STATUS,
            is_active=True,
        )
        launch_pending_envelope_postgres(
            envelope.legacy_id,
            {
                "data_recebimento": "2026-07-04",
                "valor_total": "90,00",
                "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                "justificativa": "Primeiro lancamento operacional.",
                "origem_operacional": "Teste",
            },
            actor="tester",
        )
        original_ids = list(envelope.items.filter(is_active=True).values_list("contribution_legacy_id", flat=True))

        result = update_launched_envelope_postgres(
            envelope.legacy_id,
            {
                "data_recebimento": "2026-07-04",
                "valor_total": "120,00",
                "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                "justificativa": "Correcao auditada do envelope.",
                "origem_operacional": "Teste corrigido",
            },
            actor="tester",
        )

        envelope.refresh_from_db()
        self.assertEqual(envelope.total_informed, Decimal("120.00"))
        self.assertEqual(envelope.status, "lancado")
        self.assertEqual(len(result["contribution_ids"]), 1)
        self.assertFalse(NativeContribution.objects.filter(legacy_id__in=original_ids, is_active=True).exists())
        self.assertTrue(NativeContribution.objects.filter(legacy_id=result["contribution_ids"][0], is_active=True).exists())

    def test_envelope_detail_shows_aux_contributor_when_no_person(self) -> None:
        aux = NativeAuxContributor.objects.create(
            organization_id=1,
            name="Contribuinte Auxiliar",
            normalized_name="CONTRIBUINTE AUXILIAR",
            primary_document="12345678900",
            is_active=True,
        )
        envelope = NativeEnvelope.objects.create(
            legacy_id=401,
            organization_id=1,
            native_aux_contributor_id=aux.id,
            status="lancado",
            is_active=True,
        )

        detail = get_envelope_detail_postgres(envelope.legacy_id)

        self.assertIsNotNone(detail)
        self.assertEqual(detail["contribuinte_nome"], aux.name)
        self.assertEqual(detail["documento_principal"], aux.primary_document)


class PersonStatementDataPostgresTests(TestCase):
    def setUp(self) -> None:
        self.person = PersonSnapshot.objects.create(
            legacy_id=501,
            organization_id=1,
            name="Pessoa Extrato",
            normalized_name="PESSOA EXTRATO",
            social_name="",
            cpf="12345678901",
            primary_email="pessoa@example.com",
            normalized_email="PESSOA@EXAMPLE.COM",
            primary_phone="21999999999",
            primary_whatsapp="21999999999",
            status="membro_ativo",
            is_active=True,
            is_archived=False,
            notes="",
        )
        PersonContributionSnapshot.objects.create(
            legacy_id=9001,
            organization_id=1,
            person=self.person,
            contributor_legacy_id=0,
            received_at=timezone.now().date(),
            received_at_raw="2026-07-01",
            competence="2026-07",
            competence_order=202607,
            amount=Decimal("123.45"),
            operational_status="regular",
            contribution_type_name="Dizimo",
            receipt_method_name="Dinheiro",
            source_name="manual",
            is_active=True,
        )

    def test_statement_entries_fall_back_to_empty_observacoes_when_snapshot_has_no_notes(self) -> None:
        statement = person_statement_data_postgres(self.person.legacy_id)

        self.assertIsNotNone(statement)
        entries = [entry for entry in statement["entries"] if entry["kind"] == "item"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["observacoes"], "")
        self.assertEqual(entries[0]["tipo"], "Dizimo")


@override_settings(
    POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED=True,
    POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED=False,
)
class EnvelopeAutomaticReceiptTests(TestCase):
    def setUp(self) -> None:
        self.person = PersonSnapshot.objects.create(
            legacy_id=601,
            organization_id=1,
            name="Pessoa Recibo Envelope",
            normalized_name="PESSOA RECIBO ENVELOPE",
            social_name="",
            cpf="12345678901",
            primary_email="pessoa.recibo@example.com",
            normalized_email="PESSOA.RECIBO@EXAMPLE.COM",
            primary_phone="21999999999",
            primary_whatsapp="21999999999",
            status="membro_ativo",
            is_active=True,
            is_archived=False,
            notes="",
        )
        self.type_snapshot = ContributionTypeSnapshot.objects.create(
            legacy_id=17,
            organization_id=1,
            code="DIZIMO",
            name="Dizimo",
            is_active=True,
        )

    def test_launch_pending_envelope_creates_receipt_dispatch_for_person_with_email(self) -> None:
        envelope = NativeEnvelope.objects.create(
            legacy_id=701,
            organization_id=1,
            lot_name="Lote recibo envelope",
            competence="jul/2026",
            competence_order=202607,
            status=ENVELOPE_PENDING_STATUS,
            is_active=True,
        )

        result = launch_pending_envelope_postgres(
            envelope.legacy_id,
            {
                "data_recebimento": "2026-07-05",
                "valor_total": "90,00",
                "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                "justificativa": "Envelope com recibo automatico.",
                "origem_operacional": "Teste de recibo",
            },
            actor="tester",
        )

        contribution_id = result["contribution_ids"][0]
        receipt = ReceiptSnapshot.objects.get(is_cancelled=False)
        dispatch = ReceiptDispatch.objects.get(legacy_receipt_id=receipt.legacy_id)

        self.assertEqual(receipt.person_legacy_id, self.person.legacy_id)
        self.assertEqual(dispatch.status, ReceiptDispatch.Status.SENT)
        self.assertEqual(dispatch.trigger, ReceiptDispatch.Trigger.AUTOMATIC)
        self.assertEqual(result["auto_receipt"]["created"], 1)
        self.assertEqual(result["auto_receipt"]["sent"], 1)
        self.assertTrue(NativeContribution.objects.filter(legacy_id=contribution_id, is_active=True).exists())

    @override_settings(
        POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED=False,
        POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED=False,
    )
    def test_backfill_native_event_receipts_queues_missing_envelope_receipts(self) -> None:
        envelope = NativeEnvelope.objects.create(
            legacy_id=702,
            organization_id=1,
            lot_name="Lote retroativo",
            competence="jul/2026",
            competence_order=202607,
            status=ENVELOPE_PENDING_STATUS,
            is_active=True,
        )
        launch_pending_envelope_postgres(
            envelope.legacy_id,
            {
                "data_recebimento": "2026-07-06",
                "valor_total": "75,00",
                "tipo_contribuicao_id_padrao": str(self.type_snapshot.legacy_id),
                "participante_principal_ref": f"Pessoa #{self.person.legacy_id}",
                "justificativa": "Envelope sem disparo inicial.",
                "origem_operacional": "Teste retroativo",
            },
            actor="tester",
        )

        self.assertFalse(ReceiptDispatch.objects.exists())

        with self.settings(
            POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED=True,
            POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED=False,
        ):
            summary = backfill_native_event_receipts(actor="tester")

        dispatch = ReceiptDispatch.objects.get()
        self.assertEqual(dispatch.status, ReceiptDispatch.Status.PENDING)
        self.assertEqual(dispatch.trigger, ReceiptDispatch.Trigger.RETROACTIVE)
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["queued"], 1)
