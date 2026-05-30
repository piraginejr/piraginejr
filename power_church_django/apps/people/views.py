from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from power_church_django.services.data_exchange import (
    dataset_download_response,
    people_export_dataset,
    people_export_form_context,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.family_profiles import update_household_profile
from power_church_django.services.legacy import (
    LegacyDatabaseError,
    family_registry_dashboard,
    get_people_import_lot_detail,
    get_person_detail,
    list_secure_people_trash,
    list_people,
    people_import_dashboard,
    search_people_for_relationship,
)
from power_church_django.services.legacy_write import (
    LegacyWriteError,
    PERSON_MARITAL_STATUS_OPTIONS,
    PERSON_SEX_OPTIONS,
    PERSON_STATUS_OPTIONS,
    create_family_group_relationships,
    create_person,
    create_person_relationship,
    deactivate_person_relationship,
    empty_person_form,
    get_person_form_initial,
    import_people_from_upload,
    person_form_payload,
    purge_secure_person_trash,
    soft_delete_person,
    suppress_family_group_suggestions,
    suppress_family_suggestion,
    sync_person_household_relationships,
    update_person_relationship,
    update_person,
    validate_person_cpf_for_form,
    validate_person_email_for_form,
)
from power_church_django.services.photos import (
    PhotoUploadError,
    find_member_photo,
    member_photo_url,
    photo_content_type,
    save_member_photo_payload,
    uploaded_photo_payload,
)


def index(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Pessoas",
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
        "can_delete_people": _can_delete_people(request),
        "people": None,
        "export_options": people_export_form_context(),
    }
    try:
        context["people"] = list_people(q=context["q"], status=context["status"])
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/list.html", context)


def export(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "")
    status = request.GET.get("status", "")
    preset = request.GET.get("preset", "")
    columns = [value for value in request.GET.getlist("column") if value.strip()]
    export_format = request.GET.get("format", "xlsx")
    try:
        export_data = people_export_dataset(q=q, status=status, columns=columns, preset=preset)
    except LegacyDatabaseError as exc:
        messages.error(request, str(exc))
        return redirect("/people/")
    try:
        record_django_audit_event(
            actor=_actor_label(request),
            action="exportar_pessoas_django",
            table_name="pessoas",
            source="django_import_export",
            summary=f"Exportacao de pessoas em {export_format.upper()}",
            after={
                "q": q,
                "status": status,
                "formato": export_format,
                "preset": export_data["preset"],
                "colunas": export_data["columns"],
                "quantidade_colunas": len(export_data["columns"]),
                "total_filtrado": export_data["total"],
                "registros_exportados": export_data["shown"],
            },
        )
    except Exception:
        pass
    return dataset_download_response(export_data["dataset"], export_format, "pessoas")


def trash(request: HttpRequest) -> HttpResponse:
    if not _can_delete_people(request):
        messages.error(request, "Entre com um usuario autorizado para auditar exclusoes.")
        return redirect("/people/")
    context = {"title": "Lixeira segura de pessoas", "can_purge_people": _can_purge_people(request)}
    try:
        context["trash"] = list_secure_people_trash()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/trash.html", context)


def purge_trash(request: HttpRequest, trash_id: int) -> HttpResponse:
    if not _can_purge_people(request):
        messages.error(request, "Somente superusuario pode executar a purga final.")
        return redirect("/people/trash/")
    try:
        trash_data = list_secure_people_trash(limit=10000)
    except LegacyDatabaseError as exc:
        messages.error(request, str(exc))
        return redirect("/people/trash/")
    item = next((row for row in trash_data["items"] if int(row["id"] or 0) == int(trash_id or 0)), None)
    if item is None:
        messages.error(request, "Registro da lixeira nao encontrado.")
        return redirect("/people/trash/")
    if request.method == "POST":
        password = str(request.POST.get("password") or "")
        confirmation = normalize_delete_confirmation(request.POST.get("confirmation"))
        reason = str(request.POST.get("reason") or "").strip()
        expected = normalize_delete_confirmation(item["nome"])
        if not request.user.check_password(password):
            messages.error(request, "Senha invalida. A purga nao foi executada.")
            return redirect(f"/people/trash/{trash_id}/purge/")
        if confirmation != expected:
            messages.error(request, "Digite exatamente o nome exibido na lixeira para confirmar a purga.")
            return redirect(f"/people/trash/{trash_id}/purge/")
        try:
            person_id = purge_secure_person_trash(trash_id, reason, actor=_actor_label(request))
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/people/trash/{trash_id}/purge/")
        messages.success(request, f"Purga segura concluida para a ficha original #{person_id}.")
        return redirect("/people/trash/")
    return render(
        request,
        "power_church_django/people/purge_confirm.html",
        {
            "title": "Purga segura",
            "item": item,
        },
    )


def validate_field(request: HttpRequest) -> JsonResponse:
    field = str(request.GET.get("field") or "").strip()
    value = request.GET.get("value") or ""
    ignore_person_id = int(request.GET.get("person_id") or 0)
    if field == "cpf":
        result = validate_person_cpf_for_form(value, ignore_person_id=ignore_person_id)
    elif field == "email_principal":
        result = validate_person_email_for_form(value)
    else:
        result = {"ok": False, "message": "Campo nao reconhecido.", "normalized": ""}
    return JsonResponse(result)


def search(request: HttpRequest) -> JsonResponse:
    q = request.GET.get("q") or ""
    person_id = int(request.GET.get("person_id") or 0)
    try:
        results = search_people_for_relationship(person_id, q=q)
    except LegacyDatabaseError:
        results = []
    return JsonResponse({"results": results})


def families(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        section = request.POST.get("section", "audit")
        cep = request.POST.get("cep", "")
        mode = request.POST.get("mode", "all")
        category = request.POST.get("category", "all")
        q = request.POST.get("q", "")
        review = request.POST.get("review", "all")
        if request.POST.get("family_profile_action") == "update_household_profile":
            try:
                families_data = family_registry_dashboard(
                    q=q,
                    cep=cep,
                    section=section,
                    mode=mode,
                    review=review,
                    category=category,
                )
                person_ids_blob = str(request.POST.get("person_ids") or "")
                target_group = next(
                    (
                        group
                        for group in families_data.get("organized", {}).get("items", [])
                        if str(group.get("person_ids") or "") == person_ids_blob
                    ),
                    None,
                )
                if not target_group:
                    raise LegacyWriteError("Nao foi possivel localizar o nucleo domiciliar para atualizar a identidade da familia.")
                profile = update_household_profile(
                    person_ids=[int(value) for value in person_ids_blob.split(",") if value.strip().isdigit()],
                    people=target_group.get("people") or [],
                    head_person_id=int(request.POST.get("head_person_id") or 0),
                    display_name_override=request.POST.get("display_name_override", ""),
                    actor=_actor_label(request),
                )
                messages.success(request, f"Identidade familiar atualizada para {profile['display_name_effective']}.")
            except (LegacyDatabaseError, LegacyWriteError, ValueError) as exc:
                messages.error(request, str(exc))
            query = urlencode(
                {
                    "section": section,
                    "cep": cep,
                    "mode": mode,
                    "category": category,
                    "q": q,
                    "review": review,
                }
            )
            return redirect(f"/people/families/?{query}")
        selected_groups = []
        action = request.POST.get("bulk_action") or "create"
        single_group = request.POST.get("single_person_ids", "") or request.POST.get("single_suppress_person_ids", "")
        if request.POST.get("single_suppress_person_ids"):
            action = "suppress"
        if single_group:
            selected_groups = [single_group]
        else:
            selected_groups = [value for value in request.POST.getlist("person_ids") if value]
        try:
            if not selected_groups:
                raise LegacyWriteError("Marque pelo menos uma hipotese ou familia domiciliar para aplicar a acao.")
            changed = 0
            for group_ids in selected_groups:
                if action == "suppress":
                    changed += suppress_family_group_suggestions(group_ids, actor=_actor_label(request))
                else:
                    changed += create_family_group_relationships(group_ids, actor=_actor_label(request))
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        else:
            if action == "suppress":
                messages.success(
                    request,
                    f"Sugestao familiar ignorada: {changed} par(es) retirado(s) da fila em {len(selected_groups)} grupo(s).",
                )
            else:
                messages.success(
                    request,
                    f"Familia domiciliar criada: {changed} relacao(oes) nova(s) em {len(selected_groups)} grupo(s).",
                )
        query = urlencode(
            {
                "section": section,
                "cep": cep,
                "mode": mode,
                "category": category,
                "q": q,
                "review": review,
            }
        )
        return redirect(f"/people/families/?{query}")
    context = {
        "title": "Familias domiciliares",
        "q": request.GET.get("q", ""),
        "cep": request.GET.get("cep", ""),
        "section": request.GET.get("section", "organized"),
        "mode": request.GET.get("mode", "all"),
        "review": request.GET.get("review", "all"),
        "category": request.GET.get("category", "all"),
    }
    try:
        context["families"] = family_registry_dashboard(
            q=context["q"],
            cep=context["cep"],
            section=context["section"],
            mode=context["mode"],
            review=context["review"],
            category=context["category"],
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/families.html", context)


def photo(request: HttpRequest, person_id: int) -> HttpResponse:
    detail = get_person_detail(person_id)
    if not detail:
        raise Http404("Pessoa nao encontrada")
    person = detail["person"]
    path = find_member_photo(person_id, person["cpf"], person["nome"])
    if path is None:
        raise Http404("Foto nao encontrada")
    return FileResponse(path.open("rb"), content_type=photo_content_type(path))


def detail(request: HttpRequest, person_id: int) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action", "")
        success_message = "Relacao familiar registrada com trilha de auditoria."
        try:
            if action == "create_family_relationship":
                create_person_relationship(person_id, request.POST, actor=_actor_label(request))
                success_message = "Relacao familiar registrada com trilha de auditoria."
            elif action == "suppress_family_suggestion":
                suppress_family_suggestion(
                    person_id,
                    int(request.POST.get("related_person_id") or 0),
                    actor=_actor_label(request),
                )
                success_message = "Sugestao familiar ignorada e retirada da fila por endereco."
            elif action == "update_family_relationship":
                update_person_relationship(
                    person_id,
                    int(request.POST.get("relationship_id") or 0),
                    request.POST,
                    actor=_actor_label(request),
                )
                success_message = "Relacao familiar atualizada com trilha de auditoria."
            elif action == "deactivate_family_relationship":
                deactivate_person_relationship(
                    person_id,
                    int(request.POST.get("relationship_id") or 0),
                    actor=_actor_label(request),
                )
                success_message = "Relacao familiar removida e protegida contra recriacao automatica."
            elif action == "sync_family_relationships":
                summary = sync_person_household_relationships(person_id, actor=_actor_label(request))
                messages.success(
                    request,
                    "Sincronizacao de familias domiciliares concluida: "
                    f"{summary['created']} relacao(oes) criada(s) e "
                    f"{summary['deactivated']} relacao(oes) automatica(s) removida(s).",
                )
                return redirect(f"/people/{person_id}/")
            else:
                raise LegacyWriteError("Acao de ficha nao reconhecida.")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, success_message)
        return redirect(f"/people/{person_id}/")

    context = {
        "title": "Ficha da pessoa",
        "can_view_finance": _local_or_has_permission(request, "view_contributions"),
        "can_manage_finance": _local_or_has_permission(request, "manage_contributions"),
        "can_delete_people": _can_delete_people(request),
    }
    try:
        context["detail"] = get_person_detail(person_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/detail.html", context)


def delete(request: HttpRequest, person_id: int) -> HttpResponse:
    try:
        detail_data = get_person_detail(person_id)
    except LegacyDatabaseError as exc:
        messages.error(request, str(exc))
        return redirect("/people/")
    if not detail_data:
        messages.error(request, "Pessoa nao encontrada.")
        return redirect("/people/")
    if not _can_delete_people(request):
        messages.error(request, "Entre com um usuario autorizado para excluir fichas.")
        return redirect(f"/people/{person_id}/")
    if request.method == "POST":
        password = str(request.POST.get("password") or "")
        confirmation = normalize_delete_confirmation(request.POST.get("confirmation"))
        reason = str(request.POST.get("reason") or "").strip()
        expected = normalize_delete_confirmation(detail_data["person"]["nome"])
        if not request.user.check_password(password):
            messages.error(request, "Senha invalida. A ficha nao foi excluida.")
            return redirect(f"/people/{person_id}/delete/")
        if confirmation != expected:
            messages.error(request, "Digite exatamente o nome da pessoa para confirmar a exclusao.")
            return redirect(f"/people/{person_id}/delete/")
        try:
            trash_id = soft_delete_person(person_id, reason, actor=_actor_label(request))
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/people/{person_id}/delete/")
        messages.success(request, f"Ficha retirada do cadastro operacional. Registro seguro #{trash_id}.")
        return redirect("/people/")
    return render(
        request,
        "power_church_django/people/delete_confirm.html",
        {
            "title": "Excluir ficha",
            "detail": detail_data,
        },
    )


def new(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Nova pessoa",
        "mode": "new",
        "status_options": PERSON_STATUS_OPTIONS,
        "sex_options": PERSON_SEX_OPTIONS,
        "marital_status_options": PERSON_MARITAL_STATUS_OPTIONS,
        "form": empty_person_form(),
    }
    if request.method == "POST":
        context["form"] = person_form_payload(request.POST)
        try:
            photo_payload = uploaded_photo_payload(request.FILES.get("foto"))
        except PhotoUploadError as exc:
            context["error"] = str(exc)
            return render(request, "power_church_django/people/form.html", context)
        try:
            person_id = create_person(context["form"], actor=_actor_label(request))
        except LegacyWriteError as exc:
            context["error"] = str(exc)
        else:
            _save_person_photo_if_present(request, person_id, context["form"], photo_payload)
            messages.success(request, "Ficha criada com trilha de auditoria.")
            return redirect(f"/people/{person_id}/")
    return render(request, "power_church_django/people/form.html", context)


def edit(request: HttpRequest, person_id: int) -> HttpResponse:
    initial = get_person_form_initial(person_id)
    if initial is None:
        return render(
            request,
            "power_church_django/people/form.html",
            {
                "title": "Editar pessoa",
                "mode": "edit",
                "status_options": PERSON_STATUS_OPTIONS,
                "sex_options": PERSON_SEX_OPTIONS,
                "marital_status_options": PERSON_MARITAL_STATUS_OPTIONS,
                "error": "Pessoa nao encontrada.",
            },
        )
    context = {
        "title": "Editar pessoa",
        "mode": "edit",
        "person_id": person_id,
        "status_options": PERSON_STATUS_OPTIONS,
        "sex_options": PERSON_SEX_OPTIONS,
        "marital_status_options": PERSON_MARITAL_STATUS_OPTIONS,
        "form": initial,
        "current_photo_url": member_photo_url(person_id, initial.get("cpf"), initial.get("nome")),
    }
    if request.method == "POST":
        context["form"] = person_form_payload(request.POST)
        try:
            photo_payload = uploaded_photo_payload(request.FILES.get("foto"))
        except PhotoUploadError as exc:
            context["error"] = str(exc)
            return render(request, "power_church_django/people/form.html", context)
        try:
            update_person(person_id, context["form"], actor=_actor_label(request))
        except LegacyWriteError as exc:
            context["error"] = str(exc)
        else:
            _save_person_photo_if_present(request, person_id, context["form"], photo_payload)
            messages.success(request, "Ficha atualizada com trilha de auditoria.")
            return redirect(f"/people/{person_id}/")
    return render(request, "power_church_django/people/form.html", context)


def imports(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        upload = request.FILES.get("planilha_xlsx")
        if upload is None:
            messages.error(request, "Selecione uma planilha .xlsx antes de importar.")
            return redirect("/people/imports/")
        payload = b"".join(upload.chunks())
        allow_duplicate_file = request.POST.get("allow_duplicate_file") == "1"
        try:
            summary = import_people_from_upload(
                upload.name,
                payload,
                allow_duplicate_file=allow_duplicate_file,
            )
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect("/people/imports/")
        lote_id = summary.get("lote_id")
        pendencies = summary.get("pendencias", 0)
        messages.success(request, f"Importacao concluida no lote #{lote_id} com {pendencies} pendencia(s) para auditoria.")
        return redirect(f"/people/imports/{lote_id}/" if lote_id else "/people/imports/")

    context = {"title": "Importacao de pessoas"}
    try:
        context["dashboard"] = people_import_dashboard()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/imports.html", context)


def import_lot(request: HttpRequest, lot_id: int) -> HttpResponse:
    context = {"title": "Auditoria da importacao de pessoas"}
    try:
        context["detail"] = get_people_import_lot_detail(lot_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/people/import_lot.html", context)


def _actor_label(request: HttpRequest) -> str:
    if request.user.is_authenticated:
        return f"django:{request.user.username}"
    return "django:operador_local"


def _local_or_has_permission(request: HttpRequest, codename: str) -> bool:
    if not request.user.is_authenticated:
        return True
    return request.user.is_superuser or request.user.has_perm(f"power_church.{codename}")


def _can_delete_people(request: HttpRequest) -> bool:
    if not request.user.is_authenticated:
        return False
    return request.user.is_superuser or request.user.has_perm("power_church.delete_people")


def _can_purge_people(request: HttpRequest) -> bool:
    return bool(request.user.is_authenticated and request.user.is_superuser)


def normalize_delete_confirmation(value: object) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _save_person_photo_if_present(
    request: HttpRequest,
    person_id: int,
    form: dict[str, str],
    photo_payload: dict[str, object] | None,
) -> None:
    if photo_payload is None:
        return
    try:
        saved_path = save_member_photo_payload(person_id, form.get("cpf"), form.get("nome"), photo_payload)
    except (PhotoUploadError, OSError) as exc:
        messages.warning(request, f"A ficha foi salva, mas a foto nao foi anexada: {exc}")
        return
    if saved_path is None:
        return
    try:
        record_django_audit_event(
            actor=_actor_label(request),
            action="upload_foto_pessoa_django",
            table_name="pessoas_fotos",
            record_id=person_id,
            source="django_people_form",
            summary="Foto da pessoa atualizada pelo formulario Django.",
            after={
                "arquivo": saved_path.name,
                "tamanho_bytes": int(photo_payload.get("size") or 0),
                "content_type": str(photo_payload.get("content_type") or ""),
            },
        )
    except Exception:
        return
