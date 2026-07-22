from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from power_church_django.services.access_control import (
    access_control_snapshot,
    create_or_update_admin,
    ensure_access_control,
    module_permission_required,
)


@module_permission_required("manage_accounts")
def index(request: HttpRequest) -> HttpResponse:
    ensure_access_control()
    if request.method == "POST":
        action = str(request.POST.get("action") or "create_user").strip()
        if action == "create_user":
            _handle_user_creation(request)
        elif action == "reset_password":
            _handle_password_reset(request)
        elif action == "delete_user":
            _handle_user_deletion(request)
        else:
            messages.error(request, "Acao de usuario nao reconhecida.")
        return redirect("/accounts/")
    context = {
        "title": "Usuarios e privilegios",
        "access": access_control_snapshot(),
        "can_manage_users": _can_manage_users(request),
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
