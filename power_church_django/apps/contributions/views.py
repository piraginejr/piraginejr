from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from pathlib import Path

from power_church_django.services.legacy_write import (
    LegacyWriteError,
    apply_envelope_profile_update,
    backfill_envelope_profile_updates,
    create_contribution,
    create_envelope_contribution_batch,
    create_envelope_image_lot,
    create_manual_contribution_batch,
    create_frequentador_from_contributor,
    create_receipt,
    envelope_upload_root,
    ignore_envelope_profile_update,
    ignore_pending_envelope,
    launch_pending_envelope,
    link_contributor_to_person_by_id,
    split_contribution,
    update_contribution,
    update_launched_envelope,
)
from power_church_django.services.legacy import (
    LegacyDatabaseError,
    envelope_contribution_context,
    envelope_lot_form_context,
    get_envelope_detail,
    get_envelope_lot_detail,
    get_next_pending_envelope_id,
    get_contribution_detail,
    get_contributor_detail,
    get_receipt_detail,
    list_envelopes,
    list_contributions,
    list_contributors,
    list_receipts,
    launched_envelope_edit_context,
    manual_contribution_context,
    new_contribution_context,
    pending_envelope_contribution_context,
    person_statement_data,
    lookup_envelope_people,
    receipt_new_context,
    split_contribution_context,
)


def _actor(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return str(user.username)
    return "django"


def index(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Contribuicoes",
        "q": request.GET.get("q", ""),
        "competencia": request.GET.get("competencia", ""),
        "status": request.GET.get("status", ""),
    }
    try:
        list_limit = 5000 if context["competencia"] or context["q"] or context["status"] else 300
        context["contributions"] = list_contributions(
            q=context["q"],
            competencia=context["competencia"],
            status=context["status"],
            limit=list_limit,
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/list.html", context)


def new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        person_id = request.POST.get("pessoa_id", "")
        try:
            contribution_id = create_contribution(request.POST, actor=_actor(request))
            messages.success(request, f"Contribuicao #{contribution_id} registrada com auditoria.")
            return redirect(f"/contributions/{contribution_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/new/?person_id={person_id}")

    person_id = int(request.GET.get("person_id") or 0)
    context = {"title": "Nova contribuicao", "person_id": person_id}
    try:
        context["form_data"] = new_contribution_context(person_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/form.html", context)


def manual_batch(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            contribution_ids = create_manual_contribution_batch(request.POST, actor=_actor(request))
            messages.success(request, f"{len(contribution_ids)} contribuicao(oes) registrada(s) com auditoria.")
            return redirect("/contributions/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/manual/")

    context = {"title": "Lancamento manual assistido"}
    try:
        context["form_data"] = manual_contribution_context()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/manual_batch.html", context)


def envelopes(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Envelopes de contribuicao",
        "q": request.GET.get("q", ""),
        "competencia": request.GET.get("competencia", ""),
    }
    try:
        context["envelopes"] = list_envelopes(
            q=context["q"],
            competencia=context["competencia"],
            limit=1000 if context["q"] or context["competencia"] else 300,
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/envelopes.html", context)


def envelope_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            result = create_envelope_contribution_batch(
                request.POST,
                request.FILES.get("imagem_envelope"),
                actor=_actor(request),
            )
            messages.success(
                request,
                f"Envelope #{result['envelope_id']} registrado com {len(result['contribution_ids'])} linha(s) e imagem arquivada.",
            )
            return redirect(f"/contributions/envelopes/{result['envelope_id']}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/envelopes/new/")

    context = {"title": "Novo envelope"}
    try:
        person_id = int(request.GET.get("person_id") or 0)
        context["form_data"] = envelope_contribution_context(person_id=person_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/envelope_form.html", context)


def envelope_lot_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            result = create_envelope_image_lot(
                request.POST,
                request.FILES.getlist("imagens_envelope"),
                actor=_actor(request),
            )
            duplicates = len(result.get("duplicates") or [])
            suffix = f" ({duplicates} duplicado(s) marcados para revisao)" if duplicates else ""
            messages.success(
                request,
                f"Lote de envelopes criado com {len(result.get('envelope_ids') or [])} arquivo(s){suffix}.",
            )
            return redirect(f"/contributions/envelopes/lots/{result['lot_id']}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/envelopes/lots/new/")

    context = {"title": "Criar lote de envelopes"}
    try:
        context["form_data"] = envelope_lot_form_context()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/envelope_lot_form.html", context)


def envelope_lot_detail(request: HttpRequest, lot_id: int) -> HttpResponse:
    context = {"title": "Lote de envelopes"}
    try:
        context["lot"] = get_envelope_lot_detail(lot_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    if not context.get("lot") and not context.get("error"):
        raise Http404("Lote de envelopes nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_lot_detail.html", context)


def envelope_lot_next(request: HttpRequest, lot_id: int) -> HttpResponse:
    try:
        envelope_id = get_next_pending_envelope_id(lot_id)
    except LegacyDatabaseError as exc:
        messages.error(request, str(exc))
        return redirect(f"/contributions/envelopes/lots/{lot_id}/")
    if not envelope_id:
        messages.info(request, "Nao ha envelopes pendentes neste lote.")
        return redirect(f"/contributions/envelopes/lots/{lot_id}/")
    return redirect(f"/contributions/envelopes/{envelope_id}/launch/")


def envelope_launch(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method == "POST":
        lot_id = int(request.POST.get("lote_id") or 0)
        try:
            result = launch_pending_envelope(envelope_id, request.POST, actor=_actor(request))
            lot_id = int(result["lot_id"])
            next_id = get_next_pending_envelope_id(lot_id)
            messages.success(
                request,
                f"Envelope #{envelope_id} lancado com {len(result['contribution_ids'])} contribuicao(oes).",
            )
            if next_id:
                return redirect(f"/contributions/envelopes/{next_id}/launch/")
            return redirect(f"/contributions/envelopes/lots/{lot_id}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/envelopes/{envelope_id}/launch/")

    context = {"title": "Digitar envelope"}
    try:
        context["form_data"] = pending_envelope_contribution_context(envelope_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    if not context.get("form_data") and not context.get("error"):
        raise Http404("Envelope pendente nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_form.html", context)


def envelope_edit(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            result = update_launched_envelope(envelope_id, request.POST, actor=_actor(request))
            messages.success(
                request,
                f"Envelope #{envelope_id} corrigido com {len(result['contribution_ids'])} linha(s) ativa(s); versao anterior preservada na auditoria.",
            )
            return redirect(f"/contributions/envelopes/{envelope_id}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/envelopes/{envelope_id}/edit/")

    context = {"title": "Editar envelope"}
    try:
        context["form_data"] = launched_envelope_edit_context(envelope_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    if not context.get("form_data") and not context.get("error"):
        raise Http404("Envelope lancado nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_form.html", context)


def envelope_ignore(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(f"/contributions/envelopes/{envelope_id}/launch/")
    lot_id = int(request.POST.get("lote_id") or 0)
    try:
        ignore_pending_envelope(envelope_id, request.POST.get("justificativa_ignorar", ""), actor=_actor(request))
        messages.success(request, f"Envelope #{envelope_id} ignorado com justificativa.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
        return redirect(f"/contributions/envelopes/{envelope_id}/launch/")
    if lot_id:
        return redirect(f"/contributions/envelopes/lots/{lot_id}/")
    return redirect("/contributions/envelopes/")


def envelope_lookup(request: HttpRequest) -> JsonResponse:
    phone = request.GET.get("phone", "")
    address = request.GET.get("address", "")
    try:
        payload = lookup_envelope_people(phone=phone, address=address)
    except LegacyDatabaseError as exc:
        return JsonResponse({"ok": False, "error": str(exc), "phone_matches": [], "address_matches": []}, status=500)
    return JsonResponse({"ok": True, **payload})


def envelope_profile_update_apply(request: HttpRequest, update_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    envelope_id = int(request.POST.get("envelope_id") or 0)
    try:
        result = apply_envelope_profile_update(update_id, actor=_actor(request))
        envelope_id = int(result["envelope_id"])
        messages.success(request, f"Telefone aplicado na ficha da pessoa vinculada ao envelope #{envelope_id}.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    if envelope_id:
        return redirect(f"/contributions/envelopes/{envelope_id}/")
    return redirect("/contributions/envelopes/")


def envelope_profile_update_ignore(request: HttpRequest, update_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    envelope_id = int(request.POST.get("envelope_id") or 0)
    try:
        result = ignore_envelope_profile_update(update_id, actor=_actor(request))
        envelope_id = int(result["envelope_id"])
        messages.success(request, f"Pendencia cadastral do envelope #{envelope_id} marcada como revisada.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    if envelope_id:
        return redirect(f"/contributions/envelopes/{envelope_id}/")
    return redirect("/contributions/envelopes/")


def envelope_profile_update_backfill(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    try:
        result = backfill_envelope_profile_updates(actor=_actor(request))
        messages.success(
            request,
            f"Reprocessamento concluido: {result['scanned']} envelope(s) verificado(s) e {result['created']} pendencia(s) criada(s).",
        )
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    return redirect("/contributions/envelopes/")


def envelope_detail(request: HttpRequest, envelope_id: int) -> HttpResponse:
    context = {"title": "Envelope"}
    try:
        context["detail"] = get_envelope_detail(envelope_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    if not context.get("detail") and not context.get("error"):
        raise Http404("Envelope nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_detail.html", context)


def envelope_image(request: HttpRequest, envelope_id: int) -> HttpResponse:
    detail = get_envelope_detail(envelope_id)
    if not detail or not detail.get("has_image"):
        raise Http404("Imagem nao encontrada.")
    root = Path(envelope_upload_root()).resolve()
    # A leitura do caminho real fica no banco legado; validamos que ele continua dentro da pasta de envelopes.
    from power_church_django.services.legacy import connect_legacy

    with connect_legacy() as conn:
        row = conn.execute("SELECT caminho_imagem, imagem_content_type FROM envelopes WHERE id = ? AND ativo = 1", (envelope_id,)).fetchone()
    if row is None:
        raise Http404("Imagem nao encontrada.")
    path = Path(str(row["caminho_imagem"] or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Http404("Imagem fora da pasta de envelopes.") from exc
    if not path.exists():
        raise Http404("Arquivo do envelope nao encontrado.")
    content_type = str(row["imagem_content_type"] or "") or None
    return FileResponse(path.open("rb"), content_type=content_type)


def detail(request: HttpRequest, contribution_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            update_contribution(contribution_id, request.POST, actor=_actor(request))
            messages.success(request, f"Contribuicao #{contribution_id} ajustada com auditoria.")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        return redirect(f"/contributions/{contribution_id}/")

    context = {"title": "Contribuicao"}
    try:
        context["detail"] = get_contribution_detail(contribution_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/detail.html", context)


def split(request: HttpRequest, contribution_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            contribution_ids = split_contribution(contribution_id, request.POST, actor=_actor(request))
            messages.success(request, f"Rateio salvo com {len(contribution_ids)} linha(s) e soma conferida.")
            return redirect(f"/contributions/{contribution_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/{contribution_id}/split/")

    context = {"title": "Ratear contribuicao"}
    try:
        context["form_data"] = split_contribution_context(contribution_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/split.html", context)


def person_statement(request: HttpRequest, person_id: int) -> HttpResponse:
    context = {
        "title": "Extrato de contribuicoes",
        "year": request.GET.get("year", ""),
        "competencia": request.GET.get("competencia", ""),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
        "type_ids": [int(value) for value in request.GET.getlist("tipo_id") if str(value).isdigit()],
    }
    try:
        context["statement"] = person_statement_data(
            person_id,
            year=context["year"],
            competencia=context["competencia"],
            date_start=context["date_start"],
            date_end=context["date_end"],
            type_ids=context["type_ids"],
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/statement.html", context)


def receipts(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Recibos",
        "q": request.GET.get("q", ""),
        "person_id": int(request.GET.get("person_id") or 0),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
    }
    try:
        context["receipts"] = list_receipts(
            q=context["q"],
            person_id=context["person_id"],
            date_start=context["date_start"],
            date_end=context["date_end"],
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/receipts/list.html", context)


def receipt_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        person_id = request.POST.get("pessoa_id", "")
        try:
            receipt_id = create_receipt(request.POST, actor=_actor(request))
            messages.success(request, f"Recibo #{receipt_id} gerado com auditoria.")
            return redirect(f"/receipts/{receipt_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/receipts/new/?person_id={person_id}")
    person_id = int(request.GET.get("person_id") or 0)
    context = {
        "title": "Novo recibo",
        "person_id": person_id,
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
    }
    try:
        context["form_data"] = receipt_new_context(person_id, date_start=context["date_start"], date_end=context["date_end"])
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/receipts/form.html", context)


def receipt_detail(request: HttpRequest, receipt_id: int) -> HttpResponse:
    context = {"title": "Recibo"}
    try:
        context["detail"] = get_receipt_detail(receipt_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/receipts/detail.html", context)


def contributors(request: HttpRequest) -> HttpResponse:
    selected_tags = [value for value in request.GET.getlist("tag") if value.strip()]
    context = {
        "title": "Contribuintes auxiliares",
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
        "tipo": request.GET.get("tipo", ""),
        "mode": request.GET.get("mode", "todos") or "todos",
        "section": request.GET.get("section", ""),
        "selected_tags": selected_tags,
    }
    try:
        list_limit = 10000 if context["q"] or context["mode"] != "todos" or selected_tags or context["section"] else 500
        context["contributors"] = list_contributors(
            q=context["q"],
            status=context["status"],
            tipo=context["tipo"],
            mode=context["mode"],
            tags=selected_tags,
            section=context["section"],
            limit=list_limit,
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributors/list.html", context)


def contributor_detail(request: HttpRequest, contributor_id: int) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "link_person":
                person_id = int(request.POST.get("person_id") or 0)
                link_contributor_to_person_by_id(contributor_id, person_id, actor=_actor(request))
                messages.success(request, "Contribuinte vinculado a pessoa com auditoria.")
            elif action == "create_frequentador":
                family_person_id = int(request.POST.get("family_person_id") or 0)
                person_id = create_frequentador_from_contributor(contributor_id, family_person_id=family_person_id, actor=_actor(request))
                messages.success(request, f"Frequentador #{person_id} criado e vinculado com auditoria.")
                return redirect(f"/people/{person_id}/")
            else:
                messages.error(request, "Acao de contribuinte nao reconhecida.")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect(f"/contributors/{contributor_id}/")

    context = {"title": "Ficha do contribuinte auxiliar"}
    try:
        context["detail"] = get_contributor_detail(contributor_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributors/detail.html", context)
