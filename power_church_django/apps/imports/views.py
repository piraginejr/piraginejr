from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from power_church_core.banking import STATEMENT_LAYOUT_LABELS
from power_church_django.services.legacy_bank_write import (
    LegacyBankWriteError,
    PDF_PROVIDER_MODES,
    close_bank_lot,
    create_pix_lot_from_upload,
    create_statement_lot_from_upload,
    import_ready_pix_lot,
    reprocess_bank_lot,
    save_cent_rule_from_form,
    update_bank_movement_from_form,
)
from power_church_django.services.legacy import (
    LegacyDatabaseError,
    cent_rules_data,
    dashboard_summary,
    get_bank_movement_detail,
    get_import_lot_detail,
    list_import_lots,
)


def dashboard(request: HttpRequest) -> HttpResponse:
    context = {"title": "Power Church Django", "layouts": STATEMENT_LAYOUT_LABELS}
    try:
        context["summary"] = dashboard_summary()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/dashboard.html", context)


def index(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        upload = request.FILES.get("extrato_pdf")
        if upload is None:
            messages.error(request, "Selecione um PDF bancario antes de importar.")
            return redirect("/imports/")
        payload = b"".join(upload.chunks())
        import_kind = str(request.POST.get("import_kind") or "statement").strip()
        pdf_provider_mode = str(request.POST.get("pdf_provider_mode") or "compare_pymupdf").strip()
        provider_label = next(
            (
                item["label"]
                for item in PDF_PROVIDER_MODES
                if item["value"] == pdf_provider_mode
            ),
            "Comparar Swift x PyMuPDF antes de gravar",
        )
        try:
            if import_kind == "pix_sicoob":
                lot_id = create_pix_lot_from_upload(upload.name, payload, pdf_provider_mode=pdf_provider_mode)
                messages.success(request, f"Lote PIX #{lot_id} criado com sucesso no Django. Motor PDF: {provider_label}.")
                return redirect(f"/imports/pix/{lot_id}/?status=pendencias")
            layout_code = str(request.POST.get("layout_code") or "SICOOB_CONTA_CORRENTE").strip().upper()
            lot_id = create_statement_lot_from_upload(
                upload.name,
                payload,
                layout_code=layout_code,
                pdf_provider_mode=pdf_provider_mode,
            )
            layout_label = STATEMENT_LAYOUT_LABELS.get(layout_code, layout_code)
            messages.success(request, f"Lote de extrato #{lot_id} ({layout_label}) criado com sucesso no Django. Motor PDF: {provider_label}.")
            return redirect(f"/imports/statement/{lot_id}/?status=pendencias")
        except LegacyBankWriteError as exc:
            messages.error(request, str(exc))
            return redirect("/imports/")

    context = {
        "title": "Importacoes bancarias",
        "statement_layouts": [
            {"code": code, "label": label}
            for code, label in STATEMENT_LAYOUT_LABELS.items()
            if code in {"SICOOB_RECEBIMENTOS", "SICOOB_CONTA_CORRENTE", "BRADESCO_EXTRATO", "SANTANDER_AUTO", "SANTANDER_CONSOLIDADO", "SANTANDER_NAO_CONSOLIDADO"}
        ],
        "pdf_provider_modes": PDF_PROVIDER_MODES,
    }
    try:
        context["lots"] = list_import_lots()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/list.html", context)


def cent_rules(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            saved_id = save_cent_rule_from_form(request.POST)
            messages.success(request, f"Regra de centavos #{saved_id} salva e sincronizada com conta/campanha.")
            return redirect(f"/imports/rules/?edit_rule_id={saved_id}")
        except LegacyBankWriteError as exc:
            messages.error(request, str(exc))
            return redirect("/imports/rules/")

    context = {"title": "Regras por centavos"}
    try:
        context["rules_data"] = cent_rules_data(edit_rule_id=int(request.GET.get("edit_rule_id") or 0))
    except (LegacyDatabaseError, ValueError) as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/rules.html", context)


def lot_detail(request: HttpRequest, kind: str, lot_id: int) -> HttpResponse:
    kind = "pix" if kind == "pix" else "statement"
    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()
        status = str(request.POST.get("status") or request.GET.get("status") or "").strip()
        target = f"/imports/{kind}/{lot_id}/"
        if status:
            target = f"{target}?status={status}"
        try:
            if action == "reprocess":
                updated = reprocess_bank_lot(kind, lot_id)
                messages.success(request, f"Lote reprocessado. {updated} movimento(s) foram revistos e o financeiro foi sincronizado.")
            elif action == "approve_movement":
                movement_id = int(request.POST.get("movement_id") or 0)
                if not movement_id:
                    raise LegacyBankWriteError("Movimento nao informado para confirmacao.")
                imported_contribution_id = update_bank_movement_from_form(kind, movement_id, request.POST)
                if imported_contribution_id:
                    messages.success(request, f"Movimento #{movement_id} confirmado no lote e sincronizado com a contribuicao #{imported_contribution_id}.")
                else:
                    messages.success(request, f"Movimento #{movement_id} confirmado no lote.")
            elif action == "import_ready" and kind == "pix":
                imported = import_ready_pix_lot(lot_id)
                messages.success(request, f"Financeiro do lote PIX sincronizado. {imported} movimento(s) receberam lancamento.")
            elif action == "close":
                result = close_bank_lot(kind, lot_id)
                messages.success(
                    request,
                    f"Lote encerrado. {result.get('importados', 0)} movimento(s) sincronizados e {result.get('movidos_contribuintes', 0)} pendencia(s) preservadas na central de contribuintes.",
                )
            else:
                messages.error(request, "Acao de lote nao reconhecida.")
        except LegacyBankWriteError as exc:
            messages.error(request, str(exc))
        return redirect(target)

    context = {
        "title": "Detalhe do lote",
        "kind": kind,
        "status": request.GET.get("status", ""),
    }
    try:
        context["detail"] = get_import_lot_detail(kind=kind, lot_id=lot_id, status=context["status"])
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/lot_detail.html", context)


def movement_detail(request: HttpRequest, kind: str, movement_id: int) -> HttpResponse:
    kind = "pix" if kind == "pix" else "statement"
    if request.method == "POST":
        return_to = str(request.POST.get("return_to") or f"/imports/{kind}/").strip()
        if not return_to.startswith("/imports/"):
            return_to = f"/imports/{kind}/"
        action = str(request.POST.get("action") or "approve").strip()
        try:
            imported_contribution_id = update_bank_movement_from_form(kind, movement_id, request.POST)
            if action == "same_owner":
                messages.success(request, "Movimento classificado como mesma titularidade / origem interna.")
            elif action == "ignore":
                messages.success(request, "Movimento ignorado e preservado para auditoria.")
            elif imported_contribution_id:
                messages.success(request, f"Movimento confirmado e sincronizado com a contribuicao #{imported_contribution_id}.")
            else:
                messages.success(request, "Movimento confirmado com sucesso.")
            return redirect(return_to)
        except LegacyBankWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/imports/{kind}/movement/{movement_id}/")

    default_return_to = f"/imports/{kind}/"
    return_to = str(request.GET.get("return_to") or "").strip()
    if not return_to.startswith("/imports/"):
        return_to = default_return_to
    context = {
        "title": "Movimento bancario",
        "kind": kind,
        "lookup": request.GET.get("lookup", ""),
        "return_to": return_to,
    }
    try:
        context["detail"] = get_bank_movement_detail(
            kind=kind,
            movement_id=movement_id,
            lookup=context["lookup"],
        )
        if context["detail"] and context["return_to"] == default_return_to:
            context["return_to"] = context["detail"]["lot_url"]
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/movement_detail.html", context)
