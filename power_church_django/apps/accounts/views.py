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
        _handle_user_creation(request)
        return redirect("/accounts/")
    context = {
        "title": "Usuarios e privilegios",
        "access": access_control_snapshot(),
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
    if has_superuser and not request.user.is_superuser:
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
