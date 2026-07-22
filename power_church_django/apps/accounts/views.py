from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission, User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from power_church_django.services.access_control import (
    access_control_snapshot,
    access_content_type,
    create_or_update_admin,
    ensure_access_control,
    MODULE_PERMISSIONS,
    module_permission_required,
)


@module_permission_required("manage_accounts")
def index(request: HttpRequest) -> HttpResponse:
    ensure_access_control()
    if request.method == "POST":
        action = str(request.POST.get("action") or "create_user").strip()
        if action == "create_user":
            _handle_user_creation(request)
        elif action == "update_user_credentials":
            _handle_user_credentials_update(request)
        elif action == "reset_password":
            _handle_password_reset(request)
        elif action == "delete_user":
            _handle_user_deletion(request)
        elif action == "create_credential_type":
            _handle_credential_type_creation(request)
        else:
            messages.error(request, "Acao de usuario nao reconhecida.")
        return redirect("/accounts/")
    context = {
        "title": "Usuarios e privilegios",
        "access": access_control_snapshot(),
        "can_manage_users": _can_manage_users(request),
        "permission_catalog": _permission_catalog(),
    }
    return render(request, "power_church_django/accounts/index.html", context)


@login_required
def relogin(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        logout(request)
    request.session.flush()
    return redirect("/accounts/login/")


def _handle_user_creation(request: HttpRequest) -> None:
    has_superuser = User.objects.filter(is_superuser=True).exists()
    if has_superuser and not _can_manage_users(request):
        messages.error(request, "Apenas um administrador logado pode criar ou alterar usuarios.")
        return
    username = str(request.POST.get("username") or "").strip()
    email = str(request.POST.get("email") or "").strip()
    password = str(request.POST.get("password") or "")
    password_confirm = str(request.POST.get("password_confirm") or "")
    group_name = str(request.POST.get("group") or "").strip()
    make_superuser = str(request.POST.get("is_superuser") or "") == "1"
    if not username:
        messages.error(request, "Informe o usuario.")
        return
    if len(password) < 8:
        messages.error(request, "A senha precisa ter pelo menos 8 caracteres.")
        return
    if password != password_confirm:
        messages.error(request, "A confirmacao da senha nao confere.")
        return
    if not has_superuser:
        user = create_or_update_admin(username=username, email=email, password=password)
        messages.success(request, f"Primeiro administrador {user.username} criado com sucesso. Ja e possivel entrar pelo login.")
        return

    user, _created = User.objects.get_or_create(username=username)
    user.email = email
    user.is_active = True
    user.is_staff = make_superuser or group_name == "Administrador do Sistema"
    user.is_superuser = make_superuser
    user.set_password(password)
    user.save()
    user.groups.clear()
    if group_name:
        group = Group.objects.filter(name=group_name).first()
        if group:
            user.groups.add(group)
    messages.success(request, f"Usuario {user.username} criado/atualizado com sucesso.")


def _can_manage_users(request: HttpRequest) -> bool:
    return bool(request.user.is_superuser or request.user.has_perm("power_church.manage_accounts"))


def _handle_user_credentials_update(request: HttpRequest) -> None:
    if not _can_manage_users(request):
        messages.error(request, "Apenas um administrador logado pode editar credenciais.")
        return
    user_id = int(request.POST.get("user_id") or 0)
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "Usuario nao encontrado para atualizacao de credenciais.")
        return
    group_name = str(request.POST.get("group") or "").strip()
    is_active = str(request.POST.get("is_active") or "1") == "1"
    requested_superuser = str(request.POST.get("is_superuser") or ("1" if target.is_superuser else "0")) == "1"
    if not request.user.is_superuser:
        requested_superuser = target.is_superuser
    if int(target.pk or 0) == int(request.user.pk or 0) and not is_active:
        messages.error(request, "Nao e permitido desativar o proprio usuario logado.")
        return
    if target.is_superuser and not requested_superuser:
        remaining_superusers = User.objects.filter(is_superuser=True).exclude(pk=target.pk).count()
        if remaining_superusers == 0:
            messages.error(request, "Nao e permitido remover o ultimo superusuario do sistema.")
            return
    if target.is_superuser and not is_active:
        remaining_active_superusers = User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).count()
        if remaining_active_superusers == 0:
            messages.error(request, "Nao e permitido desativar o ultimo superusuario ativo do sistema.")
            return
    group = None
    if group_name:
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            messages.error(request, "Tipo de credencial nao encontrado.")
            return
    target.is_active = is_active
    target.is_superuser = requested_superuser
    target.is_staff = requested_superuser or group_name == "Administrador do Sistema"
    target.save(update_fields=["is_active", "is_superuser", "is_staff"])
    target.groups.clear()
    if group is not None:
        target.groups.add(group)
    messages.success(request, f"Credenciais do usuario {target.username} atualizadas com sucesso.")


def _handle_password_reset(request: HttpRequest) -> None:
    if not _can_manage_users(request):
        messages.error(request, "Apenas um administrador logado pode redefinir senhas.")
        return
    user_id = int(request.POST.get("user_id") or 0)
    password = str(request.POST.get("new_password") or "")
    password_confirm = str(request.POST.get("new_password_confirm") or "")
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "Usuario nao encontrado para redefinicao de senha.")
        return
    if len(password) < 8:
        messages.error(request, "A nova senha precisa ter pelo menos 8 caracteres.")
        return
    if password != password_confirm:
        messages.error(request, "A confirmacao da nova senha nao confere.")
        return
    target.set_password(password)
    target.save(update_fields=["password"])
    messages.success(request, f"Senha do usuario {target.username} atualizada com sucesso.")


def _handle_user_deletion(request: HttpRequest) -> None:
    if not _can_manage_users(request):
        messages.error(request, "Apenas um administrador logado pode excluir usuarios.")
        return
    user_id = int(request.POST.get("user_id") or 0)
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "Usuario nao encontrado para exclusao.")
        return
    if int(target.pk or 0) == int(request.user.pk or 0):
        messages.error(request, "Nao e permitido excluir o proprio usuario logado.")
        return
    if target.is_superuser and User.objects.filter(is_superuser=True).exclude(pk=target.pk).count() == 0:
        messages.error(request, "Nao e permitido excluir o ultimo superusuario do sistema.")
        return
    username = target.username
    target.delete()
    messages.success(request, f"Usuario {username} removido com sucesso.")


def _handle_credential_type_creation(request: HttpRequest) -> None:
    if not _can_manage_users(request):
        messages.error(request, "Apenas um administrador logado pode criar tipos de credencial.")
        return
    group_name = str(request.POST.get("credential_name") or "").strip()
    permission_codes = [str(code).strip() for code in request.POST.getlist("permission_codes") if str(code).strip()]
    if not group_name:
        messages.error(request, "Informe o nome do tipo de credencial.")
        return
    if Group.objects.filter(name=group_name).exists():
        messages.error(request, "Ja existe um tipo de credencial com esse nome.")
        return
    valid_codes = {permission.codename for permission in MODULE_PERMISSIONS}
    invalid_codes = [code for code in permission_codes if code not in valid_codes]
    if invalid_codes:
        messages.error(request, "Foram informadas permissoes invalidas para o novo tipo de credencial.")
        return
    content_type = access_content_type()
    permissions = list(Permission.objects.filter(content_type=content_type, codename__in=permission_codes).order_by("codename"))
    group = Group.objects.create(name=group_name)
    group.permissions.set(permissions)
    messages.success(request, f"Tipo de credencial {group.name} criado com sucesso.")


def _permission_catalog() -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for permission in MODULE_PERMISSIONS:
        grouped.setdefault(permission.module, []).append(
            {"codename": permission.codename, "name": permission.name}
        )
    return [
        {"module": module, "permissions": permissions}
        for module, permissions in grouped.items()
    ]
