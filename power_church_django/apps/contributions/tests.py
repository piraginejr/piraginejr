from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from power_church_django.apps.contributions.models import NativeEnvelope, NativeEnvelopeLot
from power_church_django.services.envelopes_native import (
    ENVELOPE_IN_PROGRESS_STATUS,
    ENVELOPE_IN_PROGRESS_TIMEOUT,
    ENVELOPE_PENDING_STATUS,
    _native_envelope_line_payloads,
    get_next_pending_envelope_id_postgres,
    ignore_pending_envelope_postgres,
    launch_pending_envelope_postgres,
    pending_envelope_contribution_context_postgres,
)
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
