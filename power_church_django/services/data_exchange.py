from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpResponse
from import_export.formats import base_formats
from tablib import Dataset

from power_church_django.services.legacy import list_people


EXPORT_FORMATS = {
    "csv": base_formats.CSV,
    "xlsx": base_formats.XLSX,
}


def people_export_dataset(q: str = "", status: str = "") -> dict[str, Any]:
    people = list_people(q=q, status=status, limit=100000)
    dataset = Dataset(title="Pessoas")
    dataset.headers = [
        "ID",
        "Codigo",
        "Nome",
        "CPF",
        "Status",
        "Sigla",
        "Ativo",
        "Telefone",
        "Email",
    ]
    for person in people["items"]:
        dataset.append(
            [
                person["id"],
                person["codigo"],
                person["nome"],
                person["cpf"],
                person["status"],
                person["sigla"],
                person["ativo"],
                person["telefone"],
                person["email"],
            ]
        )
    return {
        "dataset": dataset,
        "total": people["total"],
        "shown": people["shown"],
        "q": q,
        "status": status,
    }


def dataset_download_response(dataset: Dataset, export_format: str, filename_prefix: str) -> HttpResponse:
    export_format = (export_format or "xlsx").lower()
    format_class = EXPORT_FORMATS.get(export_format, EXPORT_FORMATS["xlsx"])
    exporter = format_class()
    payload = exporter.export_data(dataset)
    extension = exporter.get_extension()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}_{timestamp}.{extension}"
    response = HttpResponse(payload, content_type=exporter.get_content_type())
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
