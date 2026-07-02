from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from power_church_core.normalization import normalize_match_name
from power_church_django.apps.contributions.models import (
    ContributionTypeSnapshot,
    NativeContribution,
    ReceiptDispatch,
    ReceiptSnapshot,
)
from power_church_django.apps.imports.models import CentRuleSnapshot, StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.imports.services import (
    close_statement_lot_postgres_native,
    dashboard_summary_postgres,
    prepare_statement_lot_postgres_native,
    reprocess_statement_lot_postgres_native,
    update_statement_movement_postgres_native,
)
from power_church_django.apps.people.models import PersonSnapshot


@override_settings(
    POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED=True,
    POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED=False,
)
class NativeStatementImportWorkflowTests(TestCase):
    def _person(self, legacy_id: int, name: str, cpf: str = "", email: str = "") -> PersonSnapshot:
        return PersonSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=1,
            preferred_unit_id=1,
            internal_code=f"P{legacy_id}",
            name=name,
            normalized_name=normalize_match_name(name),
            social_name="",
            cpf=cpf,
            rg="",
            birth_date=None,
            birth_date_raw="",
            sex="",
            marital_status="",
            primary_email=email,
            normalized_email=email.upper(),
            primary_phone="",
            primary_whatsapp="",
            status="membro_ativo",
            is_archived=False,
            is_active=True,
            notes="",
        )

    def _type(self, legacy_id: int = 10, name: str = "Dizimo") -> ContributionTypeSnapshot:
        return ContributionTypeSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=1,
            code="dizimo",
            name=name,
            is_active=True,
        )

    def _rule(self, legacy_id: int = 20, cent_code: str = "11", type_id: int = 10, type_name: str = "Dizimo") -> CentRuleSnapshot:
        return CentRuleSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=1,
            cent_code=cent_code,
            destination_name="Dizimos",
            contribution_type_legacy_id=type_id,
            contribution_type_name=type_name,
            campaign_legacy_id=None,
            campaign_name="",
            account_code="CENT.11",
            account_name="Dizimos",
            is_active=True,
        )

    def _lot(self, reference_key: str = "postgres_nativo:teste") -> StatementImportPilotLot:
        return StatementImportPilotLot.objects.create(
            reference_key=reference_key,
            source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
            source_db_path="",
            source_lot_id=None,
            bank_name="Sicoob",
            layout_code="SICOOB_CONTA_CORRENTE",
            file_name="extrato.pdf",
            file_hash="hash-teste",
            movement_count=0,
            total_value=Decimal("0"),
            lot_status="pendente",
            metadata={"review_counts": {}, "imported_count": 0, "ignored_count": 0, "pending_human_count": 0},
        )

    def _movement(
        self,
        lot: StatementImportPilotLot,
        *,
        order_in_lot: int,
        source_name: str,
        review_status: str = "pendente",
        cent_code: str = "11",
        bank_document: str = "",
        rule_id: int = 20,
    ) -> StatementImportPilotMovement:
        return StatementImportPilotMovement.objects.create(
            lot=lot,
            source_movement_id=None,
            page_number=1,
            order_in_lot=order_in_lot,
            movement_date=date(2026, 7, 1),
            competence="2026-07",
            competence_order=202607,
            amount=Decimal("100.00"),
            cent_code=cent_code,
            movement_kind="credito",
            receiving_code="PIX",
            bank_document=bank_document,
            document_type="cpf" if bank_document else "",
            prefix="",
            source_name=source_name,
            source_name_normalized=normalize_match_name(source_name),
            origin_label="Transferencia",
            confidence="",
            match_score=Decimal("0"),
            review_status=review_status,
            review_notes="",
            duplicate_reason="",
            fingerprint="",
            signature_global="",
            raw_text=source_name,
            metadata={
                "tipo_sugerido": "Dizimo",
                "organizacao_id": 1,
                "regra_id": rule_id,
                "resolved_tipo_contribuicao_id": 0,
                "contribution_type_id": 0,
                "rule_type_id": 10,
            },
        )

    def test_prepare_lot_creates_native_contribution_and_marks_lot_ready(self) -> None:
        self._type()
        self._rule()
        person = self._person(1, "Maria Souza", "12345678901", email="maria@example.com")
        lot = self._lot("postgres_nativo:prepare")
        movement = self._movement(lot, order_in_lot=1, source_name=person.name)

        result = prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        lot.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)

        self.assertEqual(result["importados"], 1)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(lot.lot_status, "concluido")
        self.assertEqual(int((lot.metadata or {}).get("imported_count") or 0), 1)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.operational_status, "regular")
        self.assertEqual(contribution.statement_movement_legacy_id, movement.id)
        self.assertEqual(result["auto_receipt_candidates"], 1)
        self.assertEqual(result["auto_receipt_created"], 1)
        self.assertEqual(result["auto_receipt_queued"], 1)
        receipt = ReceiptSnapshot.objects.get(is_cancelled=False)
        dispatch = ReceiptDispatch.objects.get(legacy_receipt_id=receipt.legacy_id)
        self.assertEqual(dispatch.status, ReceiptDispatch.Status.PENDING)
        self.assertEqual(dispatch.trigger, ReceiptDispatch.Trigger.AUTOMATIC)

    def test_manual_approve_without_person_is_preserved_on_reprocess_and_allows_close(self) -> None:
        self._type()
        self._rule()
        lot = self._lot("postgres_nativo:manual-no-person")
        movement = self._movement(lot, order_in_lot=1, source_name="Visitante sem ficha")

        contribution_id = update_statement_movement_postgres_native(
            movement.id,
            {
                "action": ["approve"],
                "resolved_person_id": ["0"],
                "resolved_tipo_contribuicao_id": ["10"],
                "review_notes": ["Sem pessoa por enquanto."],
            },
            actor="teste",
        )

        reprocess_statement_lot_postgres_native(lot.id)
        movement.refresh_from_db()
        lot.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=contribution_id)

        self.assertGreater(contribution_id, 0)
        self.assertEqual(movement.review_status, "aprovado")
        self.assertIsNone(movement.resolved_person_legacy_id)
        self.assertEqual(int((lot.metadata or {}).get("pending_human_count") or 0), 0)
        self.assertIsNone(contribution.person_legacy_id)
        self.assertIsNotNone(contribution.native_aux_contributor_id)
        self.assertEqual(contribution.operational_status, "sem_associacao")

        close_statement_lot_postgres_native(lot.id, actor="teste")
        lot.refresh_from_db()
        self.assertEqual(lot.lot_status, "encerrado")

    def test_same_owner_deactivates_existing_statement_contribution(self) -> None:
        self._type()
        self._rule()
        person = self._person(2, "João Souza", "98765432100", email="joao@example.com")
        lot = self._lot("postgres_nativo:same-owner")
        movement = self._movement(lot, order_in_lot=1, source_name=person.name)

        prepare_statement_lot_postgres_native(lot.id, actor="teste")
        movement.refresh_from_db()
        contribution_id = int(movement.imported_contribution_legacy_id or 0)
        self.assertGreater(contribution_id, 0)

        update_statement_movement_postgres_native(
            movement.id,
            {
                "action": ["same_owner"],
                "resolved_person_id": ["0"],
                "resolved_tipo_contribuicao_id": ["10"],
                "review_notes": ["Transferência interna."],
            },
            actor="teste",
        )

        movement.refresh_from_db()
        lot.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=contribution_id)

        self.assertEqual(movement.review_status, "ignorado")
        self.assertFalse(contribution.is_active)
        self.assertEqual(contribution.operational_status, "ignorado")
        self.assertEqual(lot.lot_status, "concluido")

    def test_dashboard_pending_bank_reviews_uses_same_human_pending_rule(self) -> None:
        self._type()
        self._rule()
        lot = self._lot("postgres_nativo:dashboard")
        self._movement(lot, order_in_lot=1, source_name="A", review_status="pendente")
        self._movement(lot, order_in_lot=2, source_name="B", review_status="revisar_destinacao")
        self._movement(lot, order_in_lot=3, source_name="C", review_status="revisar_duplicidade")
        self._movement(lot, order_in_lot=4, source_name="D", review_status="ignorado")

        with patch("power_church_django.apps.imports.services.organized_family_nuclei_summary", return_value={}):
            with patch("power_church_django.apps.imports.services.broad_family_candidates_summary", return_value={}):
                summary = dashboard_summary_postgres()

        self.assertEqual(summary["pending_bank_reviews"], 3)
