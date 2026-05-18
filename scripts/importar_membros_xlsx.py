#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema_power_church_v0.sql"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_REPORT = ROOT / "reports" / "RELATORIO_IMPORTACAO_MEMBROS_2026_04_20.md"
DEFAULT_SOURCE = Path(
    "/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/"
    "Gestao_de_Membresia_Membros-2026_04_20_1659.xlsx"
)

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TODAY = date(2026, 4, 20)


CUSTOM_FIELDS = {
    "data_criacao_origem": ("Data de criacao origem", "data"),
    "status_origem": ("Status origem", "texto"),
    "aceitou_jesus": ("Aceitou Jesus", "sim_nao"),
    "aceitou_jesus_contexto": ("Aceitou Jesus em", "texto"),
    "recem_convertido": ("Recem-convertido", "sim_nao"),
    "batizado": ("Batizado", "sim_nao"),
    "tipo_batismo": ("Tipo de batismo", "texto"),
    "forma_entrada": ("Forma de entrada", "texto"),
    "igreja_origem": ("Igreja de origem", "texto"),
    "criado_por_origem": ("Criado por origem", "texto"),
    "entrevistado_por": ("Entrevistado por", "texto"),
    "orgao_emissor_rg": ("Orgao emissor RG", "texto"),
    "uf_rg": ("UF do RG", "texto"),
    "escolaridade": ("Escolaridade", "texto"),
    "ocupacao": ("Ocupacao", "texto"),
    "tipo_sanguineo": ("Tipo sanguineo", "texto"),
    "nacionalidade": ("Nacionalidade", "texto"),
    "naturalidade": ("Naturalidade", "texto"),
    "data_casamento": ("Data de casamento", "data"),
    "cpf_original_revisao": ("CPF original em revisao", "texto"),
}

HEADER_TO_DESTINATION = {
    "Data de criacao": "campo_personalizado:data_criacao_origem",
    "Numero de membro": "pessoas.codigo_interno",
    "Nome completo": "pessoas.nome",
    "Sexo": "pessoas.sexo",
    "Tipo": "pessoa_perfis",
    "Igreja": "organizacoes/unidades",
    "Status": "campo_personalizado:status_origem",
    "E pastor?": "pessoa_perfis:pastor",
    "Faz parte da lideranca?": "pessoa_perfis:lider",
    "E recem-convertido?": "campo_personalizado:recem_convertido",
    "Aceitou Jesus?": "campo_personalizado:aceitou_jesus",
    "Aceitou Jesus em": "campo_personalizado:aceitou_jesus_contexto",
    "Data que aceitou Jesus": "pessoa_historico:aceitou_jesus",
    "E-Mail": "pessoa_contatos:email",
    "Telefone": "pessoa_contatos:telefone",
    "Celular": "pessoa_contatos:celular",
    "WhatsApp?": "pessoa_contatos:whatsapp",
    "Estado Civil": "pessoas.estado_civil",
    "Batizado?": "campo_personalizado:batizado",
    "Tipo de batismo": "campo_personalizado:tipo_batismo",
    "Batizado por": "pessoa_historico:batismo",
    "Data de entrada": "pessoa_historico:entrada",
    "Forma de entrada": "campo_personalizado:forma_entrada",
    "Aniversario": "pessoas.data_nascimento",
    "Data de Casamento": "campo_personalizado:data_casamento",
    "Data de Batismo": "pessoa_historico:batismo",
    "CPF": "pessoas.cpf",
    "Documento de Identificacao": "pessoas.rg",
    "Orgao Emissor": "campo_personalizado:orgao_emissor_rg",
    "UF do RG": "campo_personalizado:uf_rg",
    "Endereco": "pessoa_enderecos.logradouro",
    "Numero": "pessoa_enderecos.numero",
    "Complemento": "pessoa_enderecos.complemento",
    "Bairro": "pessoa_enderecos.bairro",
    "CEP": "pessoa_enderecos.cep",
    "Cidade": "pessoa_enderecos.cidade",
    "UF": "pessoa_enderecos.uf",
    "Criado por": "campo_personalizado:criado_por_origem",
    "Entrevistado por": "campo_personalizado:entrevistado_por",
    "Data de inatividade": "pessoa_historico:inatividade",
    "Motivo de inatividade": "pessoa_historico:inatividade",
    "Escolaridade": "campo_personalizado:escolaridade",
    "Ocupacao": "campo_personalizado:ocupacao",
    "Tipo sanguineo": "campo_personalizado:tipo_sanguineo",
    "Nacionalidade": "campo_personalizado:nacionalidade",
    "Naturalidade": "campo_personalizado:naturalidade",
    "Igreja de origem": "campo_personalizado:igreja_origem",
}

FIELD_ALIASES = {
    "Data de criacao": ("Data de criacao", "data_criacao"),
    "Numero de membro": ("Numero de membro", "numero_membro"),
    "Nome completo": ("Nome completo", "Nome Completo", "nome"),
    "Sexo": ("Sexo", "Genero", "sexo"),
    "Tipo": ("Tipo", "tipo_de_perfil"),
    "Status": ("Status", "status", "situacao_na_igreja"),
    "E pastor?": ("E pastor?", "pastor"),
    "Faz parte da lideranca?": ("Faz parte da lideranca?", "E lider?", "lider"),
    "E recem-convertido?": ("E recem-convertido?", "recem_convertido"),
    "Aceitou Jesus": ("Aceitou Jesus", "Aceitou Jesus?", "aceitou_jesus"),
    "Aceitou Jesus em": ("Aceitou Jesus em", "Aceitou Jesus em?", "aceitou_jesus_em"),
    "Data que aceitou Jesus": ("Data que aceitou Jesus", "Data que aceitou Jesus?", "data_aceitou_jesus"),
    "E-Mail": ("E-Mail", "E-mail", "email"),
    "Telefone": ("Telefone", "telefone_fixo"),
    "Celular": ("Celular", "Celular/Telefone", "telefone_celular"),
    "WhatsApp?": ("WhatsApp?", "whatsapp"),
    "Estado Civil": ("Estado Civil", "Estado civil", "estado_civil"),
    "Batizado?": ("Batizado?", "Batizado(a)?", "batizado"),
    "Tipo de batismo": ("Tipo de batismo", "tipo_batismo"),
    "Batizado por": ("Batizado por", "Batizado(a) por", "batizado_por"),
    "Data de entrada": ("Data de entrada", "Data de entrada na igreja", "data_entrada"),
    "Forma de entrada": ("Forma de entrada", "Forma de entrada na igreja", "forma_de_entrada"),
    "Aniversario": ("Aniversario", "Data de nascimento", "data_nascimento"),
    "Data de Casamento": ("Data de Casamento", "Data de casamento", "data_casamento"),
    "Data de Batismo": ("Data de Batismo", "Data do Batismo", "data_batismo"),
    "CPF": ("CPF", "numero_cpf"),
    "Documento de Identificacao": ("Documento de Identificacao", "identidade_numero"),
    "Orgao Emissor": ("Orgao Emissor", "identidade_orgao"),
    "UF do RG": ("UF do RG", "identidade_uf"),
    "Endereco": ("Endereco", "endereco_logradouro"),
    "Numero": ("Numero", "endereco_numero"),
    "Complemento": ("Complemento", "endereco_complemento"),
    "Bairro": ("Bairro", "endereco_bairro"),
    "CEP": ("CEP", "endereco_cep"),
    "Cidade": ("Cidade", "endereco_cidade"),
    "UF": ("UF", "endereco_uf"),
    "Criado por": ("Criado por", "Criado(a) por", "criado_por"),
    "Entrevistado por": ("Entrevistado por", "Entrevistado(a) por", "entrevistado_por"),
    "Data de inatividade": ("Data de inatividade", "data_inatividade"),
    "Motivo de inatividade": ("Motivo de inatividade", "motivo_inatividade"),
    "Escolaridade": ("Escolaridade", "escolaridade"),
    "Ocupacao": ("Ocupacao", "profissao"),
    "Tipo sanguineo": ("Tipo sanguineo", "tipo_sanguineo"),
    "Nacionalidade": ("Nacionalidade", "nacionalidade"),
    "Naturalidade": ("Naturalidade", "naturalidade"),
    "Igreja de origem": ("Igreja de origem", "igreja_origem"),
}

for canonical_header, aliases in FIELD_ALIASES.items():
    destination = HEADER_TO_DESTINATION.get(canonical_header)
    if not destination and canonical_header == "Nome completo":
        destination = HEADER_TO_DESTINATION.get("Nome completo")
    if not destination:
        continue
    for alias in aliases:
        HEADER_TO_DESTINATION.setdefault(alias, destination)


def ascii_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def canonicalize_member_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    for canonical_header, aliases in FIELD_ALIASES.items():
        if clean(normalized.get(canonical_header, "")):
            continue
        for alias in aliases:
            value = clean(row.get(alias, ""))
            if value:
                normalized[canonical_header] = value
                break
    return normalized


def normalize_label(value: str) -> str:
    text = unicodedata.normalize("NFKD", clean(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def yes_no(value: str) -> str:
    text = ascii_key(clean(value))
    if text in {"s", "sim", "yes", "y", "1"}:
        return "S"
    if text in {"n", "nao", "no", "0"}:
        return "N"
    return clean(value)


def normalize_sex(value: str) -> str:
    text = clean(value).lower()
    if text.startswith("masc"):
        return "masculino"
    if text.startswith("fem"):
        return "feminino"
    return clean(value).lower()


def normalize_estado_civil(value: str) -> str:
    text = clean(value)
    if not text or ascii_key(text) in {"escolha_unica", "selecione"}:
        return ""
    mapping = {
        "casado_a": "casado",
        "divorciado_a": "divorciado",
        "viuvo_a": "viuvo",
        "uniao_estavel": "uniao_estavel",
        "noivo_a": "noivo",
    }
    return mapping.get(ascii_key(text), ascii_key(text) or text.lower())


def normalize_address_number(value: str) -> str:
    text = clean(value)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def excel_serial_to_date(raw: str) -> date | None:
    try:
        number = float(raw)
    except ValueError:
        return None
    if number <= 0:
        return None
    return date(1899, 12, 30) + timedelta(days=int(number))


def parse_date_value(value: str) -> tuple[str, bool]:
    text = clean(value)
    if not text:
        return "", False
    if text in {"1/1/1", "01/01/0001", "1/1/0001"}:
        return "", True
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat(), False
        except ValueError:
            pass
    serial = excel_serial_to_date(text)
    if serial:
        return serial.isoformat(), False
    return "", True


def valid_cpf(value: str) -> bool:
    cpf = digits(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    nums = [int(ch) for ch in cpf]
    for digit in (9, 10):
        total = sum(nums[i] * (digit + 1 - i) for i in range(digit))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if nums[digit] != check:
            return False
    return True


def mask_cpf(value: str) -> str:
    cpf = digits(value)
    if len(cpf) < 5:
        return "***"
    return f"{cpf[:3]}***{cpf[-2:]}"


def col_to_idx(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch.upper()) - 64
    return number


def load_shared_strings(zip_file: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zip_file.namelist():
        return []
    root = ET.fromstring(zip_file.read("xl/sharedStrings.xml"))
    values = []
    for si in root.findall("main:si", NS):
        values.append("".join((t.text or "") for t in si.findall(".//main:t", NS)).strip())
    return values


def cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join((x.text or "") for x in cell.findall(".//main:t", NS)).strip()
    value = cell.find("main:v", NS)
    if value is None:
        return ""
    raw = (value.text or "").strip()
    if cell_type == "s":
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return raw
    return raw


def read_xlsx(path: Path) -> tuple[str, list[str], list[dict[str, str]]]:
    with ZipFile(path) as zip_file:
        workbook = ET.fromstring(zip_file.read("xl/workbook.xml"))
        sheets = workbook.findall("main:sheets/main:sheet", NS)
        sheet_name = sheets[0].attrib.get("name", "Dados") if sheets else "Dados"
        shared = load_shared_strings(zip_file)
        root = ET.fromstring(zip_file.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        max_col = 0
        for row in root.findall("main:sheetData/main:row", NS):
            values: dict[int, str] = {}
            for cell in row.findall("main:c", NS):
                index = col_to_idx(cell.attrib.get("r", ""))
                max_col = max(max_col, index)
                values[index] = cell_value(cell, shared)
            if values:
                rows.append([values.get(i, "") for i in range(1, max_col + 1)])
    if not rows:
        raise ValueError("A planilha nao possui linhas.")
    headers = [normalize_label(item) for item in rows[0]]
    records = []
    for row_index, row in enumerate(rows[1:], start=2):
        record = {headers[i]: clean(row[i]) if i < len(row) else "" for i in range(len(headers))}
        record["__row_number"] = str(row_index)
        records.append(record)
    return sheet_name, headers, records


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def setup_demo_org(conn: sqlite3.Connection) -> tuple[int, int, int]:
    conn.execute(
        """
        INSERT INTO organizacoes(nome, nome_fantasia, tipo, status, observacoes)
        VALUES (?, ?, 'igreja', 'ativa', ?)
        """,
        (
            "Primeira Igreja Batista de Niteroi",
            "PIB Niteroi",
            "Organizacao criada para importacao piloto da planilha de membros.",
        ),
    )
    org_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    conn.execute(
        "INSERT INTO unidades(organizacao_id, nome, tipo, cidade, uf, ativa) VALUES (?, ?, 'sede', ?, ?, 1)",
        (org_id, "Sede", "Niteroi", "RJ"),
    )
    unidade_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    conn.execute(
        "INSERT INTO usuarios(nome, email, ativo) VALUES (?, ?, 1)",
        ("Administrador Local", "admin.local@powerchurch.local"),
    )
    usuario_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    conn.execute(
        "INSERT INTO perfis_acesso(organizacao_id, nome, descricao, padrao, ativo) VALUES (?, 'Administrador', ?, 1, 1)",
        (org_id, "Perfil administrador local gerado pela importacao piloto."),
    )
    perfil_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    conn.execute(
        "INSERT INTO usuarios_organizacoes(usuario_id, organizacao_id, perfil_acesso_id, ativo) VALUES (?, ?, ?, 1)",
        (usuario_id, org_id, perfil_id),
    )
    for module_id in [row[0] for row in conn.execute("SELECT id FROM modulos")]:
        conn.execute(
            "INSERT OR IGNORE INTO modulos_organizacao(organizacao_id, modulo_id, ativo, plano, data_ativacao) VALUES (?, ?, 1, 'demo', DATE('now'))",
            (org_id, module_id),
        )
    return org_id, unidade_id, usuario_id


def ensure_custom_fields(conn: sqlite3.Connection, org_id: int) -> dict[str, int]:
    ids: dict[str, int] = {}
    for key, (name, field_type) in CUSTOM_FIELDS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO campos_personalizados(
                organizacao_id, modulo, registro_tipo, nome, chave, tipo,
                obrigatorio, visivel_no_cadastro, usar_em_relatorios, ativo
            ) VALUES (?, 'pessoas', 'pessoa', ?, ?, ?, 0, 1, 1, 1)
            """,
            (org_id, name, key, field_type),
        )
        ids[key] = int(
            scalar(
                conn,
                """
                SELECT id
                FROM campos_personalizados
                WHERE organizacao_id = ? AND registro_tipo = 'pessoa' AND chave = ?
                """,
                (org_id, key),
            )
        )
    return ids


def insert_custom_value(
    conn: sqlite3.Connection,
    org_id: int,
    field_ids: dict[str, int],
    pessoa_id: int,
    key: str,
    value: str,
) -> None:
    value = clean(value)
    if not value:
        return
    field_id = field_ids[key]
    field_type = CUSTOM_FIELDS[key][1]
    value_text = value
    value_data = None
    value_numero = None
    value_json = None
    if field_type == "data":
        parsed, invalid = parse_date_value(value)
        if parsed and not invalid:
            value_text = None
            value_data = parsed
        else:
            value_json = json.dumps({"valor_original": value, "data_invalida": True}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO valores_campos_personalizados(
            organizacao_id, campo_id, registro_tipo, registro_id,
            valor_texto, valor_numero, valor_data, valor_json
        ) VALUES (?, ?, 'pessoa', ?, ?, ?, ?, ?)
        """,
        (org_id, field_id, pessoa_id, value_text, value_numero, value_data, value_json),
    )


def add_pending(
    conn: sqlite3.Connection,
    lote_id: int,
    linha_id: int,
    tipo: str,
    severidade: str,
    descricao: str,
    acao: str,
) -> None:
    conn.execute(
        """
        INSERT INTO import_pendencias(lote_id, linha_id, tipo, severidade, descricao, acao_sugerida)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (lote_id, linha_id, tipo, severidade, descricao, acao),
    )


def add_profile(conn: sqlite3.Connection, org_id: int, pessoa_id: int, profile: str, obs: str = "") -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO pessoa_perfis(organizacao_id, pessoa_id, perfil, ativo, observacoes)
        VALUES (?, ?, ?, 1, ?)
        """,
        (org_id, pessoa_id, profile, obs),
    )


def add_contact(
    conn: sqlite3.Connection,
    org_id: int,
    pessoa_id: int,
    tipo: str,
    valor: str,
    principal: int,
    observacoes: dict[str, object] | None = None,
) -> None:
    valor = clean(valor)
    if not valor:
        return
    conn.execute(
        """
        INSERT INTO pessoa_contatos(organizacao_id, pessoa_id, tipo, valor, principal, observacoes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            org_id,
            pessoa_id,
            tipo,
            valor,
            principal,
            json.dumps(observacoes or {}, ensure_ascii=False),
        ),
    )


def add_history(
    conn: sqlite3.Connection,
    org_id: int,
    pessoa_id: int,
    import_lote_id: int,
    tipo: str,
    data_evento: str,
    titulo: str,
    descricao: str,
    origem: str = "",
    destino: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO pessoa_historico(
            organizacao_id, pessoa_id, tipo_evento, data_evento, titulo,
            descricao, origem, destino, import_lote_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (org_id, pessoa_id, tipo, data_evento or None, titulo, descricao, origem, destino, import_lote_id),
    )


def precompute_duplicate_keys(records: list[dict[str, str]]) -> dict[str, set[str]]:
    cpf_counter = Counter(digits(row.get("CPF", "")) for row in records if digits(row.get("CPF", "")))
    name_counter = Counter(clean(row.get("Nome completo", "")).casefold() for row in records if clean(row.get("Nome completo", "")))
    member_counter = Counter(clean(row.get("Numero de membro", "")) for row in records if clean(row.get("Numero de membro", "")))
    return {
        "cpf": {key for key, count in cpf_counter.items() if count > 1},
        "nome": {key for key, count in name_counter.items() if count > 1},
        "codigo": {key for key, count in member_counter.items() if count > 1},
    }


def create_import_lote(
    conn: sqlite3.Connection,
    org_id: int,
    unidade_id: int,
    usuario_id: int,
    source: Path,
    total_rows: int,
    tipo_importacao: str = "pessoas_membros",
) -> int:
    conn.execute(
        """
        INSERT INTO import_lotes(
            organizacao_id, unidade_id, tipo_importacao, arquivo_nome, arquivo_hash,
            status, total_linhas, criado_por_usuario_id, confirmado_em
        ) VALUES (?, ?, ?, ?, ?, 'confirmado', ?, ?, CURRENT_TIMESTAMP)
        """,
        (org_id, unidade_id, tipo_importacao, source.name, sha256_file(source), total_rows, usuario_id),
    )
    return int(scalar(conn, "SELECT last_insert_rowid()"))


def register_mappings(conn: sqlite3.Connection, lote_id: int, headers: list[str], field_ids: dict[str, int]) -> None:
    for header in headers:
        destination = HEADER_TO_DESTINATION.get(header, "revisar")
        custom_id = None
        if destination.startswith("campo_personalizado:"):
            custom_id = field_ids.get(destination.split(":", 1)[1])
            action = "criar_campo_personalizado"
        elif destination == "revisar":
            action = "revisar_depois"
        else:
            action = "mapear_campo"
        conn.execute(
            """
            INSERT INTO import_mapeamentos(lote_id, coluna_origem, campo_destino, acao, campo_personalizado_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lote_id, header, destination, action, custom_id),
        )


def first_id(conn: sqlite3.Connection, table: str) -> int:
    value = scalar(conn, f"SELECT id FROM {table} ORDER BY id LIMIT 1")
    return int(value or 0)


def default_context(conn: sqlite3.Connection) -> tuple[int, int, int]:
    org_id = first_id(conn, "organizacoes")
    unidade_id = first_id(conn, "unidades")
    usuario_id = first_id(conn, "usuarios")
    if not org_id:
        raise ValueError("Banco sem organizacao cadastrada.")
    return org_id, unidade_id, usuario_id


def backup_database(db_path: Path, label: str) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db_path.stem}_{label}_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, target)
    return target


def desired_status_from_row(row: dict[str, str]) -> str:
    tipo = ascii_key(row.get("Tipo", ""))
    status_text = ascii_key(row.get("Status", ""))
    inativacao, _invalid = parse_date_value(row.get("Data de inatividade", ""))
    motivo_inatividade = clean(row.get("Motivo de inatividade", ""))
    if tipo in {"visitante", "visistante"} or "visitante" in status_text:
        return "visitante"
    if tipo in {"frequentador", "frequentante"} or "frequentador" in status_text:
        return "frequentador"
    if tipo in {"arquivo_morto", "arquivo morto"} or "arquivo_morto" in status_text:
        return "arquivo_morto"
    return "membro_inativo" if inativacao or motivo_inatividade else "membro_ativo"


def profile_from_row(row: dict[str, str]) -> str:
    tipo = ascii_key(row.get("Tipo", ""))
    status_text = ascii_key(row.get("Status", ""))
    if tipo == "membro":
        return "membro"
    if tipo in {"visitante", "frequentador"}:
        return tipo
    if "visitante" in status_text:
        return "visitante"
    if "frequentador" in status_text:
        return "frequentador"
    return tipo or "membro"


def single_person_by_field(conn: sqlite3.Connection, org_id: int, field: str, value: str) -> sqlite3.Row | None:
    if not value:
        return None
    return conn.execute(
        f"""
        SELECT *
        FROM pessoas
        WHERE organizacao_id = ? AND {field} = ? AND ativo = 1
        ORDER BY id
        LIMIT 1
        """,
        (org_id, value),
    ).fetchone()


def person_by_name_birth(conn: sqlite3.Connection, org_id: int, nome: str, nascimento: str) -> sqlite3.Row | None:
    if not nome or not nascimento:
        return None
    rows = conn.execute(
        """
        SELECT *
        FROM pessoas
        WHERE organizacao_id = ?
          AND data_nascimento = ?
          AND UPPER(nome) = UPPER(?)
          AND ativo = 1
        ORDER BY id
        LIMIT 2
        """,
        (org_id, nascimento, nome),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def has_contact(conn: sqlite3.Connection, pessoa_id: int, tipo: str, valor: str) -> bool:
    value = clean(valor)
    if not value:
        return True
    comparable = digits(value) if tipo in {"telefone", "celular"} else value.lower()
    rows = conn.execute(
        "SELECT valor FROM pessoa_contatos WHERE pessoa_id = ? AND tipo = ?",
        (pessoa_id, tipo),
    ).fetchall()
    for row in rows:
        current = digits(row["valor"]) if tipo in {"telefone", "celular"} else clean(row["valor"]).lower()
        if current and current == comparable:
            return True
    return False


def has_custom_value(conn: sqlite3.Connection, pessoa_id: int, field_id: int) -> bool:
    return bool(
        scalar(
            conn,
            """
            SELECT 1
            FROM valores_campos_personalizados
            WHERE registro_tipo = 'pessoa' AND registro_id = ? AND campo_id = ?
            LIMIT 1
            """,
            (pessoa_id, field_id),
        )
    )


def has_history(conn: sqlite3.Connection, pessoa_id: int, tipo: str, data_evento: str, titulo: str) -> bool:
    return bool(
        scalar(
            conn,
            """
            SELECT 1
            FROM pessoa_historico
            WHERE pessoa_id = ?
              AND tipo_evento = ?
              AND COALESCE(data_evento, '') = COALESCE(?, '')
              AND titulo = ?
            LIMIT 1
            """,
            (pessoa_id, tipo, data_evento or None, titulo),
        )
    )


def update_blank_person_fields(
    conn: sqlite3.Connection,
    pessoa: sqlite3.Row,
    values: dict[str, object],
) -> int:
    assignments: list[str] = []
    params: list[object] = []
    for field, value in values.items():
        if value in {"", None}:
            continue
        if clean(pessoa[field]):
            continue
        assignments.append(f"{field} = ?")
        params.append(value)
    if not assignments:
        return 0
    assignments.append("atualizado_em = CURRENT_TIMESTAMP")
    params.append(pessoa["id"])
    conn.execute(
        f"UPDATE pessoas SET {', '.join(assignments)} WHERE id = ?",
        params,
    )
    return len(assignments) - 1


def add_address_if_missing(conn: sqlite3.Connection, org_id: int, pessoa_id: int, row: dict[str, str]) -> int:
    if not any(clean(row.get(h, "")) for h in ["Endereco", "Numero", "Complemento", "Bairro", "CEP", "Cidade", "UF"]):
        return 0
    existing = scalar(
        conn,
        "SELECT 1 FROM pessoa_enderecos WHERE pessoa_id = ? AND principal = 1 LIMIT 1",
        (pessoa_id,),
    )
    if existing:
        return 0
    conn.execute(
        """
        INSERT INTO pessoa_enderecos(
            organizacao_id, pessoa_id, tipo, cep, logradouro, numero,
            complemento, bairro, cidade, uf, principal
        ) VALUES (?, ?, 'residencial', ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            org_id,
            pessoa_id,
            digits(row.get("CEP", "")) or None,
            clean(row.get("Endereco", "")) or None,
            normalize_address_number(row.get("Numero", "")) or None,
            clean(row.get("Complemento", "")) or None,
            clean(row.get("Bairro", "")) or None,
            clean(row.get("Cidade", "")) or None,
            clean(row.get("UF", "")) or None,
        ),
    )
    return 1


def insert_person_from_row(
    conn: sqlite3.Connection,
    org_id: int,
    unidade_id: int,
    lote_id: int,
    field_ids: dict[str, int],
    row: dict[str, str],
    cpf_to_insert: str,
    nascimento: str,
    status: str,
) -> int:
    email = clean(row.get("E-Mail", ""))
    conn.execute(
        """
        INSERT INTO pessoas(
            organizacao_id, unidade_preferencial_id, codigo_interno, nome, cpf, rg,
            data_nascimento, sexo, estado_civil, email_principal, telefone_principal,
            whatsapp_principal, status, arquivo_morto, observacoes, import_lote_id, ativo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            org_id,
            unidade_id or None,
            clean(row.get("Numero de membro", "")) or None,
            clean(row.get("Nome completo", "")) or "Nome nao informado",
            cpf_to_insert or None,
            clean(row.get("Documento de Identificacao", "")) or None,
            nascimento or None,
            normalize_sex(row.get("Sexo", "")) or None,
            normalize_estado_civil(row.get("Estado Civil", "")) or None,
            email or None,
            clean(row.get("Celular", "")) or clean(row.get("Telefone", "")) or None,
            clean(row.get("Celular", "")) if yes_no(row.get("WhatsApp?", "")) == "S" else None,
            status,
            1 if status == "arquivo_morto" else 0,
            "Importado por complemento incremental.",
            lote_id,
        ),
    )
    pessoa_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    return pessoa_id


def apply_related_person_data(
    conn: sqlite3.Connection,
    org_id: int,
    pessoa_id: int,
    lote_id: int,
    field_ids: dict[str, int],
    row: dict[str, str],
    can_add_profile: bool,
) -> Counter:
    stats = Counter()
    profile = profile_from_row(row)
    if can_add_profile and profile:
        before = int(scalar(conn, "SELECT COUNT(*) FROM pessoa_perfis WHERE pessoa_id = ?", (pessoa_id,)) or 0)
        add_profile(conn, org_id, pessoa_id, profile)
        after = int(scalar(conn, "SELECT COUNT(*) FROM pessoa_perfis WHERE pessoa_id = ?", (pessoa_id,)) or 0)
        if after > before:
            stats["perfis_adicionados"] += 1
    if yes_no(row.get("E pastor?", "")) == "S":
        add_profile(conn, org_id, pessoa_id, "pastor")
    if yes_no(row.get("Faz parte da lideranca?", "")) == "S":
        add_profile(conn, org_id, pessoa_id, "lider")

    contacts = [
        ("email", clean(row.get("E-Mail", "")), 1, {"origem": "E-Mail"}),
        ("telefone", row.get("Telefone", ""), 0, {"origem": "Telefone", "normalizado": digits(row.get("Telefone", ""))}),
        ("celular", row.get("Celular", ""), 1, {"origem": "Celular", "normalizado": digits(row.get("Celular", "")), "whatsapp": yes_no(row.get("WhatsApp?", "")) == "S"}),
    ]
    for tipo, valor, principal, obs in contacts:
        if clean(valor) and not has_contact(conn, pessoa_id, tipo, valor):
            add_contact(conn, org_id, pessoa_id, tipo, valor, principal, obs)
            stats["contatos_adicionados"] += 1

    stats["enderecos_adicionados"] += add_address_if_missing(conn, org_id, pessoa_id, row)

    custom_source = {
        "data_criacao_origem": row.get("Data de criacao", ""),
        "status_origem": row.get("Status", ""),
        "aceitou_jesus": yes_no(row.get("Aceitou Jesus", "")),
        "aceitou_jesus_contexto": row.get("Aceitou Jesus em", ""),
        "recem_convertido": yes_no(row.get("E recem-convertido?", "")),
        "batizado": yes_no(row.get("Batizado?", "")),
        "tipo_batismo": row.get("Tipo de batismo", ""),
        "forma_entrada": row.get("Forma de entrada", ""),
        "igreja_origem": row.get("Igreja de origem", ""),
        "criado_por_origem": row.get("Criado por", ""),
        "entrevistado_por": row.get("Entrevistado por", ""),
        "orgao_emissor_rg": row.get("Orgao Emissor", ""),
        "uf_rg": row.get("UF do RG", ""),
        "escolaridade": row.get("Escolaridade", ""),
        "ocupacao": row.get("Ocupacao", ""),
        "tipo_sanguineo": row.get("Tipo sanguineo", ""),
        "nacionalidade": row.get("Nacionalidade", ""),
        "naturalidade": row.get("Naturalidade", ""),
        "data_casamento": row.get("Data de Casamento", ""),
    }
    for key, value in custom_source.items():
        field_id = field_ids.get(key)
        if field_id and clean(value) and not has_custom_value(conn, pessoa_id, field_id):
            insert_custom_value(conn, org_id, field_ids, pessoa_id, key, value)
            stats["campos_personalizados_adicionados"] += 1

    history_specs = [
        ("Data que aceitou Jesus", "aceitou_jesus", "Aceitou Jesus", "Evento importado por complemento incremental."),
        ("Data de entrada", "entrada_membresia", "Entrada na membresia", clean(row.get("Forma de entrada", "")) or "Entrada importada por complemento incremental."),
        ("Data de Batismo", "batismo", "Batismo", f"Tipo: {clean(row.get('Tipo de batismo', '')) or 'nao informado'}"),
    ]
    for source_field, event_type, title, description in history_specs:
        event_date, invalid = parse_date_value(row.get(source_field, ""))
        if invalid or not event_date:
            continue
        if not has_history(conn, pessoa_id, event_type, event_date, title):
            add_history(conn, org_id, pessoa_id, lote_id, event_type, event_date, title, description)
            stats["historicos_adicionados"] += 1

    inactive_date, _invalid = parse_date_value(row.get("Data de inatividade", ""))
    inactive_reason = clean(row.get("Motivo de inatividade", ""))
    if inactive_date or inactive_reason:
        if not has_history(conn, pessoa_id, "inatividade", inactive_date, "Membro inativo"):
            add_history(
                conn,
                org_id,
                pessoa_id,
                lote_id,
                "inatividade",
                inactive_date,
                "Membro inativo",
                inactive_reason or "Inatividade importada por complemento incremental.",
            )
            stats["historicos_adicionados"] += 1
    return stats


def import_members_incremental(
    source: Path,
    db_path: Path,
    report_path: Path,
    allow_duplicate_file: bool = False,
) -> dict[str, object]:
    if not db_path.exists():
        raise FileNotFoundError(f"Banco nao encontrado para importacao incremental: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_name, headers, raw_records = read_xlsx(source)
    records = [canonicalize_member_row(row) for row in raw_records]
    file_hash = sha256_file(source)

    backup_path = backup_database(db_path, "before_incremental_people_import")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if not allow_duplicate_file:
            previous = conn.execute(
                "SELECT id FROM import_lotes WHERE arquivo_hash = ? ORDER BY id DESC LIMIT 1",
                (file_hash,),
            ).fetchone()
            if previous is not None:
                raise ValueError(f"Este arquivo ja foi importado no lote #{previous['id']}.")
        org_id, unidade_id, usuario_id = default_context(conn)
        field_ids = ensure_custom_fields(conn, org_id)
        lote_id = create_import_lote(
            conn,
            org_id,
            unidade_id,
            usuario_id,
            source,
            len(records),
            tipo_importacao="pessoas_complementar_incremental",
        )
        conn.execute(
            "INSERT INTO import_abas(lote_id, nome_aba, total_linhas) VALUES (?, ?, ?)",
            (lote_id, sheet_name, len(records)),
        )
        aba_id = int(scalar(conn, "SELECT last_insert_rowid()"))
        register_mappings(conn, lote_id, headers, field_ids)
        duplicate_keys = precompute_duplicate_keys(records)
        stats = Counter()
        pending_counts = Counter()
        for raw_row, row in zip(raw_records, records):
            original_json = json.dumps(raw_row, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO import_linhas(lote_id, aba_id, numero_linha, status, dados_originais_json)
                VALUES (?, ?, ?, 'processando', ?)
                """,
                (lote_id, aba_id, int(row["__row_number"]), original_json),
            )
            linha_id = int(scalar(conn, "SELECT last_insert_rowid()"))
            cpf_digits = digits(row.get("CPF", ""))
            cpf_to_insert = cpf_digits if cpf_digits and valid_cpf(cpf_digits) and cpf_digits not in duplicate_keys["cpf"] else ""
            if cpf_digits and not cpf_to_insert:
                pending_counts["cpf_invalido_ou_duplicado"] += 1
                add_pending(
                    conn,
                    lote_id,
                    linha_id,
                    "cpf_invalido_ou_duplicado",
                    "aviso",
                    f"CPF nao foi usado como chave automatica ({mask_cpf(cpf_digits)}).",
                    "Conferir CPF antes de vincular manualmente.",
                )
            nascimento, nascimento_invalid = parse_date_value(row.get("Aniversario", ""))
            if nascimento_invalid:
                pending_counts["data_nascimento_invalida"] += 1
                add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data de nascimento invalida.", "Revisar aniversario.")

            codigo = clean(row.get("Numero de membro", ""))
            if codigo and codigo in duplicate_keys["codigo"]:
                pending_counts["numero_membro_duplicado_no_arquivo"] += 1
                add_pending(
                    conn,
                    lote_id,
                    linha_id,
                    "numero_membro_duplicado_no_arquivo",
                    "aviso",
                    "Numero de membro repetido no proprio arquivo complementar.",
                    "Conferir manualmente antes de usar como chave de atualizacao.",
                )
                codigo = ""
            nome = clean(row.get("Nome completo", ""))
            by_cpf = single_person_by_field(conn, org_id, "cpf", cpf_to_insert) if cpf_to_insert else None
            by_code = single_person_by_field(conn, org_id, "codigo_interno", codigo) if codigo else None
            by_name_birth = None if by_cpf or by_code else person_by_name_birth(conn, org_id, nome, nascimento)
            candidate_ids = {int(item["id"]) for item in [by_cpf, by_code, by_name_birth] if item is not None}
            desired_status = desired_status_from_row(row)
            status_changed = False
            if len(candidate_ids) > 1:
                pending_counts["conflito_chaves"] += 1
                add_pending(
                    conn,
                    lote_id,
                    linha_id,
                    "conflito_chaves",
                    "aviso",
                    "CPF, numero de membro ou nome/data apontam para pessoas diferentes.",
                    "Resolver manualmente antes de importar esta linha.",
                )
                conn.execute(
                    "UPDATE import_linhas SET status = 'conflito', dados_normalizados_json = ? WHERE id = ?",
                    (json.dumps({"cpf": mask_cpf(cpf_digits), "codigo_interno": codigo, "nome": nome}, ensure_ascii=False), linha_id),
                )
                stats["conflitos"] += 1
                continue

            person = by_cpf or by_code or by_name_birth
            if person is None:
                if codigo and single_person_by_field(conn, org_id, "codigo_interno", codigo):
                    codigo = ""
                pessoa_id = insert_person_from_row(conn, org_id, unidade_id, lote_id, field_ids, row, cpf_to_insert, nascimento, desired_status)
                related_stats = apply_related_person_data(conn, org_id, pessoa_id, lote_id, field_ids, row, can_add_profile=True)
                stats.update(related_stats)
                conn.execute(
                    """
                    UPDATE import_linhas
                    SET status = 'importado', registro_tipo = 'pessoa', registro_id = ?, dados_normalizados_json = ?
                    WHERE id = ?
                    """,
                    (
                        pessoa_id,
                        json.dumps({"acao": "criado", "status": desired_status}, ensure_ascii=False),
                        linha_id,
                    ),
                )
                stats["criados"] += 1
            else:
                pessoa_id = int(person["id"])
                current_status = clean(person["status"])
                if desired_status and current_status and desired_status != current_status:
                    status_changed = True
                    pending_counts["mudanca_status_detectada"] += 1
                    add_pending(
                        conn,
                        lote_id,
                        linha_id,
                        "mudanca_status_detectada",
                        "aviso",
                        f"Complemento sugere status '{desired_status}', mas a ficha atual esta como '{current_status}'.",
                        "Conferir promocao, inativacao ou mudanca de perfil antes de alterar automaticamente.",
                    )
                updates = {
                    "codigo_interno": codigo,
                    "cpf": cpf_to_insert,
                    "rg": clean(row.get("Documento de Identificacao", "")) or None,
                    "data_nascimento": nascimento or None,
                    "sexo": normalize_sex(row.get("Sexo", "")) or None,
                    "estado_civil": normalize_estado_civil(row.get("Estado Civil", "")) or None,
                    "email_principal": clean(row.get("E-Mail", "")) or None,
                    "telefone_principal": clean(row.get("Celular", "")) or clean(row.get("Telefone", "")) or None,
                    "whatsapp_principal": clean(row.get("Celular", "")) if yes_no(row.get("WhatsApp?", "")) == "S" else None,
                }
                field_updates = update_blank_person_fields(conn, person, updates)
                related_stats = apply_related_person_data(
                    conn,
                    org_id,
                    pessoa_id,
                    lote_id,
                    field_ids,
                    row,
                    can_add_profile=not status_changed,
                )
                stats.update(related_stats)
                if field_updates:
                    stats["atualizados_campos_vazios"] += 1
                if by_name_birth is not None:
                    pending_counts["match_nome_data"] += 1
                    add_pending(
                        conn,
                        lote_id,
                        linha_id,
                        "match_nome_data",
                        "info",
                        "Linha vinculada por nome completo e data de nascimento, sem CPF/numero forte.",
                        "Conferir apenas se houver homonimos.",
                    )
                pending_for_line = int(scalar(conn, "SELECT COUNT(*) FROM import_pendencias WHERE linha_id = ?", (linha_id,)) or 0)
                status = "atualizado_com_pendencia" if pending_for_line else "atualizado"
                conn.execute(
                    """
                    UPDATE import_linhas
                    SET status = ?, registro_tipo = 'pessoa', registro_id = ?, dados_normalizados_json = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        pessoa_id,
                        json.dumps({"acao": "atualizado_incremental", "campos_preenchidos": field_updates}, ensure_ascii=False),
                        linha_id,
                    ),
                )
                stats["existentes_encontrados"] += 1
        total_pendencias = int(scalar(conn, "SELECT COUNT(*) FROM import_pendencias WHERE lote_id = ?", (lote_id,)) or 0)
        conn.execute(
            """
            UPDATE import_lotes
            SET linhas_importadas = ?, linhas_ignoradas = ?, linhas_com_erro = ?
            WHERE id = ?
            """,
            (
                stats["criados"] + stats["existentes_encontrados"],
                stats["conflitos"],
                total_pendencias,
                lote_id,
            ),
        )
        conn.commit()
        summary = {
            "source": str(source),
            "db_path": str(db_path),
            "backup_path": str(backup_path),
            "report_path": str(report_path),
            "sheet_name": sheet_name,
            "records": len(records),
            "lote_id": lote_id,
            "pessoas_total": int(scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1") or 0),
            "pendencias": total_pendencias,
            "stats": dict(stats),
            "pending_counts": dict(pending_counts),
        }
        write_incremental_report(conn, summary, report_path)
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_members(source: Path, db_path: Path, report_path: Path) -> dict[str, object]:
    if db_path.exists():
        raise FileExistsError(f"Banco ja existe: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    sheet_name, headers, raw_records = read_xlsx(source)
    records = [canonicalize_member_row(row) for row in raw_records]
    duplicate_keys = precompute_duplicate_keys(records)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    execute_schema(conn)
    org_id, unidade_id, usuario_id = setup_demo_org(conn)
    field_ids = ensure_custom_fields(conn, org_id)
    lote_id = create_import_lote(conn, org_id, unidade_id, usuario_id, source, len(records))
    conn.execute(
        "INSERT INTO import_abas(lote_id, nome_aba, total_linhas) VALUES (?, ?, ?)",
        (lote_id, sheet_name, len(records)),
    )
    aba_id = int(scalar(conn, "SELECT last_insert_rowid()"))
    register_mappings(conn, lote_id, headers, field_ids)

    stats = Counter()
    pending_counts = Counter()
    status_counts = Counter()
    profile_counts = Counter()
    custom_counts = Counter()
    used_cpfs: set[str] = set()

    for raw_row, row in zip(raw_records, records):
        original_json = json.dumps(raw_row, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO import_linhas(lote_id, aba_id, numero_linha, status, dados_originais_json)
            VALUES (?, ?, ?, 'processando', ?)
            """,
            (lote_id, aba_id, int(row["__row_number"]), original_json),
        )
        linha_id = int(scalar(conn, "SELECT last_insert_rowid()"))

        cpf_raw = row.get("CPF", "")
        cpf_digits = digits(cpf_raw)
        cpf_to_insert = ""
        if cpf_digits:
            if not valid_cpf(cpf_digits):
                pending_counts["cpf_invalido"] += 1
                add_pending(
                    conn,
                    lote_id,
                    linha_id,
                    "cpf_invalido",
                    "aviso",
                    f"CPF invalido preservado para revisao ({mask_cpf(cpf_digits)}).",
                    "Revisar CPF no cadastro da pessoa.",
                )
            elif cpf_digits in duplicate_keys["cpf"] or cpf_digits in used_cpfs:
                pending_counts["cpf_duplicado"] += 1
                add_pending(
                    conn,
                    lote_id,
                    linha_id,
                    "cpf_duplicado",
                    "aviso",
                    f"CPF duplicado preservado para revisao ({mask_cpf(cpf_digits)}).",
                    "Conferir se os registros representam a mesma pessoa.",
                )
            else:
                cpf_to_insert = cpf_digits
                used_cpfs.add(cpf_digits)

        nascimento, nascimento_invalid = parse_date_value(row.get("Aniversario", ""))
        if nascimento_invalid:
            pending_counts["data_nascimento_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data de nascimento invalida.", "Revisar aniversario.")

        estado_civil_raw = row.get("Estado Civil", "")
        estado_civil = normalize_estado_civil(estado_civil_raw)
        if clean(estado_civil_raw) and not estado_civil:
            stats["estado_civil_tratado_como_vazio"] += 1

        inativacao, inativacao_invalid = parse_date_value(row.get("Data de inatividade", ""))
        motivo_inatividade = clean(row.get("Motivo de inatividade", ""))
        status = "membro_inativo" if inativacao or motivo_inatividade else "membro_ativo"
        status_counts[status] += 1

        nome = clean(row.get("Nome completo", ""))
        if nome.casefold() in duplicate_keys["nome"]:
            pending_counts["nome_repetido"] += 1
            add_pending(
                conn,
                lote_id,
                linha_id,
                "nome_repetido",
                "info",
                "Nome repetido na planilha.",
                "Conferir junto com CPF, contato e data de nascimento.",
            )

        codigo_interno = clean(row.get("Numero de membro", ""))
        if not codigo_interno:
            pending_counts["numero_membro_vazio"] += 1
            add_pending(conn, lote_id, linha_id, "numero_membro_vazio", "aviso", "Numero de membro vazio.", "Revisar identificador interno.")

        if nascimento:
            born = datetime.strptime(nascimento, "%Y-%m-%d").date()
            age = TODAY.year - born.year - ((TODAY.month, TODAY.day) < (born.month, born.day))
            if age < 0 or age > 105:
                pending_counts["idade_suspeita"] += 1
                add_pending(conn, lote_id, linha_id, "idade_suspeita", "aviso", "Idade calculada fora do padrao esperado.", "Revisar data de nascimento.")
            elif age < 16:
                pending_counts["menor_16"] += 1
                add_pending(conn, lote_id, linha_id, "menor_16", "info", "Pessoa menor de 16 anos marcada como membro.", "Conferir regra de membresia infantil/juvenil.")

        email = clean(row.get("E-Mail", ""))
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            pending_counts["email_invalido"] += 1
            add_pending(conn, lote_id, linha_id, "email_invalido", "info", "E-mail com formato possivelmente invalido.", "Revisar contato.")

        conn.execute(
            """
            INSERT INTO pessoas(
                organizacao_id, unidade_preferencial_id, codigo_interno, nome, cpf, rg,
                data_nascimento, sexo, estado_civil, email_principal, telefone_principal,
                whatsapp_principal, status, arquivo_morto, observacoes, import_lote_id, ativo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1)
            """,
            (
                org_id,
                unidade_id,
                codigo_interno or None,
                nome or "Nome nao informado",
                cpf_to_insert or None,
                clean(row.get("Documento de Identificacao", "")) or None,
                nascimento or None,
                normalize_sex(row.get("Sexo", "")) or None,
                estado_civil or None,
                email or None,
                clean(row.get("Celular", "")) or clean(row.get("Telefone", "")) or None,
                clean(row.get("Celular", "")) if yes_no(row.get("WhatsApp?", "")) == "S" else None,
                status,
                "Importado da planilha de membros.",
                lote_id,
            ),
        )
        pessoa_id = int(scalar(conn, "SELECT last_insert_rowid()"))

        if cpf_digits and cpf_digits != cpf_to_insert:
            insert_custom_value(conn, org_id, field_ids, pessoa_id, "cpf_original_revisao", cpf_digits)
            custom_counts["cpf_original_revisao"] += 1

        conn.execute(
            "UPDATE import_linhas SET status = 'importado', registro_tipo = 'pessoa', registro_id = ? WHERE id = ?",
            (pessoa_id, linha_id),
        )

        tipo = ascii_key(row.get("Tipo", ""))
        if tipo:
            add_profile(conn, org_id, pessoa_id, "membro" if tipo == "membro" else tipo)
            profile_counts["membro" if tipo == "membro" else tipo] += 1
        if yes_no(row.get("E pastor?", "")) == "S":
            add_profile(conn, org_id, pessoa_id, "pastor")
            profile_counts["pastor"] += 1
        if yes_no(row.get("Faz parte da lideranca?", "")) == "S":
            add_profile(conn, org_id, pessoa_id, "lider")
            profile_counts["lider"] += 1

        add_contact(
            conn,
            org_id,
            pessoa_id,
            "email",
            email,
            1,
            {"origem": "E-Mail", "formato_valido": bool(not email or re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))},
        )
        add_contact(
            conn,
            org_id,
            pessoa_id,
            "telefone",
            row.get("Telefone", ""),
            0,
            {"origem": "Telefone", "normalizado": digits(row.get("Telefone", ""))},
        )
        add_contact(
            conn,
            org_id,
            pessoa_id,
            "celular",
            row.get("Celular", ""),
            1,
            {"origem": "Celular", "normalizado": digits(row.get("Celular", "")), "whatsapp": yes_no(row.get("WhatsApp?", "")) == "S"},
        )

        if any(clean(row.get(h, "")) for h in ["Endereco", "Numero", "Complemento", "Bairro", "CEP", "Cidade", "UF"]):
            conn.execute(
                """
                INSERT INTO pessoa_enderecos(
                    organizacao_id, pessoa_id, tipo, cep, logradouro, numero,
                    complemento, bairro, cidade, uf, principal
                ) VALUES (?, ?, 'residencial', ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    org_id,
                    pessoa_id,
                    digits(row.get("CEP", "")) or None,
                    clean(row.get("Endereco", "")) or None,
                    normalize_address_number(row.get("Numero", "")) or None,
                    clean(row.get("Complemento", "")) or None,
                    clean(row.get("Bairro", "")) or None,
                    clean(row.get("Cidade", "")) or None,
                    clean(row.get("UF", "")) or None,
                ),
            )
            if clean(row.get("Numero", "")) != normalize_address_number(row.get("Numero", "")):
                stats["numero_endereco_normalizado"] += 1

        # Custom/accessory fields.
        custom_source = {
            "data_criacao_origem": row.get("Data de criacao", ""),
            "status_origem": row.get("Status", ""),
            "aceitou_jesus": yes_no(row.get("Aceitou Jesus", "")),
            "aceitou_jesus_contexto": row.get("Aceitou Jesus em", ""),
            "recem_convertido": yes_no(row.get("E recem-convertido?", "")),
            "batizado": yes_no(row.get("Batizado?", "")),
            "tipo_batismo": row.get("Tipo de batismo", ""),
            "forma_entrada": row.get("Forma de entrada", ""),
            "igreja_origem": row.get("Igreja de origem", ""),
            "criado_por_origem": row.get("Criado por", ""),
            "entrevistado_por": row.get("Entrevistado por", ""),
            "orgao_emissor_rg": row.get("Orgao Emissor", ""),
            "uf_rg": row.get("UF do RG", ""),
            "escolaridade": row.get("Escolaridade", ""),
            "ocupacao": row.get("Ocupacao", ""),
            "tipo_sanguineo": row.get("Tipo sanguineo", ""),
            "nacionalidade": row.get("Nacionalidade", ""),
            "naturalidade": row.get("Naturalidade", ""),
            "data_casamento": row.get("Data de Casamento", ""),
        }
        for key, value in custom_source.items():
            before = int(scalar(conn, "SELECT COUNT(*) FROM valores_campos_personalizados WHERE registro_id = ?", (pessoa_id,)) or 0)
            insert_custom_value(conn, org_id, field_ids, pessoa_id, key, value)
            after = int(scalar(conn, "SELECT COUNT(*) FROM valores_campos_personalizados WHERE registro_id = ?", (pessoa_id,)) or 0)
            if after > before:
                custom_counts[key] += 1

        # Historic events.
        accepted_date, accepted_invalid = parse_date_value(row.get("Data que aceitou Jesus", ""))
        if accepted_invalid:
            pending_counts["data_aceitou_jesus_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data que aceitou Jesus invalida.", "Revisar data.")
        if accepted_date:
            add_history(
                conn,
                org_id,
                pessoa_id,
                lote_id,
                "aceitou_jesus",
                accepted_date,
                "Aceitou Jesus",
                "Evento importado da planilha de membros.",
                origem=clean(row.get("Aceitou Jesus em", "")),
            )

        entry_date, entry_invalid = parse_date_value(row.get("Data de entrada", ""))
        if entry_invalid:
            pending_counts["data_entrada_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data de entrada invalida.", "Revisar data.")
        if entry_date:
            add_history(
                conn,
                org_id,
                pessoa_id,
                lote_id,
                "entrada_membresia",
                entry_date,
                "Entrada na membresia",
                clean(row.get("Forma de entrada", "")) or "Entrada importada da planilha.",
                origem=clean(row.get("Igreja de origem", "")),
                destino=clean(row.get("Igreja", "")),
            )

        baptism_date, baptism_invalid = parse_date_value(row.get("Data de Batismo", ""))
        if baptism_invalid:
            pending_counts["data_batismo_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data de batismo invalida.", "Revisar data.")
        if baptism_date:
            add_history(
                conn,
                org_id,
                pessoa_id,
                lote_id,
                "batismo",
                baptism_date,
                "Batismo",
                f"Tipo: {clean(row.get('Tipo de batismo', '')) or 'nao informado'}",
                origem=clean(row.get("Batizado por", "")),
            )

        wedding_date, wedding_invalid = parse_date_value(row.get("Data de Casamento", ""))
        if wedding_invalid:
            pending_counts["data_casamento_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "info", "Data de casamento invalida.", "Revisar data.")

        if inativacao_invalid:
            pending_counts["data_inatividade_invalida"] += 1
            add_pending(conn, lote_id, linha_id, "data_invalida", "aviso", "Data de inatividade invalida.", "Revisar data.")
        if inativacao or motivo_inatividade:
            pending_counts["membro_inativo_sem_voto"] += 1
            add_pending(
                conn,
                lote_id,
                linha_id,
                "membro_inativo_sem_voto",
                "info",
                "Pessoa mantida como membro, mas marcada como membro_inativo sem privilegio de voto.",
                "Conferir apenas se a regra de inatividade mudar.",
            )
            add_history(
                conn,
                org_id,
                pessoa_id,
                lote_id,
                "inatividade",
                inativacao,
                "Membro inativo",
                motivo_inatividade or "Inatividade importada da planilha.",
            )

        pending_for_line = int(scalar(conn, "SELECT COUNT(*) FROM import_pendencias WHERE linha_id = ?", (linha_id,)) or 0)
        if pending_for_line:
            conn.execute("UPDATE import_linhas SET status = 'importado_com_pendencia' WHERE id = ?", (linha_id,))
        stats["importados"] += 1

    total_pendencias = int(scalar(conn, "SELECT COUNT(*) FROM import_pendencias WHERE lote_id = ?", (lote_id,)) or 0)
    conn.execute(
        """
        UPDATE import_lotes
        SET linhas_importadas = ?, linhas_ignoradas = 0, linhas_com_erro = ?
        WHERE id = ?
        """,
        (stats["importados"], total_pendencias, lote_id),
    )
    conn.commit()

    summary = {
        "source": str(source),
        "db_path": str(db_path),
        "report_path": str(report_path),
        "sheet_name": sheet_name,
        "records": len(records),
        "lote_id": lote_id,
        "pessoas": int(scalar(conn, "SELECT COUNT(*) FROM pessoas")),
        "perfis": int(scalar(conn, "SELECT COUNT(*) FROM pessoa_perfis")),
        "contatos": int(scalar(conn, "SELECT COUNT(*) FROM pessoa_contatos")),
        "enderecos": int(scalar(conn, "SELECT COUNT(*) FROM pessoa_enderecos")),
        "historico": int(scalar(conn, "SELECT COUNT(*) FROM pessoa_historico")),
        "campos_personalizados": int(scalar(conn, "SELECT COUNT(*) FROM campos_personalizados")),
        "valores_personalizados": int(scalar(conn, "SELECT COUNT(*) FROM valores_campos_personalizados")),
        "pendencias": total_pendencias,
        "status_counts": dict(status_counts),
        "profile_counts": dict(profile_counts),
        "custom_counts": dict(custom_counts),
        "pending_counts": dict(pending_counts),
        "normalization_counts": dict(stats),
    }
    write_report(conn, summary, report_path)
    conn.close()
    return summary


def md_table(counter: dict[str, int]) -> str:
    if not counter:
        return "Nenhum registro.\n"
    lines = ["| Item | Quantidade |", "|---|---:|"]
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines) + "\n"


def write_report(conn: sqlite3.Connection, summary: dict[str, object], report_path: Path) -> None:
    table_counts = {
        "pessoas": summary["pessoas"],
        "pessoa_perfis": summary["perfis"],
        "pessoa_contatos": summary["contatos"],
        "pessoa_enderecos": summary["enderecos"],
        "pessoa_historico": summary["historico"],
        "campos_personalizados": summary["campos_personalizados"],
        "valores_campos_personalizados": summary["valores_personalizados"],
        "import_pendencias": summary["pendencias"],
    }
    contact_counts = dict(conn.execute("SELECT tipo, COUNT(*) FROM pessoa_contatos GROUP BY tipo").fetchall())
    history_counts = dict(conn.execute("SELECT tipo_evento, COUNT(*) FROM pessoa_historico GROUP BY tipo_evento").fetchall())
    pending_by_severity = dict(conn.execute("SELECT severidade, COUNT(*) FROM import_pendencias GROUP BY severidade").fetchall())
    lines = [
        "# Relatorio De Importacao De Membros",
        "",
        "## 1. Resultado Geral",
        "",
        f"- Arquivo importado: `{Path(str(summary['source'])).name}`",
        f"- Aba: `{summary['sheet_name']}`",
        f"- Banco gerado: `{summary['db_path']}`",
        f"- Lote de importacao: `{summary['lote_id']}`",
        f"- Registros lidos: `{summary['records']}`",
        "",
        "## 2. Registros Gerados",
        "",
        md_table(table_counts),
        "## 3. Status Operacional",
        "",
        md_table(summary["status_counts"]),
        "## 4. Perfis Criados",
        "",
        md_table(summary["profile_counts"]),
        "## 5. Contatos",
        "",
        md_table(contact_counts),
        "## 6. Historico",
        "",
        md_table(history_counts),
        "## 7. Campos Personalizados Mais Preenchidos",
        "",
        md_table(summary["custom_counts"]),
        "## 8. Pendencias E Revisoes",
        "",
        "Pendencias aqui nao significam falha de importacao. Elas indicam itens que devem ser revisados ou conferidos depois.",
        "",
        "### Por Severidade",
        "",
        md_table(pending_by_severity),
        "### Por Tipo",
        "",
        md_table(summary["pending_counts"]),
        "## 9. Normalizacoes Aplicadas",
        "",
        md_table(summary["normalization_counts"]),
        "## 10. Decisoes Aplicadas",
        "",
        "- CPF valido foi gravado em `pessoas.cpf`.",
        "- CPF invalido ou duplicado nao foi gravado como CPF principal; foi preservado em campo personalizado para revisao.",
        "- CPF vazio foi aceito.",
        "- Pessoa com Data/Motivo de inatividade recebeu status `membro_inativo`, mantendo perfil `membro`.",
        "- `E recem-convertido?` foi importado como campo acessorio.",
        "- `Tipo de batismo` foi importado como campo acessorio.",
        "- `Estado Civil = Escolha unica` foi tratado como vazio.",
        "- Datas `1/1/1` foram tratadas como invalidas e viraram pendencia.",
        "- Numeros de endereco terminados em `.0` foram normalizados.",
        "",
        "## 11. Observacao De Privacidade",
        "",
        "Este relatorio nao lista nomes, CPFs completos, telefones ou enderecos.",
        "",
    ]
    report_path.write_text("\n".join(lines))


def write_incremental_report(conn: sqlite3.Connection, summary: dict[str, object], report_path: Path) -> None:
    lote_id = int(summary["lote_id"])
    line_status = dict(
        conn.execute(
            "SELECT status, COUNT(*) FROM import_linhas WHERE lote_id = ? GROUP BY status",
            (lote_id,),
        ).fetchall()
    )
    pending_by_severity = dict(
        conn.execute(
            "SELECT severidade, COUNT(*) FROM import_pendencias WHERE lote_id = ? GROUP BY severidade",
            (lote_id,),
        ).fetchall()
    )
    lines = [
        "# Relatorio De Importacao Incremental De Pessoas",
        "",
        "## 1. Resultado Geral",
        "",
        f"- Arquivo importado: `{Path(str(summary['source'])).name}`",
        f"- Aba: `{summary['sheet_name']}`",
        f"- Banco atualizado: `{summary['db_path']}`",
        f"- Backup antes da importacao: `{summary['backup_path']}`",
        f"- Lote de importacao: `{summary['lote_id']}`",
        f"- Registros lidos: `{summary['records']}`",
        f"- Pessoas ativas no banco apos importacao: `{summary['pessoas_total']}`",
        "",
        "## 2. Politica Aplicada",
        "",
        "- Importacao complementar/incremental.",
        "- Nenhuma ficha existente foi apagada.",
        "- Fichas existentes foram reconhecidas por CPF valido, numero de membro ou nome completo + data de nascimento.",
        "- Em ficha existente, o importador preencheu apenas campos vazios e adicionou contatos/historicos/campos complementares faltantes.",
        "- Mudanca de status, conflito de chaves ou match fraco foram enviados para pendencia de auditoria.",
        "- CPF invalido/duplicado nao foi usado como chave automatica.",
        "",
        "## 3. Linhas Por Status",
        "",
        md_table(line_status),
        "## 4. Acoes Executadas",
        "",
        md_table(summary["stats"]),
        "## 5. Pendencias",
        "",
        "Pendencias aqui nao significam falha de importacao. Elas indicam itens que precisam de revisao antes de uma alteracao sensivel.",
        "",
        "### Por Severidade",
        "",
        md_table(pending_by_severity),
        "### Por Tipo",
        "",
        md_table(summary["pending_counts"]),
        "## 6. Observacao De Privacidade",
        "",
        "Este relatorio nao lista nomes, CPFs completos, telefones ou enderecos.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa planilha de membros para o banco Power Church V0.")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--mode",
        choices=["auto", "new", "incremental"],
        default="auto",
        help="auto: cria banco novo se nao existir, ou importa complemento se existir.",
    )
    parser.add_argument("--allow-duplicate-file", action="store_true", help="Permite importar arquivo com hash ja registrado.")
    args = parser.parse_args()
    if args.mode == "new":
        summary = import_members(args.xlsx, args.db, args.report)
    elif args.mode == "incremental":
        summary = import_members_incremental(
            args.xlsx,
            args.db,
            args.report,
            allow_duplicate_file=args.allow_duplicate_file,
        )
    elif args.db.exists():
        summary = import_members_incremental(
            args.xlsx,
            args.db,
            args.report,
            allow_duplicate_file=args.allow_duplicate_file,
        )
    else:
        summary = import_members(args.xlsx, args.db, args.report)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
