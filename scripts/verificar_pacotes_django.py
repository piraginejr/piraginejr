from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "data" / "homologacao"


ACTIVE_PACKAGES = [
    ("auditlog", "django-auditlog", "Auditoria automatica Django"),
    ("anymail", "django-anymail", "Email por provedores externos"),
    ("crispy_forms", "django-crispy-forms", "Renderizacao de formularios"),
    ("crispy_bootstrap5", "crispy-bootstrap5", "Template pack Bootstrap 5"),
    ("django_filters", "django-filter", "Filtros padronizados"),
    ("django_tables2", "django-tables2", "Tabelas padronizadas"),
    ("djmoney", "django-money", "Campos monetarios futuros"),
    ("formtools", "django-formtools", "Fluxos em etapas"),
    ("guardian", "django-guardian", "Permissoes por objeto"),
    ("import_export", "django-import-export", "Importacao/exportacao"),
    ("waffle", "django-waffle", "Feature flags"),
]

OPTIONAL_PACKAGES = [
    ("allauth", "django-allauth", "Login/MFA futuro"),
    ("django_rq", "django-rq", "Filas com Redis para OCR/PDF"),
    ("unfold", "django-unfold", "Admin visual futuro"),
    ("django_weasyprint", "django-weasyprint", "PDF HTML/CSS futuro"),
]

REQUIRED_ACTIVE_APPS = [
    "auditlog",
    "anymail",
    "crispy_forms",
    "crispy_bootstrap5",
    "django_filters",
    "django_tables2",
    "djmoney",
    "formtools",
    "guardian",
    "import_export",
    "waffle",
]


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _run_inside_venv() -> int | None:
    if Path(sys.executable).resolve() == DJANGO_VENV_PYTHON.resolve() or not DJANGO_VENV_PYTHON.exists():
        return None
    completed = subprocess.run(
        [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(ROOT),
        text=True,
        check=False,
    )
    return completed.returncode


def _package_version(distribution_name: str) -> str:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _import_status(module_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "import OK"


def build_checks() -> list[Check]:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

    import django

    django.setup()

    from django.conf import settings
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    checks: list[Check] = []

    for module_name, distribution_name, purpose in ACTIVE_PACKAGES:
        version = _package_version(distribution_name)
        import_ok, detail = _import_status(module_name)
        checks.append(
            Check(
                f"Pacote ativo {distribution_name}",
                "OK" if version and import_ok else "FALHA",
                f"{version or 'nao instalado'}; {purpose}; {detail}",
            )
        )

    for module_name, distribution_name, purpose in OPTIONAL_PACKAGES:
        version = _package_version(distribution_name)
        import_ok, detail = _import_status(module_name)
        status = "OK" if version and import_ok else "ALERTA" if version else "FALHA"
        checks.append(
            Check(
                f"Pacote opcional {distribution_name}",
                status,
                f"{version or 'nao instalado'}; {purpose}; {detail}",
            )
        )

    missing_apps = [app for app in REQUIRED_ACTIVE_APPS if app not in settings.INSTALLED_APPS]
    checks.append(
        Check(
            "Apps Django ativos",
            "OK" if not missing_apps else "FALHA",
            "apps de fundacao carregados" if not missing_apps else "faltando: " + ", ".join(missing_apps),
        )
    )

    auth_backends = list(getattr(settings, "AUTHENTICATION_BACKENDS", []))
    checks.append(
        Check(
            "Guardian backend",
            "OK" if "guardian.backends.ObjectPermissionBackend" in auth_backends else "FALHA",
            ", ".join(auth_backends),
        )
    )
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    checks.append(
        Check(
            "Middlewares seguros",
            "OK"
            if "auditlog.middleware.AuditlogMiddleware" in middleware and "waffle.middleware.WaffleMiddleware" in middleware
            else "FALHA",
            "auditlog e waffle carregados",
        )
    )

    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    pending_relevant = sorted(
        {
            migration.app_label
            for migration, _backwards in pending
            if migration.app_label in {"audit", "auditlog", "guardian", "waffle"}
        }
    )
    checks.append(
        Check(
            "Migracoes dos pacotes ativos",
            "OK" if not pending_relevant else "FALHA",
            "sem migracoes pendentes" if not pending_relevant else "pendentes: " + ", ".join(pending_relevant),
        )
    )

    checks.append(
        Check(
            "WeasyPrint nativo",
            "OK"
            if next((check for check in checks if check.name == "Pacote opcional django-weasyprint"), None)
            and next(check for check in checks if check.name == "Pacote opcional django-weasyprint").status == "OK"
            else "ALERTA",
            "PDF HTML/CSS depende de bibliotecas nativas Pango/GObject; no Mac pode ficar em alerta ate o pacote de servidor.",
        )
    )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"pacotes_django_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    alerts = [check for check in checks if check.status == "ALERTA"]
    lines = [
        "# Pacotes Django",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'FALHAS' if failed else 'OK COM ALERTAS' if alerts else 'OK'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        detail = check.detail.replace("|", "/")
        lines.append(f"| {check.name} | {check.status} | {detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun
    parser = argparse.ArgumentParser(description="Verifica pacotes Django de fundacao e opcionais.")
    parser.add_argument("--db", default="", help="Aceito por compatibilidade com a bateria total.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    parser.parse_args()
    checks = build_checks()
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if "--report" in sys.argv:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
