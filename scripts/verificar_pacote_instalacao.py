from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"


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


def contains_all(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


def build_checks(db_path: Path) -> list[Check]:
    checks: list[Check] = []
    required_files = [
        ".dockerignore",
        "Dockerfile",
        "Dockerfile.django",
        "docker-compose.yml",
        "docker-compose.django.yml",
        "deploy/README_INSTALACAO.md",
        "deploy/env.example",
        "deploy/requirements/base.txt",
        "deploy/requirements/django.txt",
        "deploy/requirements/ocr.txt",
        "deploy/system/ubuntu-24.04.txt",
        "deploy/install_local_mac.sh",
        "deploy/install_ubuntu_server.sh",
        "deploy/backup_sqlite.sh",
        "deploy/restore_sqlite.sh",
    ]
    missing = [name for name in required_files if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Arquivos do pacote",
            "OK" if not missing else "FALHA",
            "todos presentes" if not missing else "faltando: " + ", ".join(missing),
        )
    )

    app_text = read_text(ROOT / "power_church_demo.py")
    env_terms = [
        "POWER_CHURCH_DB_PATH",
        "POWER_CHURCH_HOST",
        "POWER_CHURCH_PORT",
        "server_host_from_args",
    ]
    checks.append(
        Check(
            "App configuravel por ambiente",
            "OK" if contains_all(app_text, env_terms) else "FALHA",
            "host, porta e banco aceitam variaveis de ambiente"
            if contains_all(app_text, env_terms)
            else "faltam termos: " + ", ".join(term for term in env_terms if term not in app_text),
        )
    )

    dockerfile = read_text(ROOT / "Dockerfile")
    docker_terms = [
        "python:3.11-slim",
        "POWER_CHURCH_PDF_PROVIDER=pymupdf",
        "deploy/requirements/base.txt",
        "tesseract-ocr-por",
        "HEALTHCHECK",
        "power_church_demo.py",
    ]
    checks.append(
        Check(
            "Imagem Docker preparada",
            "OK" if contains_all(dockerfile, docker_terms) else "FALHA",
            "Python, PyMuPDF, OCR futuro e healthcheck configurados"
            if contains_all(dockerfile, docker_terms)
            else "faltam termos: " + ", ".join(term for term in docker_terms if term not in dockerfile),
        )
    )
    django_dockerfile = read_text(ROOT / "Dockerfile.django")
    django_docker_terms = [
        "python:3.11-slim",
        "deploy/requirements/django.txt",
        "gunicorn",
        "power_church_site.wsgi:application",
    ]
    checks.append(
        Check(
            "Imagem Docker Django preparada",
            "OK" if contains_all(django_dockerfile, django_docker_terms) else "FALHA",
            "Django staging preparado"
            if contains_all(django_dockerfile, django_docker_terms)
            else "faltam termos: " + ", ".join(term for term in django_docker_terms if term not in django_dockerfile),
        )
    )

    compose = read_text(ROOT / "docker-compose.yml")
    compose_terms = [
        "POWER_CHURCH_DB_PATH",
        "POWER_CHURCH_PDF_PROVIDER",
        "./data:/app/data",
        "8000:8000",
    ]
    checks.append(
        Check(
            "Compose single-tenant",
            "OK" if contains_all(compose, compose_terms) else "FALHA",
            "dados do cliente ficam em volume local ./data"
            if contains_all(compose, compose_terms)
            else "faltam termos: " + ", ".join(term for term in compose_terms if term not in compose),
        )
    )
    django_compose = read_text(ROOT / "docker-compose.django.yml")
    django_compose_terms = [
        "power-church-django",
        "postgres:16",
        "POWER_CHURCH_LEGACY_DB_PATH",
        "8001:8000",
    ]
    checks.append(
        Check(
            "Compose Django staging",
            "OK" if contains_all(django_compose, django_compose_terms) else "FALHA",
            "Django com PostgreSQL em staging"
            if contains_all(django_compose, django_compose_terms)
            else "faltam termos: " + ", ".join(term for term in django_compose_terms if term not in django_compose),
        )
    )

    requirements = read_text(ROOT / "deploy" / "requirements" / "base.txt")
    checks.append(
        Check(
            "Dependencia PDF portavel declarada",
            "OK" if "PyMuPDF" in requirements else "FALHA",
            "PyMuPDF declarado em deploy/requirements/base.txt"
            if "PyMuPDF" in requirements
            else "PyMuPDF nao encontrado no requirements base",
        )
    )

    backup_text = read_text(ROOT / "deploy" / "backup_sqlite.sh")
    restore_text = read_text(ROOT / "deploy" / "restore_sqlite.sh")
    checks.append(
        Check(
            "Backup e restore SQLite",
            "OK" if ".backup" in backup_text and "pre_restore" in restore_text else "FALHA",
            "backup usa sqlite .backup e restore preserva copia anterior"
            if ".backup" in backup_text and "pre_restore" in restore_text
            else "rotina de backup/restore incompleta",
        )
    )

    checks.append(
        Check(
            "Banco local informado",
            "OK" if db_path.exists() else "ALERTA",
            str(db_path) if db_path.exists() else "banco nao encontrado; aceitavel em imagem limpa antes do primeiro restore",
        )
    )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"pacote_instalacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    alerts = [check for check in checks if check.status == "ALERTA"]
    lines = [
        "# Pacote De Instalacao",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'FALHAS' if failed else 'OK COM ALERTAS' if alerts else 'OK'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica se o pacote de instalacao esta pronto.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"), help="Caminho do banco SQLite.")
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
