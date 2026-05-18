from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DJANGO_VENV_PYTHON = ROOT / "power_church_django" / ".venv" / "bin" / "python"


@dataclass
class RunResult:
    name: str
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_script(
    name: str,
    script_name: str,
    db_path: str,
    extra_args: list[str] | None = None,
    python_executable: Path | str | None = None,
) -> RunResult:
    runner = str(python_executable or sys.executable)
    command = [runner, str(ROOT / "scripts" / script_name), "--db", db_path, "--report", *(extra_args or [])]
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, env=env, check=False)
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    return RunResult(name, command, completed.returncode, output)


def write_report(results: list[RunResult]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"funcionalidade_total_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [item for item in results if not item.ok]
    lines = [
        "# Funcionalidade Total",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "| Etapa | Status | Comando |",
        "| --- | --- | --- |",
    ]
    for result in results:
        command_text = " ".join(result.command)
        lines.append(f"| {result.name} | {'OK' if result.ok else 'FALHA'} | `{command_text}` |")
    for result in results:
        lines.extend(
            [
                "",
                f"## {result.name}",
                "",
                "```text",
                result.output[-12000:] if result.output else "(sem saida)",
                "```",
            ]
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Roda a bateria total de homologacao funcional.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown consolidado.")
    args = parser.parse_args()
    portable_python = DJANGO_VENV_PYTHON if DJANGO_VENV_PYTHON.exists() else Path(sys.executable)
    steps = [
        ("Dependencias do ambiente local", "verificar_dependencias_servidor.py", [], None),
        ("Pacote de instalacao", "verificar_pacote_instalacao.py", [], None),
        ("Funcionalidade dos nucleos migrados", "verificar_funcionalidade_transicao.py", [], None),
        ("Arquitetura dos extratores PDF", "verificar_extratores_pdf.py", [], None),
        ("Paridade PyMuPDF nos bancos", "verificar_extratores_pdf.py", ["--compare-provider", "pymupdf"], portable_python),
        ("Fixtures PDF por banco", "verificar_fixtures_pdf_bancos.py", [], portable_python),
        ("Estabilidade geral", "verificar_estabilidade_demo.py", [], None),
        ("Dados operacionais", "verificar_dados_operacionais.py", [], None),
        ("Prontidao arquitetural", "verificar_prontidao_transicao.py", [], None),
        ("Prontidao para Django", "verificar_prontidao_django.py", [], None),
        ("Pacotes Django de fundacao", "verificar_pacotes_django.py", [], None),
        ("Contrato visual Django", "verificar_contrato_visual_django.py", [], None),
        ("Django funcional em leitura", "verificar_django_funcional.py", [], None),
        ("Django escrita de pessoas em banco temporario", "verificar_django_escrita_pessoas.py", [], None),
        ("Simulacao familias domiciliares por endereco", "sincronizar_nucleos_familiares_endereco.py", [], None),
        ("Paridade Django operacional", "verificar_paridade_django.py", [], None),
    ]
    results: list[RunResult] = []
    for name, script_name, extra_args, python_executable in steps:
        print(f"==> {name}")
        result = run_script(name, script_name, str(args.db), extra_args=extra_args, python_executable=python_executable)
        results.append(result)
        print(result.output)
        print(f"<== {'OK' if result.ok else 'FALHA'}: {name}\n")
    if args.report:
        report = write_report(results)
        print(f"Relatorio consolidado: {report}")
    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
