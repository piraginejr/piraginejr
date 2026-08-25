from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from django.test import TestCase, override_settings

from power_church_core.bank_parsers import parse_statement_pdf_by_layout
from power_church_core.normalization import normalize_match_name
from power_church_django.apps.contributions.models import (
    ContributionTypeSnapshot,
    NativeAuxContributor,
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
from power_church_django.apps.people.models import PersonContributionSnapshot, PersonSnapshot
from power_church_django.apps.people.models import PersonContributorSnapshot, PersonIdentifierSnapshot
from power_church_django.services.financial_identity_lookup import rebuild_financial_identity_lookup
from power_church_django.services.receipt_delivery import backfill_native_event_receipts


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
        origin_label: str = "Transferencia",
        raw_text: str | None = None,
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
            origin_label=origin_label,
            confidence="",
            match_score=Decimal("0"),
            review_status=review_status,
            review_notes="",
            duplicate_reason="",
            fingerprint="",
            signature_global="",
            raw_text=raw_text if raw_text is not None else source_name,
            metadata={
                "tipo_sugerido": "Dizimo",
                "organizacao_id": 1,
                "regra_id": rule_id,
                "resolved_tipo_contribuicao_id": 0,
                "contribution_type_id": 0,
                "rule_type_id": 10,
            },
        )

    def _existing_contribution(
        self,
        person: PersonSnapshot,
        *,
        legacy_id: int,
        received_at: date,
        competence: str,
        amount: str = "100.00",
    ) -> PersonContributionSnapshot:
        return PersonContributionSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=person.organization_id,
            person=person,
            contributor_legacy_id=None,
            received_at=received_at,
            received_at_raw=received_at.isoformat(),
            competence=competence,
            competence_order=202607,
            amount=Decimal(amount),
            operational_status="regular",
            contribution_type_name="Dizimo",
            receipt_method_name="PIX",
            source_name="Teste existente",
            is_active=True,
        )

    def _person_contributor(
        self,
        person: PersonSnapshot,
        *,
        legacy_id: int,
        name: str,
        primary_document: str = "",
        document_type: str = "",
    ) -> PersonContributorSnapshot:
        return PersonContributorSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=person.organization_id,
            person=person,
            name=name,
            contributor_type="pf" if document_type != "cnpj" else "pj",
            primary_document=primary_document,
            document_type=document_type,
            origin="teste",
            quality="doador",
            status="ativo",
            is_active=True,
        )

    def _person_identifier(
        self,
        person: PersonSnapshot,
        *,
        legacy_id: int,
        identifier_type: str,
        value: str,
        notes: str = "",
        contributor_legacy_id: int | None = None,
    ) -> PersonIdentifierSnapshot:
        return PersonIdentifierSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=person.organization_id,
            person=person,
            contributor_legacy_id=contributor_legacy_id,
            identifier_type=identifier_type,
            value=value,
            is_primary=True,
            notes=notes,
            is_active=True,
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

    def test_sicoob_current_account_parser_keeps_value_with_visual_detail_line(self) -> None:
        page_rows = [
            {"text": "Data", "x": 37.5, "y": 800.3},
            {"text": "Documento", "x": 69.1, "y": 800.3},
            {"text": "Histórico", "x": 132.4, "y": 800.3},
            {"text": "Valor", "x": 536.7, "y": 800.3},
            {"text": "11/06", "x": 37.5, "y": 690.0},
            {"text": "Pix", "x": 69.1, "y": 690.0},
            {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 690.0},
            {"text": "Recebimento Pix Patricia Gomes de Andrade Brandao ***.574.697-** dizimo restante", "x": 132.4, "y": 681.3},
            {"text": "R$ 60,00C", "x": 508.5, "y": 688.2},
            {"text": "11/06", "x": 37.5, "y": 665.3},
            {"text": "Pix", "x": 69.1, "y": 665.3},
            {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 665.3},
            {"text": "Recebimento Pix ODILON GUIMARAES JUNIOR ***.481.357-**", "x": 132.4, "y": 656.6},
            {"text": "R$ 1.020,00C", "x": 493.9, "y": 663.4},
            {"text": "11/06", "x": 37.5, "y": 640.5},
            {"text": "Pix", "x": 69.1, "y": 640.5},
            {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 640.5},
            {"text": "Recebimento Pix MARIA JOSE DE SOUZA PONCE RIBEIRO ***.969.047-**", "x": 132.4, "y": 631.8},
            {"text": "R$ 200,00C", "x": 502.7, "y": 638.7},
        ]

        with NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(b"fake-sicoob-pdf")
            tmp.flush()
            with patch(
                "power_church_core.bank_parsers.extract_pdf_pages",
                return_value=["PERÍODO: 01/06/2026 - 30/06/2026"],
            ):
                with patch("power_church_core.bank_parsers.extract_pdf_line_selections", return_value=[page_rows]):
                    parsed = parse_statement_pdf_by_layout("SICOOB_CONTA_CORRENTE", Path(tmp.name))

        by_name = {entry["source_name"]: entry for entry in parsed["entries"]}
        self.assertEqual(by_name["Patricia Gomes de Andrade Brandao"]["amount"], 60.0)
        self.assertEqual(by_name["ODILON GUIMARAES JUNIOR"]["amount"], 1020.0)
        self.assertEqual(by_name["MARIA JOSE DE SOUZA PONCE RIBEIRO"]["amount"], 200.0)

    def test_backfill_native_event_receipts_requeues_existing_statement_receipt_without_dispatch(self) -> None:
        self._type()
        self._rule()
        self._person(11, "Carlos Extrato", "12312312399", email="carlos@example.com")
        lot = self._lot("postgres_nativo:receipt-backfill")
        movement = self._movement(lot, order_in_lot=1, source_name="Carlos Extrato")

        result = prepare_statement_lot_postgres_native(lot.id, actor="teste")

        receipt = ReceiptSnapshot.objects.get(is_cancelled=False)
        ReceiptDispatch.objects.filter(legacy_receipt_id=receipt.legacy_id).delete()

        summary = backfill_native_event_receipts(actor="tester")

        movement.refresh_from_db()
        lot.refresh_from_db()
        dispatch = ReceiptDispatch.objects.get(legacy_receipt_id=receipt.legacy_id)

        self.assertEqual(result["auto_receipt_created"], 1)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(lot.lot_status, "concluido")
        self.assertEqual(dispatch.status, ReceiptDispatch.Status.PENDING)
        self.assertEqual(dispatch.trigger, ReceiptDispatch.Trigger.RETROACTIVE)
        self.assertEqual(summary["existing_receipts_queued"], 1)
        self.assertEqual(summary["queued"], 1)

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

    def test_prepare_lot_defaults_to_dizimo_when_cent_rule_is_missing(self) -> None:
        self._type(legacy_id=10, name="Dizimo")
        person = self._person(11, "Pessoa Sem Regra", "11122233344")
        lot = self._lot("postgres_nativo:dizimo-fallback")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=person.name,
            cent_code="00",
            bank_document=person.cpf,
            rule_id=0,
        )
        movement.metadata["tipo_sugerido"] = ""
        movement.metadata["rule_type_id"] = 0
        movement.metadata["contribution_type_id"] = 0
        movement.metadata["resolved_tipo_contribuicao_id"] = 0
        movement.save(update_fields=["metadata", "updated_at"])

        result = prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(result["importados"], 1)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(contribution.contribution_type_legacy_id, 10)
        self.assertEqual(contribution.contribution_type_name, "Dizimo")

    def test_prepare_lot_requires_destination_review_when_non_zero_cent_rule_is_missing(self) -> None:
        self._type(legacy_id=10, name="Dizimo")
        person = self._person(111, "Pessoa Centavos Sem Regra", "99988877766")
        lot = self._lot("postgres_nativo:destinacao-pendente")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=person.name,
            cent_code="26",
            bank_document=person.cpf,
            rule_id=0,
        )
        movement.metadata["tipo_sugerido"] = "Dizimo"
        movement.metadata["rule_type_id"] = 0
        movement.metadata["contribution_type_id"] = 0
        movement.metadata["resolved_tipo_contribuicao_id"] = 0
        movement.save(update_fields=["metadata", "updated_at"])

        result = prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        lot.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(result["importados"], 1)
        self.assertEqual(movement.review_status, "revisar_destinacao")
        self.assertEqual((movement.metadata or {}).get("resolved_tipo_contribuicao_id"), 0)
        self.assertEqual((lot.metadata or {}).get("pending_human_count"), 1)
        self.assertEqual(((lot.metadata or {}).get("review_counts") or {}).get("revisar_destinacao"), 1)
        self.assertEqual(contribution.contribution_type_legacy_id, 0)

    def test_prepare_lot_uses_canonical_person_when_aux_is_already_linked(self) -> None:
        self._type()
        self._rule()
        person = self._person(12, "Domingos O Cardoso")
        aux = NativeAuxContributor.objects.create(
            organization_id=1,
            legacy_reference_id=901,
            person_legacy_id=person.legacy_id,
            name=person.name.upper(),
            normalized_name=normalize_match_name(person.name),
            primary_document="4199690",
            document_type="",
            contributor_type="pf",
            origin="extrato_bradesco",
            quality="doador",
            status="ativo",
            notes="",
            is_active=True,
        )
        lot = self._lot("postgres_nativo:aux-canonico")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=person.name.upper(),
            bank_document="4199690",
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(aux.person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
        self.assertIsNone(contribution.native_aux_contributor_id)
        self.assertEqual(contribution.contributor_source, "person_snapshot")

    def test_prepare_lot_marks_duplicate_when_same_person_amount_and_day_match(self) -> None:
        self._type()
        self._rule()
        person = self._person(13, "Pessoa Duplicada", "22233344455")
        self._existing_contribution(
            person,
            legacy_id=9001,
            received_at=date(2026, 7, 1),
            competence="2026-07",
        )
        lot = self._lot("postgres_nativo:duplicate-same-day")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=person.name,
            bank_document=person.cpf,
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        self.assertEqual(movement.review_status, "revisar_duplicidade")
        self.assertEqual(movement.duplicate_contribution_legacy_id, 9001)

    def test_prepare_lot_allows_same_person_amount_in_same_month_with_different_day(self) -> None:
        self._type()
        self._rule()
        person = self._person(14, "Pessoa Repetida no Mes", "33344455566")
        self._existing_contribution(
            person,
            legacy_id=9002,
            received_at=date(2026, 7, 2),
            competence="2026-07",
        )
        lot = self._lot("postgres_nativo:no-duplicate-same-competence")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=person.name,
            bank_document=person.cpf,
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        self.assertEqual(movement.review_status, "pronto")
        self.assertIsNone(movement.duplicate_contribution_legacy_id)

    def test_prepare_lot_uses_masked_cpf_identity_to_resolve_person(self) -> None:
        self._type()
        self._rule()
        person = self._person(15, "Nelson Chrizostimo da Silva Filho", "29789621787")
        self._person_contributor(
            person,
            legacy_id=301,
            name="NELSON C SILVA FH",
            primary_document="***.896.217-**",
            document_type="cpf",
        )
        self._person_identifier(
            person,
            legacy_id=401,
            identifier_type="cpf",
            value="***.896.217-**",
            notes="Registrado automaticamente pela origem pix.",
        )
        lot = self._lot("postgres_nativo:masked-cpf")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name="NELSON C SILVA FH",
            bank_document="***.896.217-**",
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(movement.resolved_person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.contributor_source, "person_snapshot")

    def test_prepare_lot_uses_company_identity_linked_to_person(self) -> None:
        self._type()
        self._rule()
        person = self._person(16, "Diego Juliano Bravim", "")
        self._person_contributor(
            person,
            legacy_id=302,
            name="Bravim Consultoria Ltda",
            primary_document="56.102.293 0001-72",
            document_type="cnpj",
        )
        self._person_identifier(
            person,
            legacy_id=402,
            identifier_type="cnpj",
            value="56.102.293 0001-72",
            notes="contribuinte:Bravim Consultoria Ltda",
        )
        lot = self._lot("postgres_nativo:company-identity")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name="Bravim Consultoria Ltda",
            bank_document="56.102.293 0001-72",
            rule_id=20,
        )
        movement.document_type = "cnpj"
        movement.save(update_fields=["document_type", "updated_at"])

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(movement.resolved_person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.contributor_source, "person_snapshot")

    def test_prepare_lot_uses_financial_name_when_person_has_no_cpf(self) -> None:
        self._type()
        self._rule()
        person = self._person(17, "Maria José Gomes de Oliveira", "")
        self._person_contributor(
            person,
            legacy_id=303,
            name="MARIA J GOMES DE OLIVEIRA",
            primary_document="",
            document_type="",
        )
        lot = self._lot("postgres_nativo:name-only")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name="MARIA J GOMES DE OLIVEIRA",
            bank_document="",
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(movement.resolved_person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.contributor_source, "person_snapshot")

    def test_prepare_lot_sends_to_manual_review_when_known_payer_differs_from_known_beneficiary(self) -> None:
        self._type()
        self._rule()
        payer = self._person(18, "Ana Herculana da Silva", "")
        beneficiary = self._person(19, "Paulo Henrique Rodrigues da Silva", "")
        lot = self._lot("postgres_nativo:declared-beneficiary")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name=payer.name,
            bank_document="",
            rule_id=20,
            origin_label="Dizimo de Paulo Henrique Rodrigues da Silva",
            raw_text="PIX RECEBIDO - OUTRA IF | Dizimo de Paulo Henrique Rodrigues da Silva",
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "revisar_pessoa")
        self.assertIsNone(movement.resolved_person_legacy_id)
        self.assertEqual(movement.suggested_person_legacy_id, beneficiary.legacy_id)
        self.assertEqual((movement.metadata or {}).get("declared_beneficiary_name"), beneficiary.name)
        self.assertTrue((movement.metadata or {}).get("declared_beneficiary_requires_review"))
        self.assertIsNone(contribution.person_legacy_id)
        self.assertEqual(contribution.contributor_name, payer.name)
        self.assertIn("Origem: Ana Herculana da Silva", contribution.notes)
        self.assertIn("Beneficiario declarado: Paulo Henrique Rodrigues da Silva", contribution.notes)

    def test_prepare_lot_auto_links_declared_beneficiary_when_payer_is_not_known_person(self) -> None:
        self._type()
        self._rule()
        beneficiary = self._person(20, "Paulo Henrique Rodrigues da Silva", "")
        lot = self._lot("postgres_nativo:declared-beneficiary-auto")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name="Ana Herculana da Silva",
            bank_document="",
            rule_id=20,
            origin_label="Dizimo de Paulo Henrique Rodrigues da Silva",
            raw_text="PIX RECEBIDO - OUTRA IF | Dizimo de Paulo Henrique Rodrigues da Silva",
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(movement.resolved_person_legacy_id, beneficiary.legacy_id)
        self.assertFalse((movement.metadata or {}).get("declared_beneficiary_requires_review"))
        self.assertEqual(contribution.person_legacy_id, beneficiary.legacy_id)
        self.assertEqual(contribution.contributor_name, beneficiary.name)
        self.assertIn("Origem: Ana Herculana da Silva", contribution.notes)
        self.assertIn("Beneficiario declarado: Paulo Henrique Rodrigues da Silva", contribution.notes)

    def test_financial_identity_lookup_table_feeds_people_matching_cache(self) -> None:
        self._type()
        self._rule()
        person = self._person(21, "Andre Luis Carvalho Alves", "")
        self._person_contributor(
            person,
            legacy_id=304,
            name="Andre Luis Carvalho Alves",
            primary_document="03292342422",
            document_type="cpf",
        )

        total = rebuild_financial_identity_lookup()
        self.assertGreater(total, 0)

        lot = self._lot("postgres_nativo:lookup-table")
        movement = self._movement(
            lot,
            order_in_lot=1,
            source_name="Andre Luis Carvalho Alves",
            bank_document="032.923.424-22",
            rule_id=20,
        )

        prepare_statement_lot_postgres_native(lot.id, actor="teste")

        movement.refresh_from_db()
        contribution = NativeContribution.objects.get(legacy_id=movement.imported_contribution_legacy_id)
        self.assertEqual(movement.review_status, "pronto")
        self.assertEqual(movement.resolved_person_legacy_id, person.legacy_id)
        self.assertEqual(contribution.person_legacy_id, person.legacy_id)
