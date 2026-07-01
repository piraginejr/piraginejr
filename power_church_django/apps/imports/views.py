from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from power_church_django.services.access_control import module_permission_required
from power_church_core.banking import STATEMENT_LAYOUT_LABELS
from .services import (
    LegacyBankWriteError,
    PDF_PROVIDER_MODES,
    close_statement_lot_postgres_native,
    create_statement_lot_postgres_native,
    dashboard_summary_postgres,
    cent_rules_data_postgres,
    get_statement_movement_detail_from_snapshot,
    get_statement_lot_detail_from_snapshot,
    list_import_lots_postgres,
    prepare_statement_lot_postgres_native,
    reprocess_statement_lot_postgres_native,
    save_cent_rule_from_form_postgres,
    update_statement_movement_postgres_native,
)


def _actor(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return str(user.username)
    return "django"


def _native_statement_backend(backend: str) -> bool:
    return str(backend or "").strip() == "postgres_nativo"


@module_permission_required("view_dashboard")
def dashboard(request: HttpRequest) -> HttpResponse:
    context = {"title": "Power Church Django", "layouts": STATEMENT_LAYOUT_LABELS}
    try:
        context["summary"] = dashboard_summary_postgres()
    except ValueError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/dashboard.html", context)


@module_permission_required("view_imports", "manage_imports")
def index(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        actor = _actor(request)
        upload = request.FILES.get("extrato_pdf")
        if upload is None:
            messages.error(request, "Selecione um PDF bancario antes de importar.")
            return redirect("/imports/")
        payload = b"".join(upload.chunks())
        import_kind = str(request.POST.get("import_kind") or "statement").strip()
        storage_backend = str(request.POST.get("storage_backend") or "postgres_native").strip().lower()
        pdf_provider_mode = str(request.POST.get("pdf_provider_mode") or "compare_pymupdf").strip()
        provider_label = next(
            (
                item["label"]
                for item in PDF_PROVIDER_MODES
                if item["value"] == pdf_provider_mode
            ),
            "Comparar Swift x PyMuPDF antes de gravar",
        )
        if import_kind == "pix_sicoob":
            messages.error(
                request,
                "Nesta versao, o caminho oficial e o extrato bancario completo. O PIX isolado ficou fora da operacao para evitar lacunas de TED, transferencia e outros recebimentos.",
            )
            return redirect("/imports/")
        try:
            layout_code = str(request.POST.get("layout_code") or "SICOOB_CONTA_CORRENTE").strip().upper()
            if storage_backend != "postgres_native":
                messages.info(
                    request,
                    "O caminho legado de novos extratos foi desligado nesta fase. O lote sera criado diretamente no Postgres nativo.",
                )
            native_lot = create_statement_lot_postgres_native(
                filename=upload.name,
                payload=payload,
                layout_code=layout_code,
                pdf_provider="pymupdf" if pdf_provider_mode in {"compare_pymupdf", "pymupdf"} else "swift_pdfkit",
                comparison_ok=pdf_provider_mode != "compare_pymupdf",
                comparison_note=(
                    "Lote operacional criado diretamente no Postgres nativo."
                    if pdf_provider_mode != "compare_pymupdf"
                    else "Lote operacional criado no Postgres nativo com foco em portabilidade."
                ),
            )
            layout_label = STATEMENT_LAYOUT_LABELS.get(layout_code, layout_code)
            messages.success(
                request,
                f"Lote de extrato #{native_lot.id} ({layout_label}) criado diretamente no Postgres. "
                f"Movimentos carregados: {int(native_lot.movement_count or 0)}. Motor PDF: {provider_label}.",
            )
            return redirect(f"/imports/statement/{int(native_lot.id)}/?backend=postgres_nativo&status=pendencias")
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
        context["lots"] = list_import_lots_postgres()
    except ValueError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/list.html", context)


@module_permission_required("view_imports", "manage_imports")
def cent_rules(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            saved_id = save_cent_rule_from_form_postgres(request.POST)
            messages.success(request, f"Regra de centavos #{saved_id} salva no Postgres nativo.")
            return redirect(f"/imports/rules/?edit_rule_id={saved_id}")
        except (LegacyBankWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("/imports/rules/")

    context = {"title": "Regras por centavos"}
    try:
        context["rules_data"] = cent_rules_data_postgres(edit_rule_id=int(request.GET.get("edit_rule_id") or 0))
    except ValueError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/rules.html", context)


@module_permission_required("view_imports", ("manage_imports", "operate_bank_review"))
def lot_detail(request: HttpRequest, kind: str, lot_id: int) -> HttpResponse:
    kind = "pix" if kind == "pix" else "statement"
    backend = str(request.POST.get("backend") or request.GET.get("backend") or "").strip()
    if kind == "pix":
        messages.error(
            request,
            "Nesta versao, o caminho oficial e o extrato bancario completo. O modulo PIX isolado ficou fora da operacao.",
        )
        return redirect("/imports/")
    if request.method == "POST":
        actor = _actor(request)
        action = str(request.POST.get("action") or "").strip()
        status = str(request.POST.get("status") or request.GET.get("status") or "").strip()
        target = f"/imports/{kind}/{lot_id}/"
        if _native_statement_backend(backend):
            target = f"{target}?backend=postgres_nativo"
        if status:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}status={status}"
        try:
            if kind == "statement" and not _native_statement_backend(backend):
                raise ValueError(
                    "Este lote historico ficou somente para consulta. Novas auditorias e reprocessamentos acontecem apenas no fluxo Postgres nativo."
                )
            if action == "reprocess":
                updated = reprocess_statement_lot_postgres_native(lot_id)
                preparation = prepare_statement_lot_postgres_native(lot_id, actor=actor)
                messages.success(
                    request,
                    f"Lote nativo reprocessado. {updated} movimento(s) foram recalculados e "
                    f"{int(preparation.get('reviewed', 0) or 0)} item(ns) ficaram prontos para auditoria no Postgres.",
                )
            elif action == "prepare" and kind == "statement":
                result = prepare_statement_lot_postgres_native(lot_id, actor=actor)
                messages.success(
                    request,
                    f"Lote nativo preparado para auditoria. {int(result.get('reviewed', 0) or 0)} movimento(s) "
                    f"foram classificados no Postgres, sem depender do legado.",
                )
            elif action == "approve_movement":
                movement_id = int(request.POST.get("movement_id") or 0)
                if not movement_id:
                    raise LegacyBankWriteError("Movimento nao informado para confirmacao.")
                imported_contribution_id = update_statement_movement_postgres_native(movement_id, request.POST, actor=actor)
                if imported_contribution_id:
                    messages.success(request, f"Movimento #{movement_id} confirmado no lote e sincronizado com a contribuicao #{imported_contribution_id}.")
                else:
                    messages.success(request, f"Movimento #{movement_id} confirmado no lote.")
            elif action == "close":
                result = close_statement_lot_postgres_native(lot_id, actor=actor)
                messages.success(
                    request,
                    f"Lote encerrado manualmente pelo operador. {result.get('importados', 0)} movimento(s) sincronizados e "
                    f"{result.get('movidos_contribuintes', 0)} pendencia(s) preservadas na central de contribuintes.",
                )
            else:
                messages.error(request, "Acao de lote nao reconhecida.")
        except (LegacyBankWriteError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect(target)

    context = {
        "title": "Detalhe do lote",
        "kind": kind,
        "status": request.GET.get("status", ""),
        "backend": backend,
    }
    try:
        context["detail"] = get_statement_lot_detail_from_snapshot(lot_id=lot_id, status=context["status"], backend=backend)
    except ValueError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/lot_detail.html", context)


@module_permission_required("view_imports", ("manage_imports", "operate_bank_review"))
def movement_detail(request: HttpRequest, kind: str, movement_id: int) -> HttpResponse:
    kind = "pix" if kind == "pix" else "statement"
    backend = str(request.POST.get("backend") or request.GET.get("backend") or "").strip()
    if kind == "pix":
        messages.error(
            request,
            "Nesta versao, o caminho oficial e o extrato bancario completo. O modulo PIX isolado ficou fora da operacao.",
        )
        return redirect("/imports/")
    if request.method == "POST":
        return_to = str(request.POST.get("return_to") or f"/imports/{kind}/").strip()
        if not return_to.startswith("/imports/"):
            return_to = f"/imports/{kind}/"
        action = str(request.POST.get("action") or "approve").strip()
        try:
            if kind == "statement" and not _native_statement_backend(backend):
                raise ValueError(
                    "Este movimento historico ficou somente para consulta. A auditoria operacional segue apenas no fluxo Postgres nativo."
                )
            imported_contribution_id = update_statement_movement_postgres_native(movement_id, request.POST, actor=_actor(request))
            if action == "same_owner":
                messages.success(request, "Movimento classificado como mesma titularidade / origem interna.")
            elif action == "ignore":
                messages.success(request, "Movimento ignorado e preservado para auditoria.")
            elif imported_contribution_id:
                messages.success(request, f"Movimento confirmado e sincronizado com a contribuicao #{imported_contribution_id}.")
            else:
                messages.success(request, "Movimento confirmado com sucesso.")
            return redirect(return_to)
        except (LegacyBankWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            target = f"/imports/{kind}/movement/{movement_id}/"
            if backend == "postgres_nativo":
                target = f"{target}?backend=postgres_nativo"
            return redirect(target)

    default_return_to = f"/imports/{kind}/"
    return_to = str(request.GET.get("return_to") or "").strip()
    if not return_to.startswith("/imports/"):
        return_to = default_return_to
    context = {
        "title": "Movimento bancario",
        "kind": kind,
        "lookup": request.GET.get("lookup", ""),
        "return_to": return_to,
        "backend": backend,
    }
    try:
        context["detail"] = get_statement_movement_detail_from_snapshot(
            movement_id=movement_id,
            lookup=context["lookup"],
            backend=backend,
        )
        if context["detail"] and context["return_to"] == default_return_to:
            context["return_to"] = context["detail"]["lot_url"]
    except ValueError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/imports/movement_detail.html", context)
