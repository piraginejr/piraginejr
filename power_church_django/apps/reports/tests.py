from __future__ import annotations

import codecs
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from tablib import Dataset

from power_church_django.apps.reports import views
from power_church_django.services.data_exchange import dataset_download_response
from power_church_django.services.pdf_reports import contribution_period_pdf


class ReportEncodingTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_csv_download_uses_utf8_bom(self) -> None:
        dataset = Dataset(title="Pessoas")
        dataset.headers = ["Nome", "Cidade"]
        dataset.append(["João", "São Gonçalo"])
        dataset.append(["José", "Niterói"])

        response = dataset_download_response(dataset, "csv", "pessoas")

        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertTrue(response.content.startswith(codecs.BOM_UTF8))
        decoded = response.content.decode("utf-8-sig")
        self.assertIn("João", decoded)
        self.assertIn("José", decoded)
        self.assertIn("São Gonçalo", decoded)
        self.assertIn("Niterói", decoded)

    def test_contribution_period_pdf_keeps_portuguese_characters(self) -> None:
        report = {
            "competencia": "",
            "q": "João José Ângela Conceição São Gonçalo Niterói",
            "date_start": "",
            "date_end": "",
            "items": [
                {
                    "group_label": "Contribuintes com nome",
                    "nome": "João José Ângela Conceição",
                    "documento": "123.456.789-10",
                    "remessas": [{"data": "01/07/2026", "valor_fmt": "R$ 10,00"}],
                    "total_fmt": "R$ 10,00",
                    "sigla": "SA",
                },
                {
                    "group_label": "Contribuintes com nome",
                    "nome": "São Gonçalo Niterói",
                    "documento": "109.876.543-21",
                    "remessas": [{"data": "02/07/2026", "valor_fmt": "R$ 20,00"}],
                    "total_fmt": "R$ 20,00",
                    "sigla": "NF",
                },
            ],
            "summary": {
                "total_fmt": "R$ 30,00",
                "contribuintes": 2,
                "remessas": 2,
                "sa": 1,
                "si": 0,
                "nf": 1,
                "nv": 0,
                "nm": 0,
                "nr": 0,
                "somente_documento": 0,
            },
        }

        payload = contribution_period_pdf(report)

        for value in ["João", "José", "Ângela", "Conceição", "São Gonçalo", "Niterói"]:
            self.assertIn(value.encode("cp1252"), payload)

    @patch("power_church_django.apps.reports.views.contribution_report_postgres")
    def test_report_index_html_explicitly_uses_utf8(self, report_mock) -> None:
        report_mock.return_value = {
            "items": [],
            "summary": {},
            "competencias": [],
            "truncated": False,
        }

        request = self.factory.get("/reports/")
        response = views.index(request)

        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
