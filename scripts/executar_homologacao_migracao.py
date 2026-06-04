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
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
MANAGE_PY = DJANGO_DIR / "manage.py"


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


STAGES: dict[str, dict[str, object]] = {
    "1": {
        "slug": "fundacao",
        "title": "Etapa 1 - Fundacao e modulos modernos de baixo risco",
        "roteiro": ROOT / "data" / "homologacao" / "ROTEIRO_OPERADOR_ETAPA1_FUNDACAO_V1.md",
        "steps": (
            ("manage.py check", lambda db: [str(DJANGO_VENV_PYTHON), str(MANAGE_PY), "check"]),
            ("Django funcional", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_django_funcional.py"), "--db", str(db), "--report"]),
            ("Contrato visual Django", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_contrato_visual_django.py"), "--db", str(db), "--report"]),
            ("Funcionalidade total", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_funcionalidade_total.py"), "--db", str(db), "--report"]),
        ),
    },
    "2": {
        "slug": "cadastro_familias",
        "title": "Etapa 2 - Cadastro, buscas, familias e merge",
        "roteiro": ROOT / "data" / "homologacao" / "ROTEIRO_OPERADOR_ETAPA2_CADASTRO_FAMILIAS_V1.md",
        "steps": (
            ("Sincronizar espelho cadastral Postgres", lambda db: [sys.executable, str(ROOT / "scripts" / "sincronizar_espelho_cadastro_postgres.py"), "--db", str(db), "--report"]),
            ("Verificar espelho cadastral Postgres", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_espelho_cadastro_postgres.py"), "--db", str(db), "--report"]),
            ("manage.py check", lambda db: [str(DJANGO_VENV_PYTHON), str(MANAGE_PY), "check"]),
            ("Django funcional", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_django_funcional.py"), "--db", str(db), "--report"]),
            ("Escrita de pessoas Django", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_django_escrita_pessoas.py"), "--db", str(db), "--report"]),
            ("Paridade Django", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_paridade_django.py"), "--db", str(db), "--report"]),
            ("Funcionalidade total", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_funcionalidade_total.py"), "--db", str(db), "--report"]),
        ),
    },
    "3": {
        "slug": "financeiro_recibos",
        "title": "Etapa 3 - Contribuicoes, envelopes, recibos e extratos",
        "roteiro": ROOT / "data" / "homologacao" / "ROTEIRO_OPERADOR_ETAPA3_FINANCEIRO_RECIBOS_V1.md",
        "steps": (
            ("manage.py check", lambda db: [str(DJANGO_VENV_PYTHON), str(MANAGE_PY), "check"]),
            ("Dados operacionais", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_dados_operacionais.py"), "--db", str(db), "--report"]),
            ("Django funcional", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_django_funcional.py"), "--db", str(db), "--report"]),
            ("Paridade Django", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_paridade_django.py"), "--db", str(db), "--report"]),
            ("Funcionalidade total", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_funcionalidade_total.py"), "--db", str(db), "--report"]),
        ),
    },
    "4": {
        "slug": "importacoes_conciliacoes",
        "title": "Etapa 4 - Importacoes bancarias, conciliacoes e prontidao para nuvem",
        "roteiro": ROOT / "data" / "homologacao" / "ROTEIRO_OPERADOR_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md",
        "steps": (
            ("manage.py check", lambda db: [str(DJANGO_VENV_PYTHON), str(MANAGE_PY), "check"]),
            ("Dados operacionais", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_dados_operacionais.py"), "--db", str(db), "--report"]),
            ("Prontidao da transicao", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_prontidao_transicao.py"), "--db", str(db), "--report"]),
            ("Paridade Django", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_paridade_django.py"), "--db", str(db), "--report"]),
            ("Funcionalidade total", lambda db: [sys.executable, str(ROOT / "scripts" / "verificar_funcionalidade_total.py"), "--db", str(db), "--report"]),
        ),
    },
}


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de ambiente nao encontrado: {path}")
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
            value = value[1:-1]
        env[key] = value
    return env


def run_step(step: Step, env: dict[str, str]) -> StepResult:
    completed = subprocess.run(
        step.command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    return StepResult(step.name, step.command, completed.returncode, output)


def write_report(stage_key: str, db_path: Path, env_file: Path, roteiro: Path, results: list[StepResult]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = STAGES[stage_key]
    failed = [item for item in results if not item.ok]
    target = REPORT_DIR / f"homologacao_migracao_etapa{stage_key}_{stage['slug']}_{stamp}.md"
    lines = [
        f"# Homologacao Migracao Etapa {stage_key}",
        "",
        f"Etapa: {stage['title']}",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco legado: `{db_path}`",
        f"Ambiente Postgres: `{env_file}`",
        f"Roteiro do operador: `{roteiro}`",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "| Checagem | Status | Comando |",
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
    lines.extend(
        [
            "",
            "## Proximo passo",
            "",
            "Se todas as checagens estiverem `OK`, execute o roteiro curto do operador desta etapa e so libere a migracao quando os cenarios criticos estiverem marcados como `OK`.",
            "",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def build_stage_steps(stage_key: str, db_path: Path) -> list[Step]:
    stage = STAGES[stage_key]
    raw_steps = stage["steps"]
    return [Step(name, builder(db_path)) for name, builder in raw_steps]  # type: ignore[misc]


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa a homologacao automatica da migracao por etapa.")
    parser.add_argument("--stage", required=True, choices=sorted(STAGES.keys()), help="Etapa da migracao a homologar.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite usado nas checagens.")
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Arquivo de ambiente do Django/Postgres para a homologacao.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    env_file = Path(args.env_file).expanduser().resolve()
    if not db_path.exists():
        print(f"Banco legado nao encontrado: {db_path}", file=sys.stderr)
        return 2
    if not DJANGO_VENV_PYTHON.exists():
        print(f"Python da venv Django nao encontrado: {DJANGO_VENV_PYTHON}", file=sys.stderr)
        return 2

    extra_env = load_env_file(env_file)
    env = dict(os.environ)
    env.update(extra_env)
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    env["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)

    stage_key = args.stage
    roteiro = Path(STAGES[stage_key]["roteiro"])  # type: ignore[arg-type]
    results: list[StepResult] = []
    for step in build_stage_steps(stage_key, db_path):
        print(f"==> {step.name}")
        result = run_step(step, env)
        results.append(result)
        print(result.output)
        print(f"<== {'OK' if result.ok else 'FALHA'}: {step.name}\n")

    report = write_report(stage_key, db_path, env_file, roteiro, results)
    print(f"Relatorio consolidado: {report}")
    print(f"Roteiro do operador: {roteiro}")
    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
