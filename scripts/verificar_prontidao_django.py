from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
APP_PATH = ROOT / "power_church_demo.py"
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def latest_report(prefix: str) -> Path | None:
    reports = sorted(REPORT_DIR.glob(f"{prefix}_*.md"))
    return reports[-1] if reports else None


def latest_ok_report(prefix: str) -> Path | None:
    for report in reversed(sorted(REPORT_DIR.glob(f"{prefix}_*.md"))):
        if report_has_ok(report):
            return report
    return None


def report_has_ok(path: Path | None) -> bool:
    if path is None:
        return False
    text = read_text(path)
    return "Resultado: OK" in text or "Resultado: OK COM ALERTAS" in text


def run_django_command(args: list[str]) -> tuple[bool, str]:
    if not DJANGO_VENV_PYTHON.exists():
        return False, f"Python da venv nao encontrado: {DJANGO_VENV_PYTHON}"
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    completed = subprocess.run(
        [str(DJANGO_VENV_PYTHON), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    return completed.returncode == 0, output


def bank_zero_value_detail(conn: sqlite3.Connection) -> tuple[int, str]:
    count = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM contribuicoes
         WHERE ativo = 1
           AND valor <= 0
           AND (pix_movimento_id IS NOT NULL OR extrato_movimento_id IS NOT NULL)
        """,
    )
    if count == 0:
        return 0, "0 lancamento(s) bancario(s) ativo(s) com valor <= 0"
    rows = conn.execute(
        """
        SELECT c.id AS contribuicao_id, c.data_recebimento AS data_ref, c.valor,
               'PIX' AS origem, pm.lote_id, 'SICOOB_PIX' AS banco,
               pm.id AS movimento_id, pm.nome_origem AS remetente
          FROM contribuicoes c
          JOIN pix_movimentos pm ON pm.id = c.pix_movimento_id
         WHERE c.ativo = 1 AND c.valor <= 0
        UNION ALL
        SELECT c.id AS contribuicao_id, COALESCE(c.data_recebimento, em.data_movimento) AS data_ref, c.valor,
               'EXTRATO' AS origem, em.lote_id, COALESCE(el.banco, el.layout_codigo, 'Extrato') AS banco,
               em.id AS movimento_id, COALESCE(em.nome_origem, em.origin_label, em.raw_text) AS remetente
          FROM contribuicoes c
          JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
          LEFT JOIN extrato_lotes el ON el.id = em.lote_id
         WHERE c.ativo = 1 AND c.valor <= 0
         LIMIT 5
        """
    ).fetchall()
    samples = [
        f"{row['origem']} lote {row['lote_id']} mov {row['movimento_id']} contrib {row['contribuicao_id']} "
        f"{row['data_ref'] or '-'} {row['banco'] or '-'} {row['remetente'] or '-'}"
        for row in rows
    ]
    return count, f"{count} lancamento(s) bancario(s) ativo(s) com valor <= 0: " + " ; ".join(samples)


def build_checks(db_path: Path) -> list[Check]:
    checks: list[Check] = []
    required_docs = [
        "PLANO_TRANSICAO_POWER_CHURCH_V1.md",
        "PLANO_HOSPEDAGEM_MIGRACAO_E_OCR_V1.md",
        "PLANO_MIGRACAO_DJANGO_V1.md",
        "MATRIZ_HOMOLOGACAO_V1.md",
        "CHECKLIST_NOVO_BANCO_IMPORTACOES.md",
    ]
    missing_docs = [name for name in required_docs if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Documentacao para Django",
            "OK" if not missing_docs else "FALHA",
            "documentos presentes" if not missing_docs else "faltando: " + ", ".join(missing_docs),
        )
    )

    core_files = [
        "normalization.py",
        "formatting.py",
        "matching.py",
        "designations.py",
        "banking.py",
        "bank_parsers.py",
        "bank_lots.py",
        "pdf_text.py",
        "signatures.py",
        "contributors.py",
    ]
    missing_core = [name for name in core_files if not (ROOT / "power_church_core" / name).exists()]
    checks.append(
        Check(
            "Nucleo reutilizavel para Django",
            "OK" if not missing_core else "FALHA",
            "modulos de dominio presentes" if not missing_core else "faltando: " + ", ".join(missing_core),
        )
    )

    app_text = read_text(APP_PATH)
    delegated_terms = [
        "core_normalization",
        "core_formatting",
        "core_matching",
        "core_designations",
        "core_banking",
        "core_bank_parsers",
        "core_bank_lots",
        "core_pdf_text",
        "core_signatures",
        "core_contributors",
    ]
    missing_delegations = [term for term in delegated_terms if term not in app_text]
    checks.append(
        Check(
            "Prototipo consome nucleo",
            "OK" if not missing_delegations else "FALHA",
            "fachadas delegam regras ao power_church_core"
            if not missing_delegations
            else "faltando: " + ", ".join(missing_delegations),
        )
    )

    env_terms = ["POWER_CHURCH_DB_PATH", "POWER_CHURCH_HOST", "POWER_CHURCH_PORT", "POWER_CHURCH_PDF_PROVIDER"]
    env_text = app_text + "\n" + read_text(ROOT / "deploy" / "env.example")
    missing_env = [term for term in env_terms if term not in env_text]
    checks.append(
        Check(
            "Configuracao portavel",
            "OK" if not missing_env else "FALHA",
            "host, porta, banco e provedor PDF parametrizados"
            if not missing_env
            else "faltando: " + ", ".join(missing_env),
        )
    )

    package_files = ["Dockerfile", "docker-compose.yml", "deploy/backup_sqlite.sh", "deploy/restore_sqlite.sh"]
    missing_package = [name for name in package_files if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Pacote para staging",
            "OK" if not missing_package else "FALHA",
            "Docker, compose, backup e restore presentes"
            if not missing_package
            else "faltando: " + ", ".join(missing_package),
        )
    )

    django_files = [
        "power_church_django/manage.py",
        "power_church_django/power_church_site/settings.py",
        "power_church_django/power_church_site/urls.py",
        "power_church_django/apps/accounts/apps.py",
        "power_church_django/apps/people/apps.py",
        "power_church_django/apps/contributions/apps.py",
        "power_church_django/apps/imports/apps.py",
        "power_church_django/apps/imports/services.py",
        "power_church_django/apps/audit/apps.py",
        "power_church_django/apps/reports/apps.py",
        "power_church_django/templates/power_church_django/dashboard.html",
    ]
    missing_django_files = [name for name in django_files if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Projeto Django paralelo",
            "OK" if not missing_django_files else "FALHA",
            "scaffold Django presente" if not missing_django_files else "faltando: " + ", ".join(missing_django_files),
        )
    )
    settings_text = read_text(ROOT / "power_church_django" / "power_church_site" / "settings.py")
    settings_terms = [
        "power_church_django.apps.accounts",
        "power_church_django.apps.people",
        "power_church_django.apps.contributions",
        "power_church_django.apps.imports",
        "power_church_django.apps.audit",
        "power_church_django.apps.reports",
        "POWER_CHURCH_LEGACY_DB_PATH",
        "POWER_CHURCH_POSTGRES_DB",
    ]
    missing_settings_terms = [term for term in settings_terms if term not in settings_text]
    checks.append(
        Check(
            "Settings Django preparado",
            "OK" if not missing_settings_terms else "FALHA",
            "apps iniciais, legado SQLite e PostgreSQL configurados"
            if not missing_settings_terms
            else "faltando: " + ", ".join(missing_settings_terms),
        )
    )
    import_services_text = read_text(ROOT / "power_church_django" / "apps" / "imports" / "services.py")
    service_terms = ["parse_statement_pdf_by_layout", "statement_entry_plan", "statement_should_skip_entry"]
    missing_service_terms = [term for term in service_terms if term not in import_services_text]
    checks.append(
        Check(
            "Django consome nucleo bancario",
            "OK" if not missing_service_terms else "FALHA",
            "servico de importacao usa power_church_core"
            if not missing_service_terms
            else "faltando: " + ", ".join(missing_service_terms),
        )
    )
    django_requirements = read_text(ROOT / "deploy" / "requirements" / "django.txt")
    requirement_terms = [
        "Django>=5.2",
        "psycopg",
        "gunicorn",
        "django-auditlog",
        "django-import-export",
        "openpyxl",
        "django-filter",
        "django-tables2",
        "django-formtools",
        "django-guardian",
        "django-waffle",
        "django-money",
        "django-crispy-forms",
        "crispy-bootstrap5",
        "django-anymail",
        "django-allauth",
        "django-rq",
        "django-weasyprint",
        "django-unfold",
    ]
    missing_requirement_terms = [term for term in requirement_terms if term not in django_requirements]
    checks.append(
        Check(
            "Requirements Django",
            "OK" if not missing_requirement_terms else "FALHA",
            "Django, PostgreSQL, gunicorn e pacotes de fundacao/futuros declarados"
            if not missing_requirement_terms
            else "faltando: " + ", ".join(missing_requirement_terms),
        )
    )
    django_package_files = ["Dockerfile.django", "docker-compose.django.yml"]
    missing_django_package_files = [name for name in django_package_files if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Staging Django",
            "OK" if not missing_django_package_files else "FALHA",
            "Dockerfile.django e compose Django presentes"
            if not missing_django_package_files
            else "faltando: " + ", ".join(missing_django_package_files),
        )
    )
    checks.append(
        Check(
            "Venv Django",
            "OK" if DJANGO_VENV_PYTHON.exists() else "ALERTA",
            str(DJANGO_VENV_PYTHON) if DJANGO_VENV_PYTHON.exists() else "venv ainda nao criada em power_church_django/.venv",
        )
    )
    django_runtime_ok, django_runtime_output = run_django_command(
        ["-c", "import sys, django, fitz; print(sys.version.split()[0]); print(django.get_version()); print(fitz.__doc__.split()[1])"]
    )
    checks.append(
        Check(
            "Runtime Django instalado",
            "OK" if django_runtime_ok else "ALERTA",
            django_runtime_output.replace("\n", " | ") if django_runtime_output else "sem saida",
        )
    )
    django_check_ok, django_check_output = run_django_command(["power_church_django/manage.py", "check"])
    checks.append(
        Check(
            "Django manage.py check",
            "OK" if django_check_ok else "ALERTA",
            django_check_output.replace("\n", " | ") if django_check_output else "sem saida",
        )
    )
    django_db = ROOT / "data" / "power_church_django.sqlite3"
    checks.append(
        Check(
            "Banco Django separado",
            "OK" if django_db.exists() else "ALERTA",
            str(django_db) if django_db.exists() else "migrations iniciais ainda nao aplicadas",
        )
    )
    checks.append(
        Check(
            "Django instalado no ambiente local",
            "OK" if importlib.util.find_spec("django") or django_runtime_ok else "ALERTA",
            "modulo django encontrado na venv" if django_runtime_ok else "Django ainda nao instalado neste Python local",
        )
    )

    total_report = latest_ok_report("funcionalidade_total")
    checks.append(
        Check(
            "Ultima bateria total",
            "OK" if report_has_ok(total_report) else "FALHA",
            str(total_report) if total_report else "nenhum relatorio encontrado",
        )
    )
    fixtures_report = latest_report("fixtures_pdf_bancos")
    checks.append(
        Check(
            "Fixtures bancarias reais",
            "OK" if report_has_ok(fixtures_report) else "FALHA",
            str(fixtures_report) if fixtures_report else "nenhum relatorio encontrado",
        )
    )
    stability_report = latest_report("verificacao_estabilidade")
    checks.append(
        Check(
            "Estabilidade do prototipo",
            "OK" if report_has_ok(stability_report) else "FALHA",
            str(stability_report) if stability_report else "nenhum relatorio encontrado",
        )
    )

    if not db_path.exists():
        checks.append(Check("Banco SQLite atual", "FALHA", f"nao encontrado: {db_path}"))
        return checks
    conn = sqlite3.connect(db_path)
    try:
        people = scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1")
        contributors = scalar(conn, "SELECT COUNT(*) FROM contribuintes WHERE ativo = 1")
        contributions = scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1")
        statement_lots = scalar(conn, "SELECT COUNT(*) FROM extrato_lotes")
        pix_lots = scalar(conn, "SELECT COUNT(*) FROM pix_lotes")
        checks.append(
            Check(
                "Massa de dados para migracao",
                "OK" if people and contributors and contributions and statement_lots else "FALHA",
                f"{people} pessoas, {contributors} contribuintes, {contributions} contribuicoes, {statement_lots} extratos, {pix_lots} PIX",
            )
        )
        zero_contributions = scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND valor <= 0")
        checks.append(
            Check(
                "Contribuicoes validas",
                "OK" if zero_contributions == 0 else "FALHA",
                f"{zero_contributions} lancamento(s) ativo(s) com valor <= 0",
            )
        )
        bank_zero_count, bank_zero_detail = bank_zero_value_detail(conn)
        checks.append(
            Check(
                "Contribuicoes bancarias com valor valido",
                "OK" if bank_zero_count == 0 else "FALHA",
                bank_zero_detail,
            )
        )
    finally:
        conn.close()

    app_lines = len(app_text.splitlines())
    checks.append(Check("Tamanho do prototipo", "ALERTA" if app_lines > 8000 else "OK", f"{app_lines} linhas"))
    pdf_adapter_text = read_text(ROOT / "power_church_core" / "pdf_text.py")
    swift_refs = pdf_adapter_text.count("PDFKit") + pdf_adapter_text.count('"swift"')
    checks.append(
        Check(
            "PDF portavel pendente",
            "ALERTA" if swift_refs else "OK",
            f"{swift_refs} referencia(s) Swift/PDFKit preservadas no adaptador local" if swift_refs else "sem dependencia local no adaptador",
        )
    )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"prontidao_django_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.status == "FALHA"]
    alerts = [check for check in checks if check.status == "ALERTA"]
    lines = [
        "# Prontidao Para Django",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'FALHAS' if failed else 'OK COM ALERTAS' if alerts else 'OK'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    lines.extend(
        [
            "",
            "## Leitura",
            "",
            "- `FALHA` bloqueia iniciar Django.",
            "- `ALERTA` nao bloqueia, mas deve virar tarefa da migracao.",
            "- Django deve nascer em paralelo e consumir `power_church_core` antes de substituir telas.",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica se o projeto esta pronto para iniciar migracao gradual para Django.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    args = parser.parse_args()
    checks = build_checks(Path(args.db))
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
