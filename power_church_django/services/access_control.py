from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterable

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse


ACCESS_APP_LABEL = "power_church"
ACCESS_MODEL = "moduleaccess"


@dataclass(frozen=True)
class ModulePermission:
    codename: str
    name: str
    module: str


MODULE_PERMISSIONS = [
    ModulePermission("view_dashboard", "Pode ver dashboard", "Dashboard"),
    ModulePermission("view_people", "Pode ver pessoas", "Secretaria"),
    ModulePermission("manage_people", "Pode gerir pessoas", "Secretaria"),
    ModulePermission("delete_people", "Pode enviar pessoas para lixeira segura", "Secretaria"),
    ModulePermission("view_contributors", "Pode ver contribuintes auxiliares", "Contribuintes"),
    ModulePermission("manage_contributors", "Pode gerir contribuintes auxiliares", "Contribuintes"),
    ModulePermission("view_contributions", "Pode ver contribuicoes", "Contribuicoes"),
    ModulePermission("manage_contributions", "Pode gerir contribuicoes", "Contribuicoes"),
    ModulePermission("view_imports", "Pode ver importacoes bancarias", "Importacoes"),
    ModulePermission("manage_imports", "Pode importar e reprocessar lotes", "Importacoes"),
    ModulePermission("operate_bank_review", "Pode operar auditoria bancaria", "Importacoes"),
    ModulePermission("view_reports", "Pode ver relatorios", "Relatorios"),
    ModulePermission("view_audit", "Pode ver auditoria", "Auditoria"),
    ModulePermission("manage_accounts", "Pode gerir usuarios e privilegios", "Administracao"),
]


DEFAULT_GROUPS = {
    "Administrador do Sistema": [permission.codename for permission in MODULE_PERMISSIONS],
    "Gestor de Secretaria": [
        "view_dashboard",
        "view_people",
        "manage_people",
        "delete_people",
        "view_contributors",
        "view_reports",
        "view_audit",
    ],
    "Operador de Recebimentos": [
        "view_dashboard",
        "view_contributors",
        "manage_contributors",
        "view_contributions",
        "manage_contributions",
        "view_imports",
        "manage_imports",
        "operate_bank_review",
        "view_reports",
    ],
    "Auditor": [
        "view_dashboard",
        "view_people",
        "view_contributors",
        "view_contributions",
        "view_imports",
        "view_reports",
        "view_audit",
    ],
    "Consulta": [
        "view_dashboard",
        "view_people",
        "view_contributors",
        "view_contributions",
        "view_reports",
    ],
}

SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def access_content_type() -> ContentType:
    content_type, _created = ContentType.objects.get_or_create(app_label=ACCESS_APP_LABEL, model=ACCESS_MODEL)
    return content_type


def ensure_access_control() -> dict[str, int]:
    content_type = access_content_type()
    permission_by_code: dict[str, Permission] = {}
    created_permissions = 0
    updated_permissions = 0
    for module_permission in MODULE_PERMISSIONS:
        permission, created = Permission.objects.get_or_create(
            content_type=content_type,
            codename=module_permission.codename,
            defaults={"name": module_permission.name},
        )
        if created:
            created_permissions += 1
        elif permission.name != module_permission.name:
            permission.name = module_permission.name
            permission.save(update_fields=["name"])
            updated_permissions += 1
        permission_by_code[module_permission.codename] = permission

    created_groups = 0
    for group_name, codenames in DEFAULT_GROUPS.items():
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            created_groups += 1
        group.permissions.set(permission_by_code[codename] for codename in codenames)

    return {
        "created_permissions": created_permissions,
        "updated_permissions": updated_permissions,
        "created_groups": created_groups,
        "total_permissions": len(MODULE_PERMISSIONS),
        "total_groups": len(DEFAULT_GROUPS),
    }


def create_or_update_admin(username: str, email: str = "", password: str = "") -> User:
    user, created = User.objects.get_or_create(username=username)
    user.email = email or user.email
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    if password:
        user.set_password(password)
    elif created:
        user.set_unusable_password()
    user.save()
    admin_group = Group.objects.filter(name="Administrador do Sistema").first()
    if admin_group:
        user.groups.add(admin_group)
    return user


def access_control_snapshot() -> dict[str, Any]:
    content_type = ContentType.objects.filter(app_label=ACCESS_APP_LABEL, model=ACCESS_MODEL).first()
    installed_permissions: set[str] = set()
    if content_type:
        installed_permissions = set(
            Permission.objects.filter(content_type=content_type).values_list("codename", flat=True)
        )
    expected_permissions = {permission.codename for permission in MODULE_PERMISSIONS}
    groups = []
    for group in Group.objects.order_by("name").prefetch_related("permissions"):
        group_permissions = [
            permission.codename
            for permission in group.permissions.filter(content_type=content_type).order_by("codename")
        ] if content_type else []
        groups.append(
            {
                "name": group.name,
                "permissions": group_permissions,
                "count": len(group_permissions),
                "is_default": group.name in DEFAULT_GROUPS,
            }
        )
    users = list(User.objects.order_by("username").prefetch_related("groups"))
    for user in users:
        current_groups = list(user.groups.all())
        user.primary_group_name = current_groups[0].name if current_groups else ""
    return {
        "installed": not (expected_permissions - installed_permissions),
        "missing_permissions": sorted(expected_permissions - installed_permissions),
        "permissions": MODULE_PERMISSIONS,
        "groups": groups,
        "users": users,
        "user_count": User.objects.count(),
        "has_superuser": User.objects.filter(is_superuser=True).exists(),
        "group_count": Group.objects.count(),
        "permission_count": len(installed_permissions),
    }


def _normalize_permission_codes(codes: str | Iterable[str] | None) -> tuple[str, ...]:
    if codes is None:
        return ()
    if isinstance(codes, str):
        clean = codes.strip()
        return (clean,) if clean else ()
    normalized = [str(code).strip() for code in codes if str(code).strip()]
    return tuple(normalized)


def user_has_module_permission(user: User, *permission_codes: str) -> bool:
    normalized = _normalize_permission_codes(permission_codes)
    if getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return any(user.has_perm(f"power_church.{code}") for code in normalized)


def module_permission_required(
    view_permissions: str | Iterable[str],
    unsafe_permissions: str | Iterable[str] | None = None,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    read_permissions = _normalize_permission_codes(view_permissions)
    write_permissions = _normalize_permission_codes(unsafe_permissions) or read_permissions

    def decorator(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
            ensure_access_control()
            required_permissions = read_permissions if request.method in SAFE_HTTP_METHODS else write_permissions
            if user_has_module_permission(request.user, *required_permissions):
                return view_func(request, *args, **kwargs)
            required_label = ", ".join(required_permissions) or "acesso_autenticado"
            raise PermissionDenied(f"Usuario sem permissao para acessar este modulo ({required_label}).")

        return login_required(wrapped)

    return decorator
