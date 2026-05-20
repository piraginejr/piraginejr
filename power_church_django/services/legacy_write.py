from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shlex
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from django.conf import settings

from power_church_core.family import family_address_key
from power_church_core.formatting import competencia_from_date, parse_money
from power_church_core.normalization import clean_cpf, moneyless_int, normalize_match_name, normalize_query, valid_cpf
from power_church_django.services.legacy import legacy_db_path, table_exists
from power_church_django.services.photos import list_member_photo_variants


ALLOWED_PERSON_STATUSES = {
    "membro_ativo",
    "membro_inativo",
    "frequentador",
    "visitante",
    "arquivo_morto",
}

PERSON_STATUS_OPTIONS = [
    ("membro_ativo", "Membro ativo"),
    ("membro_inativo", "Membro inativo"),
    ("frequentador", "Frequentador"),
    ("visitante", "Visitante"),
    ("arquivo_morto", "Arquivo morto"),
]

PERSON_SEX_OPTIONS = [
    ("", "Nao informado"),
    ("masculino", "Masculino"),
    ("feminino", "Feminino"),
]

PERSON_MARITAL_STATUS_OPTIONS = [
    ("", "Nao informado"),
    ("solteiro", "Solteiro(a)"),
    ("casado", "Casado(a)"),
    ("uniao_estavel", "Uniao estavel"),
    ("divorciado", "Divorciado(a)"),
    ("viuvo", "Viuvo(a)"),
    ("noivo", "Noivo(a)"),
]

CONTRIBUTION_STATUS_OPTIONS = {
    "regular",
    "sem_associacao",
    "em_saneamento",
    "revisar_destinacao",
}

ALLOWED_FAMILY_RELATIONSHIP_TYPES = {
    "nucleo_familiar",
    "familia_estendida",
    "conjuge",
    "filho",
    "pai_mae",
    "irmao",
    "neto",
    "genro_nora",
    "outro_familiar",
}

AUTO_ADDRESS_RELATIONSHIP_MARKER = "AUTOMATICAMENTE POR ENDERECO"
MANUAL_FAMILY_SUPPRESSION_MARKER = "IGNORADO MANUALMENTE"
STALE_ADDRESS_RELATIONSHIP_MARKER = "DESATIVADO AUTOMATICAMENTE POR ENDERECO DIVERGENTE"
ENVELOPE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".webp", ".tif", ".tiff"}
ENVELOPE_PENDING_STATUS = "aguardando_digitacao"
ENVELOPE_LAUNCHED_STATUS = "lancado"
ENVELOPE_IGNORED_STATUS = "ignorado"
ENVELOPE_DUPLICATE_STATUS = "duplicado"

EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class LegacyWriteError(RuntimeError):
    """Raised when a controlled write to the legacy database fails."""


def connect_legacy_write() -> sqlite3.Connection:
    path = legacy_db_path()
    if not path.exists():
        raise LegacyWriteError(f"Banco legado nao encontrado: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def envelope_upload_root() -> Path:
    configured = os.environ.get("POWER_CHURCH_ENVELOPE_DIR")
    if configured:
        return Path(configured)
    return Path(settings.POWER_CHURCH_LEGACY_DB_PATH).resolve().parent / "envelope_uploads"


def clean_member_code(value: object) -> str:
    compact = str(value or "").strip().replace(" ", "")
    upper = compact.upper()
    for prefix in ("MEM-", "MBR-", "NM-"):
        if upper.startswith(prefix):
            compact = compact[len(prefix) :]
            break
    compact = compact.replace("-", "")
    digits = "".join(ch for ch in compact if ch.isdigit())
    return digits or normalize_query(compact)


def status_grants_member_code(value: object) -> bool:
    return normalize_query(value) in {"membro_ativo", "membro_inativo"}


def _normalize_choice(value: object, allowed: set[str]) -> str:
    normalized = normalize_query(value).lower()
    return normalized if normalized in allowed else ""


def _manual_cpf_or_error(value: object) -> str | None:
    text = normalize_query(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not valid_cpf(digits):
        raise LegacyWriteError("CPF invalido. Corrija o numero antes de salvar a ficha.")
    return digits


def _manual_email_or_error(value: object) -> str:
    email = normalize_query(value).lower()
    if not email:
        return ""
    if len(email) > 254 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        raise LegacyWriteError("E-mail invalido. Corrija o endereco antes de salvar a ficha.")
    local, domain = email.rsplit("@", 1)
    labels = domain.split(".")
    if (
        not local
        or not domain
        or not EMAIL_LOCAL_RE.match(local)
        or len(labels) < 2
        or any(not label or not EMAIL_DOMAIN_LABEL_RE.match(label) for label in labels)
        or len(labels[-1]) < 2
    ):
        raise LegacyWriteError("E-mail invalido. Corrija o endereco antes de salvar a ficha.")
    return email


def validate_person_cpf_for_form(value: object, ignore_person_id: int = 0) -> dict[str, object]:
    try:
        cpf_value = _manual_cpf_or_error(value)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": ""}
    if not cpf_value:
        return {"ok": True, "message": "", "normalized": ""}
    with connect_legacy_write() as conn:
        organization_id = default_organization_id(conn)
        try:
            _assert_unique_cpf(conn, organization_id, cpf_value, ignore_person_id=ignore_person_id)
        except LegacyWriteError as exc:
            return {"ok": False, "message": str(exc), "normalized": cpf_value}
    return {"ok": True, "message": "CPF valido e ainda nao usado por outra ficha ativa.", "normalized": cpf_value}


def validate_person_email_for_form(value: object) -> dict[str, object]:
    try:
        email_value = _manual_email_or_error(value)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": ""}
    if not email_value:
        return {"ok": True, "message": "", "normalized": ""}
    return {"ok": True, "message": "E-mail com formato valido.", "normalized": email_value}


def _form_value(data: Any, key: str, default: str = "") -> str:
    getter = getattr(data, "get", None)
    value = getter(key, default) if getter else default
    return normalize_query(value)


def person_form_payload(data: Any) -> dict[str, str]:
    payload = {
        "codigo_interno": clean_member_code(_form_value(data, "codigo_interno")),
        "nome": _form_value(data, "nome"),
        "nome_social": _form_value(data, "nome_social"),
        "cpf": clean_cpf(_form_value(data, "cpf")),
        "rg": _form_value(data, "rg"),
        "data_nascimento": _form_value(data, "data_nascimento"),
        "sexo": _normalize_choice(_form_value(data, "sexo"), {value for value, _label in PERSON_SEX_OPTIONS if value}),
        "estado_civil": _normalize_choice(
            _form_value(data, "estado_civil"),
            {value for value, _label in PERSON_MARITAL_STATUS_OPTIONS if value},
        ),
        "email_principal": _form_value(data, "email_principal"),
        "telefone_principal": _form_value(data, "telefone_principal"),
        "whatsapp_principal": _form_value(data, "whatsapp_principal"),
        "status": _form_value(data, "status", "frequentador"),
        "observacoes": _form_value(data, "observacoes"),
        "cep": _form_value(data, "cep"),
        "logradouro": _form_value(data, "logradouro"),
        "numero": _form_value(data, "numero"),
        "complemento": _form_value(data, "complemento"),
        "bairro": _form_value(data, "bairro"),
        "cidade": _form_value(data, "cidade"),
        "uf": _form_value(data, "uf").upper(),
        "allow_member_code_edit": "1" if _form_value(data, "allow_member_code_edit") == "1" else "",
    }
    if payload["status"] not in ALLOWED_PERSON_STATUSES:
        payload["status"] = "frequentador"
    return payload


def empty_person_form() -> dict[str, str]:
    payload = {key: "" for key in person_form_payload({}).keys()}
    payload["status"] = "frequentador"
    return payload


def default_organization_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM organizacoes ORDER BY id LIMIT 1").fetchone()
    return moneyless_int(row["id"] if row else 1)


def get_person(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pessoas WHERE id = ?", (person_id,)).fetchone()


def get_contribution(conn: sqlite3.Connection, contribution_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contribuicoes WHERE id = ?", (contribution_id,)).fetchone()


def get_receipt(conn: sqlite3.Connection, receipt_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM recibos WHERE id = ?", (receipt_id,)).fetchone()


def get_contributor(conn: sqlite3.Connection, contributor_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contribuintes WHERE id = ?", (contributor_id,)).fetchone()


def _ensure_table_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_envelope_support(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS envelope_lotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organizacao_id INTEGER NOT NULL,
            competencia TEXT NOT NULL,
            competencia_ordem INTEGER NOT NULL,
            nome TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'aberto',
            total_envelopes INTEGER NOT NULL DEFAULT 0,
            total_valor REAL NOT NULL DEFAULT 0,
            caminho_pasta TEXT,
            observacoes TEXT,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT,
            FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_envelope_lotes_competencia_nome
        ON envelope_lotes(organizacao_id, competencia_ordem, nome)
        """
    )
    for column, definition in [
        ("data_padrao_recebimento", "TEXT"),
        ("origem_operacional_padrao", "TEXT"),
        ("tipo_contribuicao_id_padrao", "INTEGER"),
        ("campanha_id_padrao", "INTEGER"),
        ("forma_recebimento_id_padrao", "INTEGER"),
    ]:
        _ensure_table_column(conn, "envelope_lotes", column, definition)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS envelopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id INTEGER NOT NULL,
            organizacao_id INTEGER NOT NULL,
            competencia TEXT NOT NULL,
            competencia_ordem INTEGER NOT NULL,
            data_recebimento TEXT NOT NULL,
            total_informado REAL NOT NULL,
            total_linhas REAL NOT NULL DEFAULT 0,
            nome_informado TEXT,
            telefone_informado TEXT,
            endereco_informado TEXT,
            pessoa_id INTEGER,
            contribuinte_id INTEGER,
            forma_recebimento_id INTEGER,
            origem_operacional TEXT,
            caminho_imagem TEXT,
            nome_arquivo_original TEXT,
            imagem_hash TEXT,
            imagem_content_type TEXT,
            imagem_tamanho INTEGER,
            status TEXT NOT NULL DEFAULT 'lancado',
            observacoes TEXT,
            justificativa TEXT,
            ocr_json TEXT,
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT,
            FOREIGN KEY (lote_id) REFERENCES envelope_lotes(id),
            FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
            FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
            FOREIGN KEY (contribuinte_id) REFERENCES contribuintes(id),
            FOREIGN KEY (forma_recebimento_id) REFERENCES formas_recebimento(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelopes_lote ON envelopes(lote_id, ativo, data_recebimento)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelopes_competencia ON envelopes(organizacao_id, competencia_ordem, ativo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelopes_hash ON envelopes(organizacao_id, imagem_hash)")
    _ensure_table_column(conn, "envelopes", "ordem_lote", "INTEGER NOT NULL DEFAULT 0")
    for column, definition in [
        ("rastreio_forma_identificada", "TEXT"),
        ("rastreio_banco_operadora", "TEXT"),
        ("rastreio_numero_cheque", "TEXT"),
        ("rastreio_numero_operacao", "TEXT"),
        ("rastreio_nsu_tid", "TEXT"),
        ("rastreio_ultimos_digitos_cartao", "TEXT"),
        ("rastreio_data_operacao", "TEXT"),
        ("rastreio_valor_operacao", "REAL"),
        ("rastreio_status_conciliacao", "TEXT NOT NULL DEFAULT 'pendente'"),
        ("rastreio_observacoes", "TEXT"),
    ]:
        _ensure_table_column(conn, "envelopes", column, definition)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelopes_lote_status ON envelopes(lote_id, status, ativo, ordem_lote, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS envelope_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            envelope_id INTEGER NOT NULL,
            organizacao_id INTEGER NOT NULL,
            pessoa_id INTEGER,
            contribuinte_id INTEGER,
            tipo_contribuicao_id INTEGER NOT NULL,
            campanha_id INTEGER,
            valor REAL NOT NULL,
            observacoes TEXT,
            contribuicao_id INTEGER,
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT,
            FOREIGN KEY (envelope_id) REFERENCES envelopes(id),
            FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
            FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
            FOREIGN KEY (contribuinte_id) REFERENCES contribuintes(id),
            FOREIGN KEY (tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
            FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
            FOREIGN KEY (contribuicao_id) REFERENCES contribuicoes(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelope_itens_envelope ON envelope_itens(envelope_id, ativo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_envelope_itens_contribuicao ON envelope_itens(contribuicao_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS envelope_atualizacoes_cadastrais (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            envelope_id INTEGER NOT NULL,
            organizacao_id INTEGER NOT NULL,
            pessoa_id INTEGER NOT NULL,
            campo TEXT NOT NULL,
            valor_cadastro TEXT,
            valor_envelope TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pendente',
            observacoes TEXT,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT,
            FOREIGN KEY (envelope_id) REFERENCES envelopes(id),
            FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
            FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_envelope_atualizacoes_pendentes
        ON envelope_atualizacoes_cadastrais(envelope_id, pessoa_id, campo, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_envelope_atualizacoes_pessoa
        ON envelope_atualizacoes_cadastrais(pessoa_id, status, campo)
        """
    )


def primary_address(conn: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM pessoa_enderecos WHERE pessoa_id = ? ORDER BY principal DESC, id LIMIT 1",
        (person_id,),
    ).fetchone()


def next_member_code(conn: sqlite3.Connection, organization_id: int) -> str:
    rows = conn.execute(
        """
        SELECT codigo_interno
          FROM pessoas
         WHERE organizacao_id = ?
           AND codigo_interno IS NOT NULL
           AND TRIM(codigo_interno) <> ''
        """,
        (organization_id,),
    ).fetchall()
    numeric_codes = [
        clean_member_code(row["codigo_interno"])
        for row in rows
        if clean_member_code(row["codigo_interno"]).isdigit()
    ]
    if not numeric_codes:
        return "00001"
    width = max(5, max(len(code) for code in numeric_codes))
    next_number = max(int(code) for code in numeric_codes) + 1
    return str(next_number).zfill(max(width, len(str(next_number))))


def member_code_exists(
    conn: sqlite3.Connection,
    organization_id: int,
    code: str,
    ignore_person_id: int = 0,
) -> bool:
    normalized = clean_member_code(code)
    if not normalized:
        return False
    clauses = ["organizacao_id = ?", "codigo_interno = ?"]
    params: list[object] = [organization_id, normalized]
    if ignore_person_id:
        clauses.append("id <> ?")
        params.append(ignore_person_id)
    row = conn.execute(f"SELECT 1 FROM pessoas WHERE {' AND '.join(clauses)} LIMIT 1", params).fetchone()
    return row is not None


def resolved_member_code(
    conn: sqlite3.Connection,
    organization_id: int,
    requested_code: str = "",
    ignore_person_id: int = 0,
) -> str:
    normalized = clean_member_code(requested_code)
    if normalized and not member_code_exists(conn, organization_id, normalized, ignore_person_id=ignore_person_id):
        return normalized
    candidate = next_member_code(conn, organization_id)
    while member_code_exists(conn, organization_id, candidate, ignore_person_id=ignore_person_id):
        candidate = str(int(candidate) + 1).zfill(len(candidate))
    return candidate


def _assert_unique_cpf(
    conn: sqlite3.Connection,
    organization_id: int,
    cpf_value: str,
    ignore_person_id: int = 0,
) -> None:
    if not cpf_value:
        return
    clauses = ["organizacao_id = ?", "cpf = ?", "ativo = 1"]
    params: list[object] = [organization_id, cpf_value]
    if ignore_person_id:
        clauses.append("id <> ?")
        params.append(ignore_person_id)
    row = conn.execute(
        f"SELECT id, nome FROM pessoas WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1",
        params,
    ).fetchone()
    if row is not None:
        raise LegacyWriteError(f"CPF ja cadastrado em outra ficha: {row['nome']} (ID {row['id']}).")


def person_snapshot(conn: sqlite3.Connection, person_id: int) -> dict[str, object]:
    person = get_person(conn, person_id)
    if person is None:
        return {}
    address = primary_address(conn, person_id)
    contacts = conn.execute(
        """
        SELECT tipo, valor, principal, observacoes
          FROM pessoa_contatos
         WHERE pessoa_id = ?
         ORDER BY principal DESC, tipo, id
        """,
        (person_id,),
    ).fetchall()
    return {
        "pessoa": dict(person),
        "endereco_principal": dict(address) if address else None,
        "contatos": [dict(row) for row in contacts],
    }


def write_audit_log(
    conn: sqlite3.Connection,
    organization_id: int | None,
    action: str,
    table: str,
    record_id: int | None,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    actor: str = "",
) -> None:
    after_payload = dict(after or {}) if after is not None else None
    if after_payload is not None and actor:
        after_payload["_operador_django"] = actor
    conn.execute(
        """
        INSERT INTO auditoria (
            organizacao_id, usuario_id, acao, tabela, registro_id, dados_antes_json, dados_depois_json
        ) VALUES (?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            action,
            table,
            record_id,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after_payload, ensure_ascii=False, default=str) if after_payload is not None else None,
        ),
    )
    try:
        from power_church_django.services.django_audit import mirror_legacy_audit_event

        mirror_legacy_audit_event(
            organization_id=organization_id,
            actor=actor,
            action=action,
            table_name=table,
            record_id=record_id,
            before=before,
            after=after_payload,
        )
    except Exception:
        pass


def ensure_secure_people_trash(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pessoas_lixeira_segura (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organizacao_id INTEGER,
            pessoa_id INTEGER NOT NULL,
            nome TEXT,
            cpf TEXT,
            motivo TEXT,
            operador TEXT,
            snapshot_json TEXT NOT NULL,
            restaurado INTEGER NOT NULL DEFAULT 0,
            restaurado_em TEXT,
            restaurado_por TEXT,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def ensure_secure_people_purge(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pessoas_purga_segura (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organizacao_id INTEGER,
            pessoa_id_original INTEGER NOT NULL,
            lixeira_id INTEGER,
            nome_hash TEXT,
            cpf_hash TEXT,
            motivo TEXT,
            operador TEXT,
            tombstone_json TEXT NOT NULL,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _count(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _linked_contributor_ids(conn: sqlite3.Connection, person_id: int) -> list[int]:
    return [
        moneyless_int(row["id"])
        for row in conn.execute(
            "SELECT id FROM contribuintes WHERE pessoa_id = ?",
            (person_id,),
        ).fetchall()
    ]


def _purge_blockers(conn: sqlite3.Connection, person_id: int) -> dict[str, int]:
    contributor_ids = _linked_contributor_ids(conn, person_id)
    placeholders = ",".join("?" for _ in contributor_ids)
    contribution_by_contributor = 0
    if contributor_ids:
        contribution_by_contributor = _count(
            conn,
            f"SELECT COUNT(*) FROM contribuicoes WHERE contribuinte_id IN ({placeholders})",
            tuple(contributor_ids),
        )
    return {
        "contribuicoes_pessoa": _count(conn, "SELECT COUNT(*) FROM contribuicoes WHERE pessoa_id = ?", (person_id,)),
        "contribuicoes_contribuinte": contribution_by_contributor,
        "recibos": _count(conn, "SELECT COUNT(*) FROM recibos WHERE pessoa_id = ?", (person_id,)),
        "lancamentos_financeiros": _count(
            conn,
            "SELECT COUNT(*) FROM lancamentos_financeiros WHERE entidade_pessoa_id = ?",
            (person_id,),
        ),
    }


def _blocker_message(blockers: dict[str, int]) -> str:
    labels = {
        "contribuicoes_pessoa": "contribuicao(oes) ligada(s) diretamente a pessoa",
        "contribuicoes_contribuinte": "contribuicao(oes) ligada(s) a identidade financeira da pessoa",
        "recibos": "recibo(s)",
        "lancamentos_financeiros": "lancamento(s) financeiro(s)",
    }
    active = [f"{total} {labels[key]}" for key, total in blockers.items() if total]
    return "; ".join(active)


def purge_secure_person_trash(trash_id: int, reason: str, actor: str = "") -> int:
    trash_id = moneyless_int(trash_id)
    reason = normalize_query(reason)
    if not trash_id:
        raise LegacyWriteError("Registro de lixeira invalido.")
    if len(reason) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para a purga final.")
    with connect_legacy_write() as conn:
        ensure_secure_people_trash(conn)
        ensure_secure_people_purge(conn)
        trash = conn.execute(
            "SELECT * FROM pessoas_lixeira_segura WHERE id = ?",
            (trash_id,),
        ).fetchone()
        if trash is None:
            raise LegacyWriteError("Registro da lixeira nao encontrado.")
        if moneyless_int(trash["restaurado"]):
            raise LegacyWriteError("Ficha restaurada nao pode ser purgada pela lixeira.")
        person_id = moneyless_int(trash["pessoa_id"])
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("A ficha original nao foi encontrada. Revise a auditoria antes de purgar.")
        if moneyless_int(person["ativo"]):
            raise LegacyWriteError("A ficha ainda esta ativa. Envie para a lixeira segura antes da purga final.")
        blockers = _purge_blockers(conn, person_id)
        blocker_detail = _blocker_message(blockers)
        if blocker_detail:
            raise LegacyWriteError(f"Purga bloqueada: existe {blocker_detail}.")

        contributor_ids = _linked_contributor_ids(conn, person_id)
        contributor_placeholders = ",".join("?" for _ in contributor_ids)
        nome_hash = hashlib.sha256(normalize_query(trash["nome"] or person["nome"]).encode("utf-8")).hexdigest()
        cpf_hash = hashlib.sha256(str(trash["cpf"] or person["cpf"] or "").encode("utf-8")).hexdigest() if (trash["cpf"] or person["cpf"]) else ""
        before = {
            "lixeira_id": trash_id,
            "pessoa_id": person_id,
            "nome_hash": nome_hash,
            "cpf_hash": cpf_hash,
            "cpf_presente": bool(trash["cpf"] or person["cpf"]),
            "bloqueios_financeiros": blockers,
        }
        tombstone = {
            "pessoa_id_original": person_id,
            "lixeira_id": trash_id,
            "nome_hash": nome_hash,
            "cpf_hash": cpf_hash,
            "motivo_purga": reason,
            "operador": actor,
            "fotos_removidas": 0,
            "contribuintes_removidos": len(contributor_ids),
            "bloqueios_financeiros": blockers,
        }
        try:
            with conn:
                photo_count = 0
                for photo_path in list_member_photo_variants(person_id):
                    try:
                        photo_path.unlink(missing_ok=True)
                        photo_count += 1
                    except OSError:
                        pass
                tombstone["fotos_removidas"] = photo_count

                conn.execute("UPDATE pix_movimentos SET suggested_person_id = NULL WHERE suggested_person_id = ?", (person_id,))
                conn.execute("UPDATE pix_movimentos SET resolved_person_id = NULL WHERE resolved_person_id = ?", (person_id,))
                conn.execute("UPDATE extrato_movimentos SET suggested_person_id = NULL WHERE suggested_person_id = ?", (person_id,))
                conn.execute("UPDATE extrato_movimentos SET resolved_person_id = NULL WHERE resolved_person_id = ?", (person_id,))
                if contributor_ids:
                    conn.execute(
                        f"UPDATE pix_movimentos SET suggested_contribuinte_id = NULL WHERE suggested_contribuinte_id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )
                    conn.execute(
                        f"UPDATE pix_movimentos SET resolved_contribuinte_id = NULL WHERE resolved_contribuinte_id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )
                    conn.execute(
                        f"UPDATE extrato_movimentos SET suggested_contribuinte_id = NULL WHERE suggested_contribuinte_id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )
                    conn.execute(
                        f"UPDATE extrato_movimentos SET resolved_contribuinte_id = NULL WHERE resolved_contribuinte_id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )
                    conn.execute(
                        f"DELETE FROM contribuintes_identificadores WHERE contribuinte_id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )
                    conn.execute(
                        f"DELETE FROM contribuintes WHERE id IN ({contributor_placeholders})",
                        tuple(contributor_ids),
                    )

                conn.execute("DELETE FROM contribuintes_identificadores WHERE pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM valores_campos_personalizados WHERE registro_tipo = 'pessoa' AND registro_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoa_relacionamentos WHERE pessoa_id = ? OR pessoa_relacionada_id = ?", (person_id, person_id))
                conn.execute("UPDATE pessoa_historico SET responsavel_pessoa_id = NULL WHERE responsavel_pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoa_historico WHERE pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoa_perfis WHERE pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoa_enderecos WHERE pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoa_contatos WHERE pessoa_id = ?", (person_id,))
                conn.execute("DELETE FROM pessoas WHERE id = ?", (person_id,))
                conn.execute("DELETE FROM pessoas_lixeira_segura WHERE id = ?", (trash_id,))
                conn.execute(
                    """
                    INSERT INTO pessoas_purga_segura (
                        organizacao_id, pessoa_id_original, lixeira_id, nome_hash, cpf_hash,
                        motivo, operador, tombstone_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        moneyless_int(person["organizacao_id"]),
                        person_id,
                        trash_id,
                        nome_hash,
                        cpf_hash,
                        reason,
                        actor,
                        json.dumps(tombstone, ensure_ascii=False, default=str),
                    ),
                )
                conn.execute(
                    """
                    UPDATE auditoria
                       SET dados_antes_json = NULL,
                           dados_depois_json = ?
                     WHERE tabela = 'pessoas'
                       AND registro_id = ?
                       AND acao = 'excluir_ficha_lixeira_segura_django'
                    """,
                    (json.dumps({"purgado": True, **tombstone}, ensure_ascii=False, default=str), person_id),
                )
                write_audit_log(
                    conn,
                    moneyless_int(person["organizacao_id"]),
                    "purgar_ficha_lixeira_segura_django",
                    "pessoas_purga_segura",
                    person_id,
                    before,
                    {"purgado": True, **tombstone},
                    actor=actor,
                )
                return person_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel purgar a ficha com seguranca: {exc}") from exc


def soft_delete_person(person_id: int, reason: str, actor: str = "") -> int:
    person_id = moneyless_int(person_id)
    reason = normalize_query(reason)
    if not person_id:
        raise LegacyWriteError("Pessoa invalida para exclusao.")
    if len(reason) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para excluir a ficha.")
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("Pessoa nao encontrada.")
        if not int(person["ativo"] or 0):
            raise LegacyWriteError("Esta ficha ja esta fora do cadastro operacional.")
        organization_id = moneyless_int(person["organizacao_id"])
        before = person_snapshot(conn, person_id)
        try:
            with conn:
                ensure_secure_people_trash(conn)
                cursor = conn.execute(
                    """
                    INSERT INTO pessoas_lixeira_segura (
                        organizacao_id, pessoa_id, nome, cpf, motivo, operador, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        organization_id,
                        person_id,
                        person["nome"] or "",
                        person["cpf"] or "",
                        reason,
                        actor,
                        json.dumps(before, ensure_ascii=False, default=str),
                    ),
                )
                trash_id = moneyless_int(cursor.lastrowid)
                conn.execute(
                    "UPDATE pessoas SET ativo = 0, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                    (person_id,),
                )
                after = person_snapshot(conn, person_id)
                after["lixeira_segura_id"] = trash_id
                after["motivo_exclusao"] = reason
                write_audit_log(
                    conn,
                    organization_id,
                    "excluir_ficha_lixeira_segura_django",
                    "pessoas",
                    person_id,
                    before,
                    after,
                    actor=actor,
                )
                return trash_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel excluir a ficha: {exc}") from exc


def create_person_relationship(person_id: int, payload: Any, actor: str = "") -> int:
    person_id = moneyless_int(person_id)
    related_person_id = moneyless_int(_form_value(payload, "related_person_id"))
    relationship_type = normalize_query(_form_value(payload, "tipo_relacionamento", "nucleo_familiar")) or "nucleo_familiar"
    notes = normalize_query(_form_value(payload, "observacoes"))
    if not person_id or not related_person_id:
        raise LegacyWriteError("Escolha a pessoa principal e a pessoa relacionada.")
    if person_id == related_person_id:
        raise LegacyWriteError("A pessoa relacionada nao pode ser a propria ficha.")
    if relationship_type not in ALLOWED_FAMILY_RELATIONSHIP_TYPES:
        raise LegacyWriteError("Tipo de relacao familiar invalido.")

    with connect_legacy_write() as conn:
        if not table_exists(conn, "pessoa_relacionamentos"):
            raise LegacyWriteError("A tabela de vinculos familiares ainda nao existe no banco.")
        person = get_person(conn, person_id)
        related_person = get_person(conn, related_person_id)
        if person is None or related_person is None:
            raise LegacyWriteError("Pessoa principal ou pessoa relacionada nao encontrada.")
        organization_id = moneyless_int(person["organizacao_id"])
        if organization_id != moneyless_int(related_person["organizacao_id"]):
            raise LegacyWriteError("As duas pessoas precisam pertencer a mesma organizacao.")
        duplicate = conn.execute(
            """
            SELECT id
              FROM pessoa_relacionamentos
             WHERE organizacao_id = ?
               AND ativo = 1
               AND (
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
                    OR
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
               )
             ORDER BY id
             LIMIT 1
            """,
            (
                organization_id,
                person_id,
                related_person_id,
                related_person_id,
                person_id,
            ),
        ).fetchone()
        if duplicate is not None:
            return moneyless_int(duplicate["id"])
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pessoa_relacionamentos (
                        organizacao_id, pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (organization_id, person_id, related_person_id, relationship_type, notes or None),
                )
                relationship_id = moneyless_int(cursor.lastrowid)
                after = conn.execute(
                    "SELECT * FROM pessoa_relacionamentos WHERE id = ?",
                    (relationship_id,),
                ).fetchone()
                write_audit_log(
                    conn,
                    organization_id,
                    "criar_vinculo_familiar_django",
                    "pessoa_relacionamentos",
                    relationship_id,
                    None,
                    dict(after) if after else {},
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel criar a relacao familiar: {exc}") from exc
    return relationship_id


def _create_relationship_in_connection(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    related_person_id: int,
    relationship_type: str,
    notes: str,
    actor: str = "",
) -> int:
    row = conn.execute(
        """
        SELECT id
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND ativo = 1
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
         ORDER BY id
         LIMIT 1
        """,
        (organization_id, person_id, related_person_id, related_person_id, person_id),
    ).fetchone()
    if row:
        return moneyless_int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO pessoa_relacionamentos (
            organizacao_id, pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,
        (organization_id, person_id, related_person_id, relationship_type, notes or None),
    )
    relationship_id = moneyless_int(cursor.lastrowid)
    after = conn.execute("SELECT * FROM pessoa_relacionamentos WHERE id = ?", (relationship_id,)).fetchone()
    write_audit_log(
        conn,
        organization_id,
        "criar_vinculo_familiar_grupo_django",
        "pessoa_relacionamentos",
        relationship_id,
        None,
        dict(after) if after else {},
        actor=actor,
    )
    return relationship_id


def create_family_group_relationships(person_ids: str | list[int], actor: str = "") -> int:
    if isinstance(person_ids, str):
        ids = [moneyless_int(item) for item in person_ids.split(",")]
    else:
        ids = [moneyless_int(item) for item in person_ids]
    ids = sorted({item for item in ids if item})
    if len(ids) < 2:
        raise LegacyWriteError("Escolha pelo menos duas pessoas para criar a familia domiciliar.")
    if len(ids) > 30:
        raise LegacyWriteError("Grupo grande demais para associacao automatica. Revise por partes.")
    with connect_legacy_write() as conn:
        if not table_exists(conn, "pessoa_relacionamentos"):
            raise LegacyWriteError("A tabela de vinculos familiares ainda nao existe no banco.")
        people = conn.execute(
            f"""
            SELECT id, organizacao_id
              FROM pessoas
             WHERE ativo = 1 AND id IN ({','.join('?' for _ in ids)})
            """,
            tuple(ids),
        ).fetchall()
        if len(people) != len(ids):
            raise LegacyWriteError("Uma ou mais pessoas do grupo nao foram encontradas.")
        organization_ids = {moneyless_int(row["organizacao_id"]) for row in people}
        if len(organization_ids) != 1:
            raise LegacyWriteError("Todas as pessoas da familia domiciliar precisam pertencer a mesma organizacao.")
        organization_id = next(iter(organization_ids))
        created = 0
        try:
            with conn:
                for index, left_id in enumerate(ids):
                    for right_id in ids[index + 1 :]:
                        if _active_relationship_between_exists(conn, organization_id, left_id, right_id):
                            continue
                        _create_relationship_in_connection(
                            conn,
                            organization_id,
                            left_id,
                            right_id,
                            "nucleo_familiar",
                            "Criado pela central de auditoria de familias domiciliares.",
                            actor=actor,
                        )
                        created += 1
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel criar a familia domiciliar: {exc}") from exc
    return created


def _address_family_key(row: sqlite3.Row) -> tuple[str, ...]:
    return family_address_key(row)


def _relationship_between_exists(
    conn: sqlite3.Connection,
    organization_id: int,
    left_person_id: int,
    right_person_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
         LIMIT 1
        """,
        (organization_id, left_person_id, right_person_id, right_person_id, left_person_id),
    ).fetchone()
    return row is not None


def _active_relationship_between_exists(
    conn: sqlite3.Connection,
    organization_id: int,
    left_person_id: int,
    right_person_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND ativo = 1
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
         LIMIT 1
        """,
        (organization_id, left_person_id, right_person_id, right_person_id, left_person_id),
    ).fetchone()
    return row is not None


def _person_address_family_keys(conn: sqlite3.Connection, person_id: int) -> set[tuple[str, ...]]:
    address_rows = conn.execute(
        """
        SELECT cep, logradouro, numero, complemento, bairro, cidade, uf
          FROM pessoa_enderecos
         WHERE pessoa_id = ?
        """,
        (person_id,),
    ).fetchall()
    keys: set[tuple[str, ...]] = set()
    for row in address_rows:
        key = _address_family_key(row)
        if key:
            keys.add(key)
    return keys


def _is_auto_address_relationship(row: sqlite3.Row) -> bool:
    return (
        normalize_match_name(row["observacoes"] or "").find(AUTO_ADDRESS_RELATIONSHIP_MARKER) >= 0
        and normalize_query(row["tipo_relacionamento"]) == "nucleo_familiar"
    )


def _is_manual_family_suppression(row: sqlite3.Row) -> bool:
    return (
        normalize_match_name(row["observacoes"] or "").find(MANUAL_FAMILY_SUPPRESSION_MARKER) >= 0
        and normalize_query(row["tipo_relacionamento"]) == "nucleo_familiar"
    )


def _relationship_pair_has_manual_suppression(
    conn: sqlite3.Connection,
    organization_id: int,
    left_person_id: int,
    right_person_id: int,
) -> bool:
    rows = conn.execute(
        """
        SELECT tipo_relacionamento, observacoes
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND ativo = 0
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
        """,
        (organization_id, left_person_id, right_person_id, right_person_id, left_person_id),
    ).fetchall()
    return any(_is_manual_family_suppression(row) for row in rows)


def _append_relationship_note(existing: object, note: str) -> str:
    current = normalize_query(existing)
    if not current:
        return note
    if normalize_match_name(current).find(normalize_match_name(note)) >= 0:
        return current
    return f"{current} | {note}"


def _deactivate_stale_address_relationships(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    current_keys: set[tuple[str, ...]],
    actor: str = "",
) -> int:
    relationship_rows = conn.execute(
        """
        SELECT *
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND ativo = 1
           AND (pessoa_id = ? OR pessoa_relacionada_id = ?)
        """,
        (organization_id, person_id, person_id),
    ).fetchall()
    deactivated = 0
    for relationship in relationship_rows:
        if not _is_auto_address_relationship(relationship):
            continue
        related_person_id = (
            moneyless_int(relationship["pessoa_relacionada_id"])
            if moneyless_int(relationship["pessoa_id"]) == person_id
            else moneyless_int(relationship["pessoa_id"])
        )
        related_keys = _person_address_family_keys(conn, related_person_id)
        if current_keys and related_keys and current_keys.intersection(related_keys):
            continue
        before = dict(relationship)
        notes = _append_relationship_note(
            relationship["observacoes"],
            "Desativado automaticamente por endereco divergente.",
        )
        conn.execute(
            "UPDATE pessoa_relacionamentos SET ativo = 0, observacoes = ? WHERE id = ?",
            (notes, relationship["id"]),
        )
        after = dict(before)
        after["ativo"] = 0
        after["observacoes"] = notes
        write_audit_log(
            conn,
            organization_id,
            "desativar_nucleo_familiar_endereco_django",
            "pessoa_relacionamentos",
            moneyless_int(relationship["id"]),
            before,
            after,
            actor=actor,
        )
        deactivated += 1
    return deactivated


def sync_household_relationships_by_address(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    actor: str = "",
) -> dict[str, int]:
    if not table_exists(conn, "pessoa_relacionamentos"):
        return {"created": 0, "deactivated": 0}
    keys = _person_address_family_keys(conn, person_id)
    deactivated = _deactivate_stale_address_relationships(
        conn,
        organization_id,
        person_id,
        keys,
        actor=actor,
    )
    if not keys:
        return {"created": 0, "deactivated": deactivated}
    candidate_rows = conn.execute(
        """
        SELECT p.id, e.cep, e.logradouro, e.numero, e.complemento, e.bairro, e.cidade, e.uf
          FROM pessoas p
          JOIN pessoa_enderecos e ON e.pessoa_id = p.id
         WHERE p.organizacao_id = ?
           AND p.ativo = 1
           AND p.id <> ?
         ORDER BY p.id
        """,
        (organization_id, person_id),
    ).fetchall()
    created = 0
    seen: set[int] = set()
    for candidate in candidate_rows:
        related_person_id = moneyless_int(candidate["id"])
        if related_person_id in seen:
            continue
        seen.add(related_person_id)
        if _address_family_key(candidate) not in keys:
            continue
        if _active_relationship_between_exists(conn, organization_id, person_id, related_person_id):
            continue
        if _relationship_pair_has_manual_suppression(conn, organization_id, person_id, related_person_id):
            continue
        cursor = conn.execute(
            """
            INSERT INTO pessoa_relacionamentos (
                organizacao_id, pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo
            ) VALUES (?, ?, ?, 'nucleo_familiar', ?, 1)
            """,
            (
                organization_id,
                person_id,
                related_person_id,
                "Criado automaticamente por endereco completo exatamente igual.",
            ),
        )
        relationship_id = moneyless_int(cursor.lastrowid)
        after = conn.execute(
            "SELECT * FROM pessoa_relacionamentos WHERE id = ?",
            (relationship_id,),
        ).fetchone()
        write_audit_log(
            conn,
            organization_id,
            "criar_nucleo_familiar_endereco_django",
            "pessoa_relacionamentos",
            relationship_id,
            None,
            dict(after) if after else {},
            actor=actor,
        )
        created += 1
    return {"created": created, "deactivated": deactivated}


def sync_person_household_relationships(person_id: int, actor: str = "") -> dict[str, int]:
    person_id = moneyless_int(person_id)
    if not person_id:
        raise LegacyWriteError("Pessoa invalida para sincronizar familia domiciliar.")
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("Pessoa nao encontrada.")
        try:
            with conn:
                return sync_household_relationships_by_address(
                    conn,
                    moneyless_int(person["organizacao_id"]),
                    person_id,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel sincronizar familia domiciliar: {exc}") from exc


def update_person_relationship(person_id: int, relationship_id: int, payload: Any, actor: str = "") -> None:
    person_id = moneyless_int(person_id)
    relationship_id = moneyless_int(relationship_id)
    relationship_type = normalize_query(_form_value(payload, "tipo_relacionamento", "nucleo_familiar")) or "nucleo_familiar"
    notes = normalize_query(_form_value(payload, "observacoes"))
    if relationship_type not in ALLOWED_FAMILY_RELATIONSHIP_TYPES:
        raise LegacyWriteError("Tipo de relacao familiar invalido.")
    with connect_legacy_write() as conn:
        relationship = conn.execute(
            """
            SELECT *
              FROM pessoa_relacionamentos
             WHERE id = ?
               AND ativo = 1
               AND (pessoa_id = ? OR pessoa_relacionada_id = ?)
             LIMIT 1
            """,
            (relationship_id, person_id, person_id),
        ).fetchone()
        if relationship is None:
            raise LegacyWriteError("Relacao familiar nao encontrada para esta ficha.")
        before = dict(relationship)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE pessoa_relacionamentos
                       SET tipo_relacionamento = ?, observacoes = ?
                     WHERE id = ?
                    """,
                    (relationship_type, notes or None, relationship_id),
                )
                after = conn.execute("SELECT * FROM pessoa_relacionamentos WHERE id = ?", (relationship_id,)).fetchone()
                write_audit_log(
                    conn,
                    moneyless_int(relationship["organizacao_id"]),
                    "atualizar_vinculo_familiar_django",
                    "pessoa_relacionamentos",
                    relationship_id,
                    before,
                    dict(after) if after else {},
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel atualizar a relacao familiar: {exc}") from exc


def _suppress_family_relationship_in_connection(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    related_person_id: int,
    actor: str = "",
) -> int:
    relationship = conn.execute(
        """
        SELECT *
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
         ORDER BY ativo DESC, id
         LIMIT 1
        """,
        (organization_id, person_id, related_person_id, related_person_id, person_id),
    ).fetchone()
    notes = "Ignorado manualmente pelo operador para nao recriar familia domiciliar por endereco."
    if relationship is not None:
        before = dict(relationship)
        updated_notes = _append_relationship_note(relationship["observacoes"], notes)
        conn.execute(
            """
            UPDATE pessoa_relacionamentos
               SET ativo = 0, tipo_relacionamento = 'nucleo_familiar', observacoes = ?
             WHERE id = ?
            """,
            (updated_notes, relationship["id"]),
        )
        after = dict(before)
        after["ativo"] = 0
        after["tipo_relacionamento"] = "nucleo_familiar"
        after["observacoes"] = updated_notes
        relationship_id = moneyless_int(relationship["id"])
    else:
        cursor = conn.execute(
            """
            INSERT INTO pessoa_relacionamentos (
                organizacao_id, pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo
            ) VALUES (?, ?, ?, 'nucleo_familiar', ?, 0)
            """,
            (organization_id, person_id, related_person_id, notes),
        )
        relationship_id = moneyless_int(cursor.lastrowid)
        before = None
        after_row = conn.execute("SELECT * FROM pessoa_relacionamentos WHERE id = ?", (relationship_id,)).fetchone()
        after = dict(after_row) if after_row else {}
    write_audit_log(
        conn,
        organization_id,
        "ignorar_sugestao_nucleo_familiar_django",
        "pessoa_relacionamentos",
        relationship_id,
        before,
        after,
        actor=actor,
    )
    return relationship_id


def suppress_family_suggestion(person_id: int, related_person_id: int, actor: str = "") -> int:
    person_id = moneyless_int(person_id)
    related_person_id = moneyless_int(related_person_id)
    if not person_id or not related_person_id:
        raise LegacyWriteError("Escolha a pessoa principal e a pessoa relacionada.")
    if person_id == related_person_id:
        raise LegacyWriteError("A pessoa relacionada nao pode ser a propria ficha.")
    with connect_legacy_write() as conn:
        if not table_exists(conn, "pessoa_relacionamentos"):
            raise LegacyWriteError("A tabela de vinculos familiares ainda nao existe no banco.")
        person = get_person(conn, person_id)
        related_person = get_person(conn, related_person_id)
        if person is None or related_person is None:
            raise LegacyWriteError("Pessoa principal ou pessoa relacionada nao encontrada.")
        organization_id = moneyless_int(person["organizacao_id"])
        if organization_id != moneyless_int(related_person["organizacao_id"]):
            raise LegacyWriteError("As duas pessoas precisam pertencer a mesma organizacao.")
        try:
            with conn:
                return _suppress_family_relationship_in_connection(
                    conn,
                    organization_id,
                    person_id,
                    related_person_id,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel ignorar a sugestao familiar: {exc}") from exc


def suppress_family_group_suggestions(person_ids: str | list[int], actor: str = "") -> int:
    if isinstance(person_ids, str):
        ids = [moneyless_int(item) for item in person_ids.split(",")]
    else:
        ids = [moneyless_int(item) for item in person_ids]
    ids = sorted({item for item in ids if item})
    if len(ids) < 2:
        raise LegacyWriteError("Escolha pelo menos duas pessoas para ignorar a sugestao.")
    with connect_legacy_write() as conn:
        if not table_exists(conn, "pessoa_relacionamentos"):
            raise LegacyWriteError("A tabela de vinculos familiares ainda nao existe no banco.")
        people = conn.execute(
            f"""
            SELECT id, organizacao_id
              FROM pessoas
             WHERE ativo = 1 AND id IN ({','.join('?' for _ in ids)})
            """,
            tuple(ids),
        ).fetchall()
        if len(people) != len(ids):
            raise LegacyWriteError("Uma ou mais pessoas do grupo nao foram encontradas.")
        organization_ids = {moneyless_int(row["organizacao_id"]) for row in people}
        if len(organization_ids) != 1:
            raise LegacyWriteError("Todas as pessoas da familia domiciliar precisam pertencer a mesma organizacao.")
        organization_id = next(iter(organization_ids))
        suppressed = 0
        try:
            with conn:
                for index, left_id in enumerate(ids):
                    for right_id in ids[index + 1 :]:
                        if _active_relationship_between_exists(conn, organization_id, left_id, right_id):
                            continue
                        _suppress_family_relationship_in_connection(
                            conn,
                            organization_id,
                            left_id,
                            right_id,
                            actor=actor,
                        )
                        suppressed += 1
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel ignorar a sugestao familiar: {exc}") from exc
    return suppressed


def deactivate_person_relationship(person_id: int, relationship_id: int, actor: str = "") -> None:
    person_id = moneyless_int(person_id)
    relationship_id = moneyless_int(relationship_id)
    with connect_legacy_write() as conn:
        relationship = conn.execute(
            """
            SELECT *
              FROM pessoa_relacionamentos
             WHERE id = ?
               AND ativo = 1
               AND (pessoa_id = ? OR pessoa_relacionada_id = ?)
             LIMIT 1
            """,
            (relationship_id, person_id, person_id),
        ).fetchone()
        if relationship is None:
            raise LegacyWriteError("Relacao familiar nao encontrada para esta ficha.")
        before = dict(relationship)
        try:
            with conn:
                notes = _append_relationship_note(
                    relationship["observacoes"],
                    "Ignorado manualmente pelo operador para nao recriar familia domiciliar por endereco.",
                )
                conn.execute(
                    "UPDATE pessoa_relacionamentos SET ativo = 0, observacoes = ? WHERE id = ?",
                    (notes, relationship_id),
                )
                after = dict(before)
                after["ativo"] = 0
                after["observacoes"] = notes
                write_audit_log(
                    conn,
                    moneyless_int(relationship["organizacao_id"]),
                    "desativar_vinculo_familiar_django",
                    "pessoa_relacionamentos",
                    relationship_id,
                    before,
                    after,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel remover a relacao familiar: {exc}") from exc


def update_primary_contact(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    kind: str,
    value: str,
) -> None:
    value = normalize_query(value)
    existing = conn.execute(
        """
        SELECT id
          FROM pessoa_contatos
         WHERE pessoa_id = ? AND tipo = ? AND principal = 1
         ORDER BY id
         LIMIT 1
        """,
        (person_id, kind),
    ).fetchone()
    if existing and value:
        conn.execute("UPDATE pessoa_contatos SET valor = ? WHERE id = ?", (value, existing["id"]))
    elif value:
        conn.execute(
            """
            INSERT INTO pessoa_contatos (organizacao_id, pessoa_id, tipo, valor, principal)
            VALUES (?, ?, ?, ?, 1)
            """,
            (organization_id, person_id, kind, value),
        )


def update_primary_address(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    values: dict[str, str],
) -> None:
    normalized = {key: normalize_query(value) for key, value in values.items()}
    existing = primary_address(conn, person_id)
    has_any = any(normalized.values())
    if existing:
        conn.execute(
            """
            UPDATE pessoa_enderecos
               SET cep = ?, logradouro = ?, numero = ?, complemento = ?, bairro = ?, cidade = ?, uf = ?,
                   atualizado_em = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                normalized.get("cep", ""),
                normalized.get("logradouro", ""),
                normalized.get("numero", ""),
                normalized.get("complemento", ""),
                normalized.get("bairro", ""),
                normalized.get("cidade", ""),
                normalized.get("uf", ""),
                existing["id"],
            ),
        )
    elif has_any:
        conn.execute(
            """
            INSERT INTO pessoa_enderecos (
                organizacao_id, pessoa_id, tipo, cep, logradouro, numero, complemento, bairro, cidade, uf, principal
            ) VALUES (?, ?, 'residencial', ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                organization_id,
                person_id,
                normalized.get("cep", ""),
                normalized.get("logradouro", ""),
                normalized.get("numero", ""),
                normalized.get("complemento", ""),
                normalized.get("bairro", ""),
                normalized.get("cidade", ""),
                normalized.get("uf", ""),
            ),
        )


def ensure_member_profile(conn: sqlite3.Connection, organization_id: int, person_id: int, status: str) -> None:
    if not str(status or "").startswith("membro"):
        return
    existing = conn.execute(
        """
        SELECT id
          FROM pessoa_perfis
         WHERE pessoa_id = ? AND perfil = 'membro' AND ativo = 1
         LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO pessoa_perfis (organizacao_id, pessoa_id, perfil, ativo)
            VALUES (?, ?, 'membro', 1)
            """,
            (organization_id, person_id),
        )


def ensure_person_contributor(
    conn: sqlite3.Connection,
    person_id: int,
    source: str = "cadastro_django",
) -> int:
    person = get_person(conn, person_id)
    if person is None:
        return 0
    organization_id = moneyless_int(person["organizacao_id"])
    cpf_digits = clean_cpf(person["cpf"])
    existing = conn.execute(
        """
        SELECT id
          FROM contribuintes
         WHERE organizacao_id = ? AND pessoa_id = ? AND ativo = 1
         ORDER BY id
         LIMIT 1
        """,
        (organization_id, person_id),
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO contribuintes (
                organizacao_id, pessoa_id, tipo, nome, nome_normalizado, documento_principal,
                documento_tipo, origem, qualidade, status, observacoes, ativo, atualizado_em
            ) VALUES (?, ?, 'pf', ?, ?, ?, ?, ?, 'doador', 'ativo', NULL, 1, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                person_id,
                person["nome"],
                normalize_match_name(person["nome"]),
                cpf_digits or None,
                "cpf" if cpf_digits else None,
                source,
            ),
        )
        contributor_id = moneyless_int(cursor.lastrowid)
    else:
        contributor_id = moneyless_int(existing["id"])
        conn.execute(
            """
            UPDATE contribuintes
               SET nome = ?, nome_normalizado = ?, documento_principal = COALESCE(?, documento_principal),
                   documento_tipo = CASE WHEN ? <> '' THEN ? ELSE documento_tipo END,
                   atualizado_em = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                person["nome"],
                normalize_match_name(person["nome"]),
                cpf_digits or None,
                "cpf" if cpf_digits else "",
                "cpf" if cpf_digits else "",
                contributor_id,
            ),
        )
    if cpf_digits:
        exists = conn.execute(
            """
            SELECT 1
              FROM contribuintes_identificadores
             WHERE contribuinte_id = ? AND tipo = 'cpf' AND valor = ? AND ativo = 1
             LIMIT 1
            """,
            (contributor_id, cpf_digits),
        ).fetchone()
        if exists is None:
            conn.execute(
                """
                INSERT INTO contribuintes_identificadores (
                    organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
                ) VALUES (?, ?, ?, 'cpf', ?, 1, 1, ?)
                """,
                (organization_id, person_id, contributor_id, cpf_digits, f"Sincronizado a partir de {source}."),
            )
    conn.execute(
        """
        UPDATE contribuicoes
           SET contribuinte_id = ?, atualizado_em = CURRENT_TIMESTAMP
         WHERE pessoa_id = ? AND ativo = 1 AND contribuinte_id IS NULL
        """,
        (contributor_id, person_id),
    )
    return contributor_id


def _document_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def link_contributor_to_person(
    conn: sqlite3.Connection,
    contributor_id: int,
    person_id: int,
    note: str = "",
    actor: str = "",
) -> bool:
    contributor = conn.execute("SELECT * FROM contribuintes WHERE id = ?", (contributor_id,)).fetchone()
    person = get_person(conn, person_id)
    if contributor is None or person is None:
        return False
    if moneyless_int(contributor["organizacao_id"]) != moneyless_int(person["organizacao_id"]):
        return False
    if moneyless_int(contributor["pessoa_id"]) == person_id:
        return False
    before = {"contribuinte": dict(contributor)}
    conn.execute(
        """
        UPDATE contribuintes
           SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (person_id, contributor_id),
    )
    conn.execute(
        """
        UPDATE contribuintes_identificadores
           SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
         WHERE contribuinte_id = ? AND ativo = 1 AND (pessoa_id IS NULL OR pessoa_id = ?)
        """,
        (person_id, contributor_id, person_id),
    )
    conn.execute(
        """
        UPDATE contribuicoes
           SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
         WHERE contribuinte_id = ? AND ativo = 1 AND pessoa_id IS NULL
        """,
        (person_id, contributor_id),
    )
    if table_exists(conn, "pix_movimentos"):
        conn.execute(
            """
            UPDATE pix_movimentos
               SET suggested_person_id = COALESCE(suggested_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
             WHERE suggested_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
        conn.execute(
            """
            UPDATE pix_movimentos
               SET resolved_person_id = COALESCE(resolved_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
             WHERE resolved_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
    if table_exists(conn, "extrato_movimentos"):
        conn.execute(
            """
            UPDATE extrato_movimentos
               SET suggested_person_id = COALESCE(suggested_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
             WHERE suggested_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
        conn.execute(
            """
            UPDATE extrato_movimentos
               SET resolved_person_id = COALESCE(resolved_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
             WHERE resolved_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
    after = conn.execute("SELECT * FROM contribuintes WHERE id = ?", (contributor_id,)).fetchone()
    after_payload = {"contribuinte": dict(after) if after else {}, "nota_vinculo": note}
    write_audit_log(
        conn,
        moneyless_int(person["organizacao_id"]),
        "vincular_contribuinte_pessoa",
        "contribuintes",
        contributor_id,
        before,
        after_payload,
        actor=actor,
    )
    return True


def link_contributor_to_person_by_id(contributor_id: int, person_id: int, actor: str = "") -> bool:
    contributor_id = moneyless_int(contributor_id)
    person_id = moneyless_int(person_id)
    if not contributor_id or not person_id:
        raise LegacyWriteError("Escolha um contribuinte e uma pessoa validos para vincular.")
    with connect_legacy_write() as conn:
        try:
            with conn:
                linked = link_contributor_to_person(
                    conn,
                    contributor_id,
                    person_id,
                    note="Vinculo feito pela central Django de contribuintes.",
                    actor=actor,
                )
                if not linked:
                    raise LegacyWriteError("O vinculo nao foi aplicado. Verifique se a pessoa e o contribuinte existem.")
                return True
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel vincular o contribuinte: {exc}") from exc


def create_frequentador_from_contributor(contributor_id: int, family_person_id: int = 0, actor: str = "") -> int:
    contributor_id = moneyless_int(contributor_id)
    family_person_id = moneyless_int(family_person_id)
    if not contributor_id:
        raise LegacyWriteError("Contribuinte invalido para criar frequentador.")
    with connect_legacy_write() as conn:
        contributor = get_contributor(conn, contributor_id)
        if contributor is None:
            raise LegacyWriteError("Contribuinte nao encontrado.")
        if moneyless_int(contributor["pessoa_id"]):
            raise LegacyWriteError("Este contribuinte ja esta vinculado a uma pessoa.")
        organization_id = moneyless_int(contributor["organizacao_id"])
        name = normalize_query(contributor["nome"])
        if not name:
            raise LegacyWriteError("O contribuinte nao tem nome suficiente para criar uma ficha.")
        family_person = None
        if family_person_id:
            family_person = get_person(conn, family_person_id)
            if family_person is None:
                raise LegacyWriteError("A pessoa de referencia familiar nao foi encontrada.")
            if moneyless_int(family_person["organizacao_id"]) != organization_id:
                raise LegacyWriteError("A referencia familiar pertence a outra organizacao.")
        document_value = normalize_query(contributor["documento_principal"])
        document_type = normalize_query(contributor["documento_tipo"]).lower()
        document_digits = _document_digits(document_value)
        cpf_db = None
        if document_type == "cpf" and "*" not in document_value and len(document_digits) == 11:
            existing = conn.execute(
                """
                SELECT id, nome
                  FROM pessoas
                 WHERE organizacao_id = ? AND cpf = ? AND ativo = 1
                 LIMIT 1
                """,
                (organization_id, document_digits),
            ).fetchone()
            if existing is not None:
                raise LegacyWriteError(f"Ja existe uma pessoa com esse CPF: {existing['nome']}. Use o vinculo existente.")
            cpf_db = document_digits
        notes = [
            f"Criado a partir do contribuinte auxiliar #{contributor_id}: {name}.",
            f"Origem financeira preservada: {name} | {document_value or 'sem documento principal'}.",
        ]
        if contributor["tipo"] == "pj":
            notes.append("A identidade financeira original estava classificada como PJ / empresa.")
        if family_person is not None:
            notes.append(f"Referencia familiar: {family_person['nome']} (pessoa #{family_person_id}).")
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pessoas (
                        organizacao_id, unidade_preferencial_id, codigo_interno, nome, cpf, rg, data_nascimento,
                        sexo, estado_civil, email_principal, telefone_principal, whatsapp_principal, status,
                        arquivo_morto, observacoes, import_lote_id, ativo, atualizado_em
                    ) VALUES (?, NULL, '', ?, ?, '', '', '', '', '', '', '', 'frequentador', 0, ?, NULL, 1, CURRENT_TIMESTAMP)
                    """,
                    (organization_id, name, cpf_db, " ".join(notes)),
                )
                person_id = moneyless_int(cursor.lastrowid)
                snapshot = person_snapshot(conn, person_id)
                snapshot["criado_do_contribuinte_id"] = contributor_id
                if family_person is not None:
                    snapshot["referencia_familiar_id"] = family_person_id
                    snapshot["referencia_familiar_nome"] = family_person["nome"]
                write_audit_log(
                    conn,
                    organization_id,
                    "criar_frequentador_por_contribuinte_django",
                    "pessoas",
                    person_id,
                    None,
                    snapshot,
                    actor=actor,
                )
                note = "Frequentador criado automaticamente a partir do contribuinte auxiliar pela central Django."
                if family_person is not None:
                    note += f" Referencia familiar: {family_person['nome']}."
                if not link_contributor_to_person(conn, contributor_id, person_id, note=note, actor=actor):
                    raise LegacyWriteError("A ficha foi criada, mas o vinculo financeiro nao pode ser aplicado.")
                return person_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel criar frequentador: {exc}") from exc


def reconcile_contributors_for_person(
    conn: sqlite3.Connection,
    person_id: int,
    source: str = "cadastro_django",
    actor: str = "",
) -> dict[str, int]:
    person = get_person(conn, person_id)
    if person is None:
        return {"linked": 0, "created": 0}
    organization_id = moneyless_int(person["organizacao_id"])
    before_count = moneyless_int(
        conn.execute(
            "SELECT COUNT(*) AS total FROM contribuintes WHERE organizacao_id = ? AND pessoa_id = ? AND ativo = 1",
            (organization_id, person_id),
        ).fetchone()["total"]
    )
    person_contributor_id = ensure_person_contributor(conn, person_id, source=source)
    cpf_digits = clean_cpf(person["cpf"])
    person_norm = normalize_match_name(person["nome"])
    same_name_count = moneyless_int(
        conn.execute(
            """
            SELECT COUNT(*) AS total
              FROM pessoas
             WHERE organizacao_id = ? AND ativo = 1 AND nome IS NOT NULL
            """,
            (organization_id,),
        ).fetchone()["total"]
    )
    if person_norm:
        same_name_count = sum(
            1
            for row in conn.execute(
                "SELECT nome FROM pessoas WHERE organizacao_id = ? AND ativo = 1",
                (organization_id,),
            ).fetchall()
            if normalize_match_name(row["nome"]) == person_norm
        )
    linked = 0
    candidates = conn.execute(
        """
        SELECT *
          FROM contribuintes
         WHERE organizacao_id = ?
           AND ativo = 1
           AND (pessoa_id IS NULL OR pessoa_id = 0)
           AND id <> ?
         ORDER BY id
        """,
        (organization_id, person_contributor_id),
    ).fetchall()
    for contributor in candidates:
        should_link = False
        reason = ""
        contributor_doc = str(contributor["documento_principal"] or "")
        contributor_doc_type = str(contributor["documento_tipo"] or "")
        if cpf_digits and contributor_doc_type == "cpf" and _document_digits(contributor_doc) == cpf_digits:
            should_link = True
            reason = f"Reconciliado automaticamente via {source}: CPF exato."
        elif same_name_count == 1 and normalize_match_name(contributor["nome"]) == person_norm:
            should_link = True
            reason = f"Reconciliado automaticamente via {source}: nome exato unico."
        if should_link and link_contributor_to_person(conn, moneyless_int(contributor["id"]), person_id, note=reason, actor=actor):
            linked += 1
    created = 1 if before_count == 0 and person_contributor_id else 0
    return {"linked": linked, "created": created, "person_contributor_id": person_contributor_id}


def create_person(payload: dict[str, str], actor: str = "") -> int:
    name = normalize_query(payload.get("nome"))
    if not name:
        raise LegacyWriteError("Nome e obrigatorio.")
    status = normalize_query(payload.get("status") or "frequentador")
    if status not in ALLOWED_PERSON_STATUSES:
        raise LegacyWriteError("Status invalido para a ficha.")
    with connect_legacy_write() as conn:
        organization_id = default_organization_id(conn)
        cpf_db = _manual_cpf_or_error(payload.get("cpf"))
        _assert_unique_cpf(conn, organization_id, cpf_db or "")
        email_db = _manual_email_or_error(payload.get("email_principal"))
        clean_payload = {**payload, "email_principal": email_db}
        member_code = (
            resolved_member_code(conn, organization_id, clean_payload.get("codigo_interno", ""))
            if status_grants_member_code(status)
            else ""
        )
        arquivo_morto = 1 if status == "arquivo_morto" else 0
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pessoas (
                        organizacao_id, unidade_preferencial_id, codigo_interno, nome, nome_social, cpf, rg,
                        data_nascimento, sexo, estado_civil, email_principal, telefone_principal,
                        whatsapp_principal, status, arquivo_morto, observacoes, import_lote_id, ativo,
                        atualizado_em
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, CURRENT_TIMESTAMP)
                    """,
                    (
                        organization_id,
                        member_code,
                        name,
                        normalize_query(payload.get("nome_social")),
                        cpf_db,
                        normalize_query(payload.get("rg")),
                        normalize_query(payload.get("data_nascimento")),
                        normalize_query(payload.get("sexo")),
                        normalize_query(payload.get("estado_civil")),
                        email_db,
                        normalize_query(payload.get("telefone_principal")),
                        normalize_query(payload.get("whatsapp_principal")),
                        status,
                        arquivo_morto,
                        normalize_query(payload.get("observacoes")),
                    ),
                )
                person_id = moneyless_int(cursor.lastrowid)
                related_sync = _sync_related_person_data(conn, organization_id, person_id, clean_payload, status, actor=actor)
                reconcile = reconcile_contributors_for_person(
                    conn,
                    person_id,
                    source="criacao_de_pessoa_django",
                    actor=actor,
                )
                saved = person_snapshot(conn, person_id)
                saved["sincronizacao_relacionamentos"] = related_sync
                saved["reconciliacao_contribuintes"] = reconcile
                write_audit_log(conn, organization_id, "criar_cadastro", "pessoas", person_id, None, saved, actor=actor)
                return person_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel criar a ficha: {exc}") from exc


def update_person(person_id: int, payload: dict[str, str], actor: str = "") -> None:
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("Pessoa nao encontrada.")
        if not int(person["ativo"] or 0):
            raise LegacyWriteError("Esta ficha esta fora do cadastro operacional.")
        name = normalize_query(payload.get("nome"))
        if not name:
            raise LegacyWriteError("Nome e obrigatorio.")
        status = normalize_query(payload.get("status") or person["status"] or "frequentador")
        if status not in ALLOWED_PERSON_STATUSES:
            raise LegacyWriteError("Status invalido para a ficha.")
        organization_id = moneyless_int(person["organizacao_id"])
        cpf_db = _manual_cpf_or_error(payload.get("cpf"))
        _assert_unique_cpf(conn, organization_id, cpf_db or "", ignore_person_id=person_id)
        email_db = _manual_email_or_error(payload.get("email_principal"))
        clean_payload = {**payload, "email_principal": email_db}
        current_member_code = clean_member_code(person["codigo_interno"])
        allow_member_code_edit = payload.get("allow_member_code_edit") == "1"
        if current_member_code:
            if allow_member_code_edit:
                member_code = resolved_member_code(
                    conn,
                    organization_id,
                    payload.get("codigo_interno", "") or current_member_code,
                    ignore_person_id=person_id,
                )
            else:
                member_code = current_member_code
        elif status_grants_member_code(status):
            member_code = resolved_member_code(
                conn,
                organization_id,
                payload.get("codigo_interno", "") if allow_member_code_edit else "",
                ignore_person_id=person_id,
            )
        else:
            member_code = ""
        arquivo_morto = 1 if status == "arquivo_morto" else 0
        before = person_snapshot(conn, person_id)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE pessoas
                       SET codigo_interno = ?, nome = ?, nome_social = ?, cpf = ?, rg = ?,
                           data_nascimento = ?, sexo = ?, estado_civil = ?, email_principal = ?,
                           telefone_principal = ?, whatsapp_principal = ?, status = ?,
                           arquivo_morto = ?, observacoes = ?, atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (
                        member_code,
                        name,
                        normalize_query(payload.get("nome_social")),
                        cpf_db,
                        normalize_query(payload.get("rg")),
                        normalize_query(payload.get("data_nascimento")),
                        normalize_query(payload.get("sexo")),
                        normalize_query(payload.get("estado_civil")),
                        email_db,
                        normalize_query(payload.get("telefone_principal")),
                        normalize_query(payload.get("whatsapp_principal")),
                        status,
                        arquivo_morto,
                        normalize_query(payload.get("observacoes")),
                        person_id,
                    ),
                )
                related_sync = _sync_related_person_data(conn, organization_id, person_id, clean_payload, status, actor=actor)
                reconcile = reconcile_contributors_for_person(
                    conn,
                    person_id,
                    source="atualizacao_de_pessoa_django",
                    actor=actor,
                )
                after = person_snapshot(conn, person_id)
                after["sincronizacao_relacionamentos"] = related_sync
                after["reconciliacao_contribuintes"] = reconcile
                write_audit_log(conn, organization_id, "atualizar_cadastro", "pessoas", person_id, before, after, actor=actor)
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel atualizar a ficha: {exc}") from exc


def _sync_related_person_data(
    conn: sqlite3.Connection,
    organization_id: int,
    person_id: int,
    payload: dict[str, str],
    status: str,
    actor: str = "",
) -> dict[str, int]:
    update_primary_contact(conn, organization_id, person_id, "email", payload.get("email_principal", ""))
    update_primary_contact(conn, organization_id, person_id, "telefone", payload.get("telefone_principal", ""))
    update_primary_contact(conn, organization_id, person_id, "whatsapp", payload.get("whatsapp_principal", ""))
    update_primary_address(
        conn,
        organization_id,
        person_id,
        {
            "cep": payload.get("cep", ""),
            "logradouro": payload.get("logradouro", ""),
            "numero": payload.get("numero", ""),
            "complemento": payload.get("complemento", ""),
            "bairro": payload.get("bairro", ""),
            "cidade": payload.get("cidade", ""),
            "uf": payload.get("uf", ""),
        },
    )
    household_summary = sync_household_relationships_by_address(conn, organization_id, person_id, actor=actor)
    ensure_member_profile(conn, organization_id, person_id, status)
    return {
        "nucleos_familiares_criados": household_summary["created"],
        "nucleos_familiares_desativados": household_summary["deactivated"],
    }


def get_person_form_initial(person_id: int) -> dict[str, str] | None:
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            return None
        if not int(person["ativo"] or 0):
            return None
        address = primary_address(conn, person_id)
        initial = empty_person_form()
        for key in [
            "codigo_interno",
            "nome",
            "nome_social",
            "cpf",
            "rg",
            "data_nascimento",
            "sexo",
            "estado_civil",
            "email_principal",
            "telefone_principal",
            "whatsapp_principal",
            "status",
            "observacoes",
        ]:
            initial[key] = normalize_query(person[key] if key in person.keys() else "")
        if address:
            for key in ["cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"]:
                initial[key] = normalize_query(address[key])
        return initial


def _assert_valid_contribution_catalogs(
    conn: sqlite3.Connection,
    organization_id: int,
    contribution_type_id: int,
    receiving_form_id: int,
    campaign_id: int = 0,
) -> None:
    contribution_type = conn.execute(
        """
        SELECT 1
          FROM tipos_contribuicao
         WHERE id = ? AND organizacao_id = ? AND ativo = 1
         LIMIT 1
        """,
        (contribution_type_id, organization_id),
    ).fetchone()
    if contribution_type is None:
        raise LegacyWriteError("Tipo de contribuicao invalido.")
    if receiving_form_id:
        receiving_form = conn.execute(
            """
            SELECT 1
              FROM formas_recebimento
             WHERE id = ? AND organizacao_id = ? AND ativo = 1
             LIMIT 1
            """,
            (receiving_form_id, organization_id),
        ).fetchone()
        if receiving_form is None:
            raise LegacyWriteError("Forma de recebimento invalida.")
    if campaign_id:
        campaign = conn.execute(
            """
            SELECT 1
              FROM campanhas
             WHERE id = ? AND organizacao_id = ? AND COALESCE(status, 'ativa') = 'ativa'
             LIMIT 1
            """,
            (campaign_id, organization_id),
        ).fetchone()
        if campaign is None:
            raise LegacyWriteError("Campanha/destinacao invalida.")


def _contribution_payload(data: Any) -> dict[str, object]:
    received_on = normalize_query(_form_value(data, "data_recebimento"))
    competence, competence_order = competencia_from_date(received_on)
    status = normalize_query(_form_value(data, "status_operacional", "regular")) or "regular"
    if status not in CONTRIBUTION_STATUS_OPTIONS:
        raise LegacyWriteError("Status operacional invalido para ajuste manual.")
    payload = {
        "data_recebimento": received_on,
        "competencia": competence,
        "competencia_ordem": competence_order,
        "valor": parse_money(_form_value(data, "valor")),
        "tipo_contribuicao_id": moneyless_int(_form_value(data, "tipo_contribuicao_id")),
        "campanha_id": moneyless_int(_form_value(data, "campanha_id")) or None,
        "forma_recebimento_id": moneyless_int(_form_value(data, "forma_recebimento_id")) or None,
        "status_operacional": status,
        "observacoes": normalize_query(_form_value(data, "observacoes")),
        "justificativa": normalize_query(_form_value(data, "justificativa")),
    }
    if not payload["tipo_contribuicao_id"]:
        raise LegacyWriteError("Escolha o tipo de contribuicao.")
    if len(str(payload["justificativa"])) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para o ajuste manual.")
    return payload


def create_contribution(payload: Any, actor: str = "") -> int:
    person_id = moneyless_int(_form_value(payload, "pessoa_id"))
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("Escolha uma pessoa valida para registrar a contribuicao.")
        organization_id = moneyless_int(person["organizacao_id"])
        values = _contribution_payload(payload)
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(values["tipo_contribuicao_id"]),
            moneyless_int(values["forma_recebimento_id"]),
            moneyless_int(values["campanha_id"]),
        )
        try:
            with conn:
                contributor_id = ensure_person_contributor(conn, person_id, source="lancamento_manual_django")
                cursor = conn.execute(
                    """
                    INSERT INTO contribuicoes (
                        organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                        campanha_id, data_recebimento, competencia, competencia_ordem,
                        valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                        ativo, atualizado_em
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                    """,
                    (
                        organization_id,
                        person_id,
                        contributor_id or None,
                        moneyless_int(values["tipo_contribuicao_id"]),
                        moneyless_int(values["campanha_id"]) or None,
                        values["data_recebimento"],
                        values["competencia"],
                        values["competencia_ordem"],
                        float(values["valor"]),
                        moneyless_int(values["forma_recebimento_id"]) or None,
                        values["observacoes"],
                        values["status_operacional"],
                    ),
                )
                contribution_id = moneyless_int(cursor.lastrowid)
                after = dict(get_contribution(conn, contribution_id) or {})
                after["justificativa_operador"] = values["justificativa"]
                write_audit_log(
                    conn,
                    organization_id,
                    "lancar_contribuicao_django",
                    "contribuicoes",
                    contribution_id,
                    None,
                    after,
                    actor=actor,
                )
                return contribution_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel registrar a contribuicao: {exc}") from exc


def update_contribution(contribution_id: int, payload: Any, actor: str = "") -> None:
    with connect_legacy_write() as conn:
        current = get_contribution(conn, contribution_id)
        if current is None or not moneyless_int(current["ativo"]):
            raise LegacyWriteError("Contribuicao nao encontrada.")
        organization_id = moneyless_int(current["organizacao_id"])
        values = _contribution_payload(payload)
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(values["tipo_contribuicao_id"]),
            moneyless_int(values["forma_recebimento_id"]),
            moneyless_int(values["campanha_id"]),
        )
        before = dict(current)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE contribuicoes
                       SET tipo_contribuicao_id = ?,
                           campanha_id = ?,
                           data_recebimento = ?,
                           competencia = ?,
                           competencia_ordem = ?,
                           valor = ?,
                           forma_recebimento_id = ?,
                           observacoes = ?,
                           status_operacional = ?,
                           atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (
                        moneyless_int(values["tipo_contribuicao_id"]),
                        moneyless_int(values["campanha_id"]) or None,
                        values["data_recebimento"],
                        values["competencia"],
                        moneyless_int(values["competencia_ordem"]),
                        float(values["valor"]),
                        moneyless_int(values["forma_recebimento_id"]) or None,
                        values["observacoes"],
                        values["status_operacional"],
                        contribution_id,
                    ),
                )
                after = dict(get_contribution(conn, contribution_id) or {})
                after["justificativa_operador"] = values["justificativa"]
                after["ajuste_manual_django"] = True
                write_audit_log(
                    conn,
                    organization_id,
                    "ajustar_contribuicao_manual_django",
                    "contribuicoes",
                    contribution_id,
                    before,
                    after,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel ajustar a contribuicao: {exc}") from exc


def _field_value(data: Any, key: str, index: int, default: str = "") -> str:
    return _form_value(data, f"{key}_{index}", default)


def _reference_id(value: object) -> int:
    text = normalize_query(value)
    if not text:
        return 0
    match = re.match(r"^\D*(\d+)", text)
    return moneyless_int(match.group(1)) if match else 0


def _document_kind(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11:
        return "cpf"
    if len(digits) == 14:
        return "cnpj"
    return None


def _contributor_type(name: object, document: object) -> str:
    digits = "".join(ch for ch in str(document or "") if ch.isdigit())
    normalized = normalize_match_name(name)
    if len(digits) == 14 or any(term in normalized for term in ("LTDA", "MEI", "EIRELI", "IGREJA", "ASSOCIACAO", "COMERCIO")):
        return "pj"
    return "pf"


def ensure_manual_contributor(
    conn: sqlite3.Connection,
    organization_id: int,
    name: str,
    document: str = "",
    source: str = "lancamento_manual_django",
) -> int:
    name = normalize_query(name)
    document = normalize_query(document)
    digits = "".join(ch for ch in document if ch.isdigit())
    document_kind = _document_kind(document)
    if not name and not digits:
        return 0
    if digits:
        existing = conn.execute(
            """
            SELECT id
              FROM contribuintes
             WHERE organizacao_id = ?
               AND ativo = 1
               AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(documento_principal, ''), '.', ''), '-', ''), '/', ''), ' ', '') = ?
             ORDER BY pessoa_id IS NULL, id
             LIMIT 1
            """,
            (organization_id, digits),
        ).fetchone()
        if existing is not None:
            return moneyless_int(existing["id"])
    normalized_name = normalize_match_name(name)
    if normalized_name:
        existing = conn.execute(
            """
            SELECT id
              FROM contribuintes
             WHERE organizacao_id = ? AND ativo = 1 AND nome_normalizado = ?
             ORDER BY pessoa_id IS NULL, id
             LIMIT 1
            """,
            (organization_id, normalized_name),
        ).fetchone()
        if existing is not None:
            return moneyless_int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO contribuintes (
            organizacao_id, pessoa_id, tipo, nome, nome_normalizado, documento_principal,
            documento_tipo, origem, qualidade, status, observacoes, ativo, atualizado_em
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'doador', 'ativo', ?, 1, CURRENT_TIMESTAMP)
        """,
        (
            organization_id,
            _contributor_type(name, document),
            name or document,
            normalized_name or normalize_match_name(document),
            digits or document or None,
            document_kind,
            source,
            "Criado por lancamento manual assistido.",
        ),
    )
    contributor_id = moneyless_int(cursor.lastrowid)
    if digits:
        conn.execute(
            """
            INSERT INTO contribuintes_identificadores (
                organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
            ) VALUES (?, NULL, ?, ?, ?, 1, 1, ?)
            """,
            (organization_id, contributor_id, document_kind or "documento", digits, f"Sincronizado a partir de {source}."),
        )
    return contributor_id


def _validated_contributor(conn: sqlite3.Connection, organization_id: int, contributor_id: int) -> int:
    if not contributor_id:
        return 0
    row = conn.execute(
        "SELECT id FROM contribuintes WHERE id = ? AND organizacao_id = ? AND ativo = 1",
        (contributor_id, organization_id),
    ).fetchone()
    if row is None:
        raise LegacyWriteError("Contribuinte auxiliar invalido.")
    return moneyless_int(row["id"])


def _manual_line_payloads(data: Any, organization_id: int, conn: sqlite3.Connection) -> list[dict[str, object]]:
    line_count = moneyless_int(_form_value(data, "line_count")) or 8
    rows: list[dict[str, object]] = []
    for index in range(1, line_count + 1):
        value_text = _field_value(data, "linha_valor", index)
        person_id = moneyless_int(_field_value(data, "linha_pessoa_id", index))
        if not person_id:
            person_id = _reference_id(_field_value(data, "linha_pessoa_ref", index))
        contributor_id = moneyless_int(_field_value(data, "linha_contribuinte_id", index))
        if not contributor_id:
            contributor_id = _reference_id(_field_value(data, "linha_contribuinte_ref", index))
        contributor_name = _field_value(data, "linha_contribuinte_nome", index)
        document = _field_value(data, "linha_documento", index)
        type_id = moneyless_int(_field_value(data, "linha_tipo_contribuicao_id", index))
        campaign_id = moneyless_int(_field_value(data, "linha_campanha_id", index))
        notes = _field_value(data, "linha_observacoes", index)
        if not any([value_text, person_id, contributor_id, contributor_name, document, type_id, campaign_id, notes]):
            continue
        if not value_text:
            raise LegacyWriteError(f"Informe o valor da linha {index}.")
        value = parse_money(value_text)
        if not type_id:
            raise LegacyWriteError(f"Escolha a destinacao/tipo da linha {index}.")
        _assert_valid_contribution_catalogs(conn, organization_id, type_id, 0, campaign_id)
        resolved_person_id = 0
        if person_id:
            person = get_person(conn, person_id)
            if person is None or moneyless_int(person["organizacao_id"]) != organization_id:
                raise LegacyWriteError(f"Pessoa invalida na linha {index}.")
            resolved_person_id = person_id
            resolved_contributor_id = ensure_person_contributor(conn, person_id, source="rateio_manual_django")
        else:
            resolved_contributor_id = _validated_contributor(conn, organization_id, contributor_id)
            if not resolved_contributor_id:
                resolved_contributor_id = ensure_manual_contributor(
                    conn,
                    organization_id,
                    contributor_name,
                    document,
                    source="rateio_manual_django",
                )
            if not resolved_contributor_id:
                raise LegacyWriteError(f"Informe pessoa ou contribuinte na linha {index}.")
        rows.append(
            {
                "index": index,
                "pessoa_id": resolved_person_id or None,
                "contribuinte_id": resolved_contributor_id or None,
                "tipo_contribuicao_id": type_id,
                "campanha_id": campaign_id or None,
                "valor": float(value),
                "observacoes": notes,
            }
        )
    if not rows:
        raise LegacyWriteError("Informe pelo menos uma linha de rateio/contribuicao.")
    return rows


def _envelope_line_payloads(
    data: Any,
    organization_id: int,
    conn: sqlite3.Connection,
    expected_total: float,
    main_person_id: int = 0,
    main_contributor_id: int = 0,
) -> list[dict[str, object]]:
    default_type_id = moneyless_int(_form_value(data, "tipo_contribuicao_id_padrao"))
    default_campaign_id = moneyless_int(_form_value(data, "campanha_id_padrao"))
    if default_type_id:
        _assert_valid_contribution_catalogs(conn, organization_id, default_type_id, 0, default_campaign_id)

    def resolved_entry(
        index: int,
        person_id: int,
        contributor_id: int,
        contributor_name: str,
        document: str,
    ) -> tuple[int | None, int | None]:
        if not person_id and not contributor_id and not contributor_name and not document:
            person_id = main_person_id
            contributor_id = main_contributor_id if not person_id else 0
        if person_id:
            person = get_person(conn, person_id)
            if person is None or moneyless_int(person["organizacao_id"]) != organization_id:
                raise LegacyWriteError(f"Pessoa invalida na linha {index}.")
            return person_id, ensure_person_contributor(conn, person_id, source="envelope_manual_django") or None

        resolved_contributor_id = _validated_contributor(conn, organization_id, contributor_id)
        if not resolved_contributor_id:
            resolved_contributor_id = ensure_manual_contributor(
                conn,
                organization_id,
                contributor_name,
                document,
                source="envelope_manual_django",
            )
        if not resolved_contributor_id:
            raise LegacyWriteError(f"Informe pessoa ou contribuinte na linha {index}.")
        return None, resolved_contributor_id

    line_count = moneyless_int(_form_value(data, "line_count")) or 10
    rows: list[dict[str, object]] = []
    for index in range(1, line_count + 1):
        value_text = _field_value(data, "linha_valor", index)
        participant_ref = _field_value(data, "linha_participante_ref", index)
        participant_person_id, participant_contributor_id, participant_name, participant_document = (
            _resolve_envelope_participant_reference(conn, organization_id, participant_ref)
        )
        person_id = moneyless_int(_field_value(data, "linha_pessoa_id", index))
        if not person_id:
            person_id = participant_person_id or _reference_id(_field_value(data, "linha_pessoa_ref", index))
        contributor_id = moneyless_int(_field_value(data, "linha_contribuinte_id", index))
        if not contributor_id:
            contributor_id = participant_contributor_id or _reference_id(_field_value(data, "linha_contribuinte_ref", index))
        contributor_name = _field_value(data, "linha_contribuinte_nome", index) or participant_name
        document = _field_value(data, "linha_documento", index) or participant_document
        line_type_id = moneyless_int(_field_value(data, "linha_tipo_contribuicao_id", index))
        line_campaign_id = moneyless_int(_field_value(data, "linha_campanha_id", index))
        notes = _field_value(data, "linha_observacoes", index)
        if not any([value_text, participant_ref, person_id, contributor_id, contributor_name, document, line_type_id, line_campaign_id, notes]):
            continue
        if not value_text:
            raise LegacyWriteError(f"Informe o valor da linha {index}.")
        value = parse_money(value_text)
        type_id = line_type_id or default_type_id
        campaign_id = line_campaign_id or default_campaign_id
        if not type_id:
            raise LegacyWriteError(f"Escolha o tipo principal do envelope ou a destinacao da linha {index}.")
        _assert_valid_contribution_catalogs(conn, organization_id, type_id, 0, campaign_id)
        resolved_person_id, resolved_contributor_id = resolved_entry(
            index,
            person_id,
            contributor_id,
            contributor_name,
            document,
        )
        rows.append(
            {
                "index": index,
                "pessoa_id": resolved_person_id,
                "contribuinte_id": resolved_contributor_id,
                "tipo_contribuicao_id": type_id,
                "campanha_id": campaign_id or None,
                "valor": float(value),
                "observacoes": notes,
            }
        )
    if rows:
        return rows
    if not default_type_id:
        raise LegacyWriteError("Escolha o tipo principal do envelope.")
    contributor_name = _form_value(data, "nome_informado")
    document = _form_value(data, "documento_informado")
    resolved_person_id, resolved_contributor_id = resolved_entry(
        1,
        main_person_id,
        main_contributor_id,
        contributor_name,
        document,
    )
    return [
        {
            "index": 1,
            "pessoa_id": resolved_person_id,
            "contribuinte_id": resolved_contributor_id,
            "tipo_contribuicao_id": default_type_id,
            "campanha_id": default_campaign_id or None,
            "valor": float(expected_total),
            "observacoes": "Lancamento principal do envelope.",
        }
    ]


def _line_observations(base: str, line_notes: str, origin_note: str) -> str:
    parts = [normalize_query(base), normalize_query(line_notes), normalize_query(origin_note)]
    return "\n".join(part for part in parts if part)


def _resolve_envelope_participant_reference(
    conn: sqlite3.Connection,
    organization_id: int,
    raw_value: object,
) -> tuple[int, int, str, str]:
    text = normalize_query(raw_value)
    if not text:
        return 0, 0, "", ""
    person_match = re.match(r"^Pessoa\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    if person_match:
        return moneyless_int(person_match.group(1)), 0, "", ""
    contributor_match = re.match(r"^Contribuinte\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    if contributor_match:
        return 0, moneyless_int(contributor_match.group(1)), "", ""
    ref_id = _reference_id(text)
    if ref_id:
        person = get_person(conn, ref_id)
        if person is not None and moneyless_int(person["organizacao_id"]) == organization_id:
            return ref_id, 0, "", ""
        contributor = conn.execute(
            "SELECT id FROM contribuintes WHERE id = ? AND organizacao_id = ? AND ativo = 1",
            (ref_id, organization_id),
        ).fetchone()
        if contributor is not None:
            return 0, ref_id, "", ""
    document_match = re.search(r"(\d[\d.\-/ ]{8,}\d)", text)
    document = document_match.group(1) if document_match else ""
    name = re.split(r"\s+·\s+|\s+-\s+", text, maxsplit=1)[0].strip()
    return 0, 0, name or text, document


def _optional_money_value(value: object) -> float | None:
    text = normalize_query(value)
    if text.lower() in {"none", "null"}:
        return None
    if not text:
        return None
    return round(float(parse_money(text)), 2)


def _optional_trace_text(value: object) -> str:
    text = normalize_query(value)
    return "" if text.lower() in {"none", "null"} else text


def _traceability_value_for_receiving_text(code_or_name: object) -> str:
    code = normalize_match_name(code_or_name)
    if "DINHEIRO" in code:
        return "dinheiro"
    if "PIX" in code:
        return "pix"
    if "TRANSFERENCIA" in code or "TED" in code or "DOC" in code:
        return "transferencia"
    if "CARTAO" in code:
        return "cartao_credito"
    if "CHEQUE" in code:
        return "cheque"
    if "DEPOSITO" in code:
        return "deposito"
    return ""


def _traceability_value_from_form_id(conn: sqlite3.Connection, form_id: int | None) -> str:
    if not form_id:
        return ""
    row = conn.execute(
        "SELECT codigo, nome FROM formas_recebimento WHERE id = ? AND ativo = 1",
        (moneyless_int(form_id),),
    ).fetchone()
    if row is None:
        return ""
    return _traceability_value_for_receiving_text(row["codigo"] or row["nome"])


def _envelope_traceability_payload(
    payload: Any,
    conn: sqlite3.Connection | None = None,
    form_id: int | None = None,
    expected_total: float | None = None,
) -> dict[str, object]:
    status = normalize_query(_form_value(payload, "rastreio_status_conciliacao", "pendente")) or "pendente"
    if status not in {"pendente", "conciliado", "divergente", "ignorado"}:
        status = "pendente"
    form_value = _optional_trace_text(_form_value(payload, "rastreio_forma_identificada"))
    if not form_value and conn is not None:
        form_value = _traceability_value_from_form_id(conn, form_id)
    operation_value = _optional_money_value(_form_value(payload, "rastreio_valor_operacao"))
    if operation_value is None and expected_total is not None:
        operation_value = round(float(expected_total), 2)
    return {
        "rastreio_forma_identificada": form_value,
        "rastreio_banco_operadora": _optional_trace_text(_form_value(payload, "rastreio_banco_operadora")),
        "rastreio_numero_cheque": _optional_trace_text(_form_value(payload, "rastreio_numero_cheque")),
        "rastreio_numero_operacao": _optional_trace_text(_form_value(payload, "rastreio_numero_operacao")),
        "rastreio_nsu_tid": _optional_trace_text(_form_value(payload, "rastreio_nsu_tid")),
        "rastreio_ultimos_digitos_cartao": _optional_trace_text(_form_value(payload, "rastreio_ultimos_digitos_cartao")),
        "rastreio_data_operacao": _optional_trace_text(_form_value(payload, "rastreio_data_operacao")),
        "rastreio_valor_operacao": operation_value,
        "rastreio_status_conciliacao": status,
        "rastreio_observacoes": _optional_trace_text(_form_value(payload, "rastreio_observacoes")),
    }


def _traceability_note(traceability: Mapping[str, object]) -> str:
    labels = [
        ("Forma", "rastreio_forma_identificada"),
        ("Banco/operadora", "rastreio_banco_operadora"),
        ("Cheque", "rastreio_numero_cheque"),
        ("Operacao/autorizacao", "rastreio_numero_operacao"),
        ("NSU/TID", "rastreio_nsu_tid"),
        ("Cartao final", "rastreio_ultimos_digitos_cartao"),
        ("Data operacao", "rastreio_data_operacao"),
        ("Valor operacao", "rastreio_valor_operacao"),
        ("Status conciliacao", "rastreio_status_conciliacao"),
    ]
    parts: list[str] = []
    for label, key in labels:
        value = traceability.get(key)
        if value not in (None, ""):
            if key == "rastreio_valor_operacao":
                value = f"{float(value):.2f}"
            parts.append(f"{label}: {value}")
    notes = normalize_query(traceability.get("rastreio_observacoes"))
    if notes:
        parts.append(f"Obs conciliacao: {notes}")
    return "Rastreabilidade financeira: " + "; ".join(parts) + "." if parts else ""


def _row_value(row: sqlite3.Row, key: str, default: object = None) -> object:
    return row[key] if key in row.keys() else default


def create_manual_contribution_batch(payload: Any, actor: str = "") -> list[int]:
    with connect_legacy_write() as conn:
        organization_id = default_organization_id(conn)
        received_on = normalize_query(_form_value(payload, "data_recebimento"))
        competence, competence_order = competencia_from_date(received_on)
        form_id = moneyless_int(_form_value(payload, "forma_recebimento_id")) or None
        status = normalize_query(_form_value(payload, "status_operacional", "regular")) or "regular"
        if status not in CONTRIBUTION_STATUS_OPTIONS:
            raise LegacyWriteError("Status operacional invalido para lancamento manual.")
        justification = normalize_query(_form_value(payload, "justificativa"))
        if len(justification) < 8:
            raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
        header_notes = normalize_query(_form_value(payload, "observacoes"))
        source_label = normalize_query(_form_value(payload, "origem_operacional"))
        expected_total_text = normalize_query(_form_value(payload, "valor_total"))
        lines = _manual_line_payloads(payload, organization_id, conn)
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(lines[0]["tipo_contribuicao_id"]),
            moneyless_int(form_id),
            0,
        )
        total = round(sum(float(line["valor"]) for line in lines), 2)
        if expected_total_text:
            expected_total = round(float(parse_money(expected_total_text)), 2)
            if abs(total - expected_total) > 0.009:
                raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total informado ({expected_total:.2f}).")
        origin_note = f"Origem manual: {source_label or 'envelope/e-mail/comprovante informado pelo operador'}."
        created_ids: list[int] = []
        try:
            with conn:
                for line in lines:
                    cursor = conn.execute(
                        """
                        INSERT INTO contribuicoes (
                            organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, data_recebimento, competencia, competencia_ordem,
                            valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                            ativo, atualizado_em
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            received_on,
                            competence,
                            competence_order,
                            line["valor"],
                            form_id,
                            _line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                            status,
                        ),
                    )
                    contribution_id = moneyless_int(cursor.lastrowid)
                    created_ids.append(contribution_id)
                    after = dict(get_contribution(conn, contribution_id) or {})
                    after["justificativa_operador"] = justification
                    after["lote_manual_rateio_total"] = total
                    after["origem_operacional"] = source_label
                    write_audit_log(
                        conn,
                        organization_id,
                        "lancar_contribuicao_manual_rateada_django",
                        "contribuicoes",
                        contribution_id,
                        None,
                        after,
                        actor=actor,
                    )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel registrar o rateio manual: {exc}") from exc
    return created_ids


def _slug_part(value: object, fallback: str = "sem_nome") -> str:
    normalized = normalize_match_name(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug or fallback


def _competence_from_payload(payload: Any, received_on: str) -> tuple[str, int]:
    month_value = normalize_query(_form_value(payload, "competencia_mes"))
    if re.fullmatch(r"\d{4}-\d{2}", month_value):
        return competencia_from_date(f"{month_value}-01")
    return competencia_from_date(received_on)


def _file_payload_from_bytes(filename: str, content_type: str, payload: bytes) -> dict[str, object]:
    filename = normalize_query(filename) or "envelope"
    suffix = Path(filename).suffix.lower()
    if suffix not in ENVELOPE_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ENVELOPE_ALLOWED_EXTENSIONS))
        raise LegacyWriteError(f"Formato de envelope nao permitido. Use: {allowed}.")
    if not payload:
        raise LegacyWriteError("O arquivo do envelope esta vazio.")
    if len(payload) > 20 * 1024 * 1024:
        raise LegacyWriteError("O arquivo do envelope excede 20 MB.")
    return {
        "filename": filename,
        "suffix": suffix,
        "content_type": normalize_query(content_type) or mimetypes.guess_type(filename)[0] or "",
        "payload": payload,
        "size": len(payload),
        "hash": hashlib.sha256(payload).hexdigest(),
    }


def _file_payload_from_path(path_value: object) -> dict[str, object]:
    source_path = _resolve_local_path(path_value)
    if not source_path.exists() or not source_path.is_file():
        raise LegacyWriteError(f"O caminho local informado para o envelope nao foi encontrado: {source_path}")
    return _file_payload_from_bytes(
        source_path.name,
        mimetypes.guess_type(source_path.name)[0] or "",
        source_path.read_bytes(),
    )


def _file_payload_from_upload(upload: Any) -> dict[str, object]:
    filename = normalize_query(getattr(upload, "name", "") or "envelope")
    chunks = getattr(upload, "chunks", None)
    payload = b"".join(chunks()) if chunks else bytes(upload)
    return _file_payload_from_bytes(filename, normalize_query(getattr(upload, "content_type", "")), payload)


def _uploaded_file_payload(upload: Any, local_path: object = "") -> dict[str, object]:
    path_text = normalize_query(local_path)
    if upload is None and path_text:
        return _file_payload_from_path(path_text)
    if upload is None:
        raise LegacyWriteError("Anexe a imagem ou PDF do envelope para preservar a auditoria.")
    return _file_payload_from_upload(upload)


def _envelope_lot_id(
    conn: sqlite3.Connection,
    organization_id: int,
    competence: str,
    competence_order: int,
    lot_name: str,
) -> int:
    name = normalize_query(lot_name) or f"Envelopes {competence}"
    existing = conn.execute(
        """
        SELECT id
          FROM envelope_lotes
         WHERE organizacao_id = ? AND competencia_ordem = ? AND nome = ?
         LIMIT 1
        """,
        (organization_id, competence_order, name),
    ).fetchone()
    if existing is not None:
        return moneyless_int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO envelope_lotes (
            organizacao_id, competencia, competencia_ordem, nome, status, observacoes, atualizado_em
        ) VALUES (?, ?, ?, ?, 'aberto', ?, CURRENT_TIMESTAMP)
        """,
        (
            organization_id,
            competence,
            competence_order,
            name,
            "Lote criado automaticamente pelo lancamento manual de envelopes.",
        ),
    )
    return moneyless_int(cursor.lastrowid)


def _store_envelope_file(
    lot_id: int,
    envelope_id: int,
    competence: str,
    competence_order: int,
    file_payload: dict[str, object],
) -> tuple[Path, Path]:
    root = envelope_upload_root()
    month_folder = f"{competence_order}_{_slug_part(competence, 'competencia')}"
    folder = root / month_folder / f"lote_{lot_id:06d}"
    folder.mkdir(parents=True, exist_ok=True)
    original_stem = _slug_part(Path(str(file_payload["filename"])).stem, "envelope")
    short_hash = str(file_payload["hash"])[:12]
    target = folder / f"envelope_{envelope_id:06d}__{original_stem}__{short_hash}{file_payload['suffix']}"
    target.write_bytes(file_payload["payload"])  # type: ignore[arg-type]
    return target, folder


def _refresh_envelope_lot_totals(conn: sqlite3.Connection, lot_id: int, folder: Path | None = None) -> None:
    conn.execute(
        """
        UPDATE envelope_lotes
           SET total_envelopes = (
                    SELECT COUNT(*)
                      FROM envelopes
                     WHERE lote_id = envelope_lotes.id AND ativo = 1
               ),
               total_valor = (
                    SELECT COALESCE(SUM(total_informado), 0)
                      FROM envelopes
                     WHERE lote_id = envelope_lotes.id AND ativo = 1 AND status = 'lancado'
               ),
               caminho_pasta = COALESCE(?, caminho_pasta),
               atualizado_em = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (str(folder) if folder else None, lot_id),
    )


def _unique_envelope_lot_name(conn: sqlite3.Connection, organization_id: int, competence_order: int, base_name: str) -> str:
    name = normalize_query(base_name) or "Envelopes digitalizados"
    candidate = name
    suffix = 2
    while conn.execute(
        """
        SELECT 1
          FROM envelope_lotes
         WHERE organizacao_id = ? AND competencia_ordem = ? AND nome = ?
         LIMIT 1
        """,
        (organization_id, competence_order, candidate),
    ).fetchone():
        candidate = f"{name} ({suffix})"
        suffix += 1
    return candidate


def _create_envelope_batch_lot(
    conn: sqlite3.Connection,
    organization_id: int,
    competence: str,
    competence_order: int,
    lot_name: str,
    default_received_on: str,
    default_origin: str,
    default_type_id: int,
    default_campaign_id: int,
    default_form_id: int,
    notes: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO envelope_lotes (
            organizacao_id, competencia, competencia_ordem, nome, status, caminho_pasta, observacoes,
            data_padrao_recebimento, origem_operacional_padrao, tipo_contribuicao_id_padrao,
            campanha_id_padrao, forma_recebimento_id_padrao, atualizado_em
        ) VALUES (?, ?, ?, ?, 'aberto', NULL, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            organization_id,
            competence,
            competence_order,
            _unique_envelope_lot_name(conn, organization_id, competence_order, lot_name),
            normalize_query(notes),
            default_received_on,
            default_origin,
            default_type_id or None,
            default_campaign_id or None,
            default_form_id or None,
        ),
    )
    return moneyless_int(cursor.lastrowid)


def _local_path_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme == "file":
        text = unquote(parsed.path)
    else:
        text = unquote(text)
    try:
        parts = shlex.split(text)
        if len(parts) == 1:
            text = parts[0]
    except ValueError:
        text = text.strip("\"'")
    return text.strip().strip("\"'")


def _local_path_candidates(value: object) -> list[Path]:
    text = _local_path_text(value)
    if not text:
        return []
    variants = [text]
    unescaped = text.replace("\\ ", " ")
    if unescaped not in variants:
        variants.append(unescaped)
    candidates: list[Path] = []
    for variant in variants:
        for normalized in {variant, unicodedata.normalize("NFC", variant), unicodedata.normalize("NFD", variant)}:
            path = Path(normalized).expanduser()
            if path not in candidates:
                candidates.append(path)
    return candidates


def _resolve_local_path(value: object) -> Path:
    candidates = _local_path_candidates(value)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if candidates:
        return candidates[0]
    return Path("")


def _local_envelope_files(folder_value: object) -> list[Path]:
    if not _local_path_text(folder_value):
        return []
    folder = _resolve_local_path(folder_value)
    if not folder.exists() or not folder.is_dir():
        raise LegacyWriteError(f"A pasta local informada para envelopes nao foi encontrada: {folder}")
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in ENVELOPE_ALLOWED_EXTENSIONS and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda path: path.name.lower())


def create_envelope_image_lot(payload: Any, uploads: list[Any] | tuple[Any, ...] | None = None, actor: str = "") -> dict[str, object]:
    uploads = list(uploads or [])
    with connect_legacy_write() as conn:
        ensure_envelope_support(conn)
        organization_id = default_organization_id(conn)
        default_received_on = normalize_query(_form_value(payload, "data_padrao_recebimento")) or date.today().isoformat()
        competence, competence_order = _competence_from_payload(payload, default_received_on)
        lot_name = normalize_query(_form_value(payload, "nome_lote")) or f"Envelopes {competence}"
        default_origin = normalize_query(_form_value(payload, "origem_operacional")) or "Envelope digitalizado"
        default_type_id = moneyless_int(_form_value(payload, "tipo_contribuicao_id_padrao"))
        default_campaign_id = moneyless_int(_form_value(payload, "campanha_id_padrao"))
        default_form_id = moneyless_int(_form_value(payload, "forma_recebimento_id"))
        if not default_type_id:
            raise LegacyWriteError("Escolha o tipo principal do lote de envelopes.")
        _assert_valid_contribution_catalogs(conn, organization_id, default_type_id, default_form_id, default_campaign_id)
        file_payloads = [_file_payload_from_upload(upload) for upload in uploads]
        file_payloads.extend(_file_payload_from_path(path) for path in _local_envelope_files(_form_value(payload, "pasta_origem")))
        file_payloads = sorted(file_payloads, key=lambda item: str(item["filename"]).lower())
        if not file_payloads:
            raise LegacyWriteError("Selecione arquivos ou informe uma pasta local com imagens/PDFs de envelopes.")
        created_ids: list[int] = []
        duplicate_ids: list[int] = []
        try:
            with conn:
                lot_id = _create_envelope_batch_lot(
                    conn,
                    organization_id,
                    competence,
                    competence_order,
                    lot_name,
                    default_received_on,
                    default_origin,
                    default_type_id,
                    default_campaign_id,
                    default_form_id,
                    _form_value(payload, "observacoes"),
                )
                folder: Path | None = None
                for index, file_payload in enumerate(file_payloads, start=1):
                    duplicate = conn.execute(
                        """
                        SELECT id
                          FROM envelopes
                         WHERE organizacao_id = ? AND ativo = 1 AND imagem_hash = ?
                         ORDER BY id
                         LIMIT 1
                        """,
                        (organization_id, file_payload["hash"]),
                    ).fetchone()
                    status = ENVELOPE_DUPLICATE_STATUS if duplicate else ENVELOPE_PENDING_STATUS
                    note = (
                        f"Imagem duplicada do envelope #{duplicate['id']}; revisar antes de qualquer lancamento."
                        if duplicate
                        else "Envelope aguardando digitacao manual."
                    )
                    cursor = conn.execute(
                        """
                        INSERT INTO envelopes (
                            lote_id, organizacao_id, competencia, competencia_ordem, data_recebimento,
                            total_informado, total_linhas, forma_recebimento_id, origem_operacional,
                            nome_arquivo_original, imagem_hash, imagem_content_type, imagem_tamanho,
                            status, observacoes, justificativa, ocr_json, ordem_lote, ativo, atualizado_em
                        ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            lot_id,
                            organization_id,
                            competence,
                            competence_order,
                            default_received_on,
                            default_form_id or None,
                            default_origin,
                            file_payload["filename"],
                            file_payload["hash"],
                            file_payload["content_type"],
                            file_payload["size"],
                            status,
                            note,
                            index,
                        ),
                    )
                    envelope_id = moneyless_int(cursor.lastrowid)
                    stored_path, folder = _store_envelope_file(lot_id, envelope_id, competence, competence_order, file_payload)
                    conn.execute(
                        "UPDATE envelopes SET caminho_imagem = ?, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                        (str(stored_path), envelope_id),
                    )
                    created_ids.append(envelope_id)
                    if duplicate:
                        duplicate_ids.append(envelope_id)
                    write_audit_log(
                        conn,
                        organization_id,
                        "criar_envelope_pendente_lote_django",
                        "envelopes",
                        envelope_id,
                        None,
                        {
                            "lote_id": lot_id,
                            "status": status,
                            "arquivo": file_payload["filename"],
                            "imagem_hash": file_payload["hash"],
                            "duplicado_de": moneyless_int(duplicate["id"]) if duplicate else None,
                        },
                        actor=actor,
                    )
                _refresh_envelope_lot_totals(conn, lot_id, folder)
                write_audit_log(
                    conn,
                    organization_id,
                    "criar_lote_envelopes_django",
                    "envelope_lotes",
                    lot_id,
                    None,
                    {
                        "competencia": competence,
                        "nome": lot_name,
                        "total_arquivos": len(file_payloads),
                        "duplicados": len(duplicate_ids),
                    },
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel criar o lote de envelopes: {exc}") from exc
    return {"lot_id": lot_id, "envelope_ids": created_ids, "duplicates": duplicate_ids}


def _digits_only(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _profile_compare_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_match_name(value))


def _address_text(address: sqlite3.Row | None) -> str:
    if address is None:
        return ""
    return " ".join(
        normalize_query(address[key])
        for key in ["logradouro", "numero", "complemento", "bairro", "cidade", "uf", "cep"]
        if key in address.keys() and normalize_query(address[key])
    )


def _phone_matches(envelope_digits: str, current_digits: set[str]) -> bool:
    if not envelope_digits:
        return True
    for candidate in current_digits:
        if not candidate:
            continue
        if envelope_digits == candidate:
            return True
        if len(envelope_digits) >= 8 and len(candidate) >= 8 and envelope_digits[-8:] == candidate[-8:]:
            return True
    return False


def _insert_envelope_profile_update(
    conn: sqlite3.Connection,
    organization_id: int,
    envelope_id: int,
    person_id: int,
    field: str,
    current_value: str,
    envelope_value: str,
    actor: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO envelope_atualizacoes_cadastrais (
            envelope_id, organizacao_id, pessoa_id, campo, valor_cadastro, valor_envelope, status, observacoes
        ) VALUES (?, ?, ?, ?, ?, ?, 'pendente', ?)
        """,
        (
            envelope_id,
            organization_id,
            person_id,
            field,
            normalize_query(current_value),
            normalize_query(envelope_value),
            "Sugerido automaticamente porque o envelope contem dado diferente da ficha.",
        ),
    )
    suggestion_id = moneyless_int(cursor.lastrowid)
    if suggestion_id:
        write_audit_log(
            conn,
            organization_id,
            "sugerir_atualizacao_cadastral_por_envelope_django",
            "envelope_atualizacoes_cadastrais",
            suggestion_id,
            None,
            {
                "envelope_id": envelope_id,
                "pessoa_id": person_id,
                "campo": field,
                "valor_cadastro": normalize_query(current_value),
                "valor_envelope": normalize_query(envelope_value),
                "status": "pendente",
            },
            actor=actor,
        )
    return suggestion_id


def _suggest_profile_updates_from_envelope(
    conn: sqlite3.Connection,
    organization_id: int,
    envelope_id: int,
    person_id: int,
    phone_value: str,
    address_value: str,
    actor: str,
) -> list[int]:
    if not person_id:
        return []
    person = get_person(conn, person_id)
    if person is None:
        return []
    created: list[int] = []
    envelope_phone = _digits_only(phone_value)
    current_phones = {
        _digits_only(person[key])
        for key in ["telefone_principal", "whatsapp_principal"]
        if key in person.keys() and _digits_only(person[key])
    }
    contact_rows = conn.execute(
        """
        SELECT valor
          FROM pessoa_contatos
         WHERE pessoa_id = ?
           AND tipo IN ('telefone', 'celular', 'whatsapp')
           AND COALESCE(valor, '') <> ''
        """,
        (person_id,),
    ).fetchall()
    current_phones.update(_digits_only(row["valor"]) for row in contact_rows if _digits_only(row["valor"]))
    if envelope_phone and not _phone_matches(envelope_phone, current_phones):
        created.append(
            _insert_envelope_profile_update(
                conn,
                organization_id,
                envelope_id,
                person_id,
                "telefone",
                ", ".join(sorted(current_phones)) if current_phones else "",
                phone_value,
                actor,
            )
        )

    envelope_address_key = _profile_compare_key(address_value)
    current_address = _address_text(primary_address(conn, person_id))
    current_address_key = _profile_compare_key(current_address)
    if envelope_address_key and not (
        current_address_key
        and (envelope_address_key in current_address_key or current_address_key in envelope_address_key)
    ):
        created.append(
            _insert_envelope_profile_update(
                conn,
                organization_id,
                envelope_id,
                person_id,
                "endereco",
                current_address,
                address_value,
                actor,
            )
        )
    return [item for item in created if item]


def create_envelope_contribution_batch(payload: Any, upload: Any, actor: str = "") -> dict[str, object]:
    file_payload = _uploaded_file_payload(upload, _form_value(payload, "imagem_envelope_path"))
    with connect_legacy_write() as conn:
        ensure_envelope_support(conn)
        organization_id = default_organization_id(conn)
        received_on = normalize_query(_form_value(payload, "data_recebimento"))
        if not received_on:
            raise LegacyWriteError("Informe a data do envelope.")
        competence, competence_order = _competence_from_payload(payload, received_on)
        form_id = moneyless_int(_form_value(payload, "forma_recebimento_id")) or None
        status = normalize_query(_form_value(payload, "status_operacional", "regular")) or "regular"
        if status not in CONTRIBUTION_STATUS_OPTIONS:
            raise LegacyWriteError("Status operacional invalido para envelope.")
        justification = normalize_query(_form_value(payload, "justificativa"))
        if len(justification) < 8:
            raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
        header_notes = normalize_query(_form_value(payload, "observacoes"))
        source_label = normalize_query(_form_value(payload, "origem_operacional")) or "Envelope manual com imagem arquivada"
        expected_total = round(float(parse_money(_form_value(payload, "valor_total"))), 2)
        if expected_total <= 0:
            raise LegacyWriteError("Informe o total do envelope.")
        traceability = _envelope_traceability_payload(payload, conn=conn, form_id=moneyless_int(form_id), expected_total=expected_total)
        main_person_id = moneyless_int(_form_value(payload, "pessoa_id")) or _reference_id(_form_value(payload, "pessoa_ref")) or None
        if main_person_id and get_person(conn, main_person_id) is None:
            raise LegacyWriteError("Pessoa principal do envelope invalida.")
        main_contributor_id = (
            moneyless_int(_form_value(payload, "contribuinte_id"))
            or _reference_id(_form_value(payload, "contribuinte_ref"))
            or None
        )
        if main_contributor_id:
            main_contributor_id = _validated_contributor(conn, organization_id, main_contributor_id) or None
        lines = _envelope_line_payloads(
            payload,
            organization_id,
            conn,
            expected_total,
            moneyless_int(main_person_id),
            moneyless_int(main_contributor_id),
        )
        total = round(sum(float(line["valor"]) for line in lines), 2)
        if abs(total - expected_total) > 0.009:
            raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total do envelope ({expected_total:.2f}).")
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(lines[0]["tipo_contribuicao_id"]),
            moneyless_int(form_id),
            moneyless_int(lines[0]["campanha_id"]),
        )
        lot_name = normalize_query(_form_value(payload, "nome_lote"))
        created_ids: list[int] = []
        item_ids: list[int] = []
        try:
            with conn:
                lot_id = _envelope_lot_id(conn, organization_id, competence, competence_order, lot_name)
                cursor = conn.execute(
                    """
                    INSERT INTO envelopes (
                        lote_id, organizacao_id, competencia, competencia_ordem, data_recebimento,
                        total_informado, total_linhas, nome_informado, telefone_informado, endereco_informado,
                        pessoa_id, contribuinte_id, forma_recebimento_id, origem_operacional,
                        nome_arquivo_original, imagem_hash, imagem_content_type, imagem_tamanho,
                        rastreio_forma_identificada, rastreio_banco_operadora, rastreio_numero_cheque,
                        rastreio_numero_operacao, rastreio_nsu_tid, rastreio_ultimos_digitos_cartao,
                        rastreio_data_operacao, rastreio_valor_operacao, rastreio_status_conciliacao,
                        rastreio_observacoes, status, observacoes, justificativa, ocr_json, ativo, atualizado_em
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lancado', ?, ?, NULL, 1, CURRENT_TIMESTAMP)
                    """,
                    (
                        lot_id,
                        organization_id,
                        competence,
                        competence_order,
                        received_on,
                        expected_total,
                        total,
                        normalize_query(_form_value(payload, "nome_informado")),
                        normalize_query(_form_value(payload, "telefone_informado")),
                        normalize_query(_form_value(payload, "endereco_informado")),
                        main_person_id,
                        main_contributor_id,
                        form_id,
                        source_label,
                        file_payload["filename"],
                        file_payload["hash"],
                        file_payload["content_type"],
                        file_payload["size"],
                        traceability["rastreio_forma_identificada"],
                        traceability["rastreio_banco_operadora"],
                        traceability["rastreio_numero_cheque"],
                        traceability["rastreio_numero_operacao"],
                        traceability["rastreio_nsu_tid"],
                        traceability["rastreio_ultimos_digitos_cartao"],
                        traceability["rastreio_data_operacao"],
                        traceability["rastreio_valor_operacao"],
                        traceability["rastreio_status_conciliacao"],
                        traceability["rastreio_observacoes"],
                        header_notes,
                        justification,
                    ),
                )
                envelope_id = moneyless_int(cursor.lastrowid)
                stored_path, folder = _store_envelope_file(lot_id, envelope_id, competence, competence_order, file_payload)
                conn.execute(
                    "UPDATE envelopes SET caminho_imagem = ?, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                    (str(stored_path), envelope_id),
                )
                update_suggestion_ids = _suggest_profile_updates_from_envelope(
                    conn,
                    organization_id,
                    envelope_id,
                    moneyless_int(main_person_id),
                    _form_value(payload, "telefone_informado"),
                    _form_value(payload, "endereco_informado"),
                    actor,
                )
                traceability_note = _traceability_note(traceability)
                origin_note = "\n".join(
                    item
                    for item in [
                        f"Envelope #{envelope_id} / lote #{lot_id}; imagem arquivada em {stored_path.name}.",
                        traceability_note,
                    ]
                    if item
                )
                for line in lines:
                    contribution_cursor = conn.execute(
                        """
                        INSERT INTO contribuicoes (
                            organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, data_recebimento, competencia, competencia_ordem,
                            valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                            ativo, atualizado_em
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            received_on,
                            competence,
                            competence_order,
                            line["valor"],
                            form_id,
                            _line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                            status,
                        ),
                    )
                    contribution_id = moneyless_int(contribution_cursor.lastrowid)
                    created_ids.append(contribution_id)
                    item_cursor = conn.execute(
                        """
                        INSERT INTO envelope_itens (
                            envelope_id, organizacao_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, valor, observacoes, contribuicao_id, ativo, atualizado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            envelope_id,
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            line["valor"],
                            line["observacoes"],
                            contribution_id,
                        ),
                    )
                    item_ids.append(moneyless_int(item_cursor.lastrowid))
                    after = dict(get_contribution(conn, contribution_id) or {})
                    after["justificativa_operador"] = justification
                    after["envelope_id"] = envelope_id
                    after["envelope_lote_id"] = lot_id
                    write_audit_log(
                        conn,
                        organization_id,
                        "lancar_contribuicao_por_envelope_django",
                        "contribuicoes",
                        contribution_id,
                        None,
                        after,
                        actor=actor,
                    )
                _refresh_envelope_lot_totals(conn, lot_id, folder)
                envelope_after = {
                    "id": envelope_id,
                    "lote_id": lot_id,
                    "competencia": competence,
                    "total_informado": expected_total,
                    "total_linhas": total,
                    "contribuicoes": created_ids,
                    "itens": item_ids,
                    "atualizacoes_cadastrais": update_suggestion_ids,
                    "caminho_imagem": str(stored_path),
                    "imagem_hash": file_payload["hash"],
                    "rastreabilidade_financeira": traceability,
                }
                write_audit_log(
                    conn,
                    organization_id,
                    "registrar_envelope_manual_django",
                    "envelopes",
                    envelope_id,
                    None,
                    envelope_after,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel registrar o envelope: {exc}") from exc
    return {"envelope_id": envelope_id, "lot_id": lot_id, "contribution_ids": created_ids}


def launch_pending_envelope(envelope_id: int, payload: Any, actor: str = "") -> dict[str, object]:
    with connect_legacy_write() as conn:
        ensure_envelope_support(conn)
        envelope = conn.execute(
            """
            SELECT e.*, l.data_padrao_recebimento, l.origem_operacional_padrao,
                   l.tipo_contribuicao_id_padrao, l.campanha_id_padrao, l.forma_recebimento_id_padrao
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
             WHERE e.id = ? AND e.ativo = 1
             LIMIT 1
            """,
            (envelope_id,),
        ).fetchone()
        if envelope is None:
            raise LegacyWriteError("Envelope pendente nao encontrado.")
        if envelope["status"] == ENVELOPE_LAUNCHED_STATUS:
            raise LegacyWriteError("Este envelope ja foi lancado.")
        if envelope["status"] in {ENVELOPE_IGNORED_STATUS, ENVELOPE_DUPLICATE_STATUS}:
            raise LegacyWriteError("Este envelope nao esta disponivel para lancamento.")
        organization_id = moneyless_int(envelope["organizacao_id"])
        lot_id = moneyless_int(envelope["lote_id"])
        received_on = normalize_query(_form_value(payload, "data_recebimento")) or normalize_query(envelope["data_recebimento"])
        if not received_on:
            raise LegacyWriteError("Informe a data do envelope.")
        competence, competence_order = _competence_from_payload(payload, received_on)
        if not normalize_query(_form_value(payload, "competencia_mes")):
            competence = envelope["competencia"] or competence
            competence_order = moneyless_int(envelope["competencia_ordem"]) or competence_order
        form_id = moneyless_int(_form_value(payload, "forma_recebimento_id")) or moneyless_int(envelope["forma_recebimento_id"]) or moneyless_int(envelope["forma_recebimento_id_padrao"]) or None
        status = normalize_query(_form_value(payload, "status_operacional", "regular")) or "regular"
        if status not in CONTRIBUTION_STATUS_OPTIONS:
            raise LegacyWriteError("Status operacional invalido para envelope.")
        justification = normalize_query(_form_value(payload, "justificativa"))
        if len(justification) < 8:
            raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
        header_notes = normalize_query(_form_value(payload, "observacoes"))
        source_label = normalize_query(_form_value(payload, "origem_operacional")) or normalize_query(envelope["origem_operacional"]) or "Envelope digitalizado"
        expected_total = round(float(parse_money(_form_value(payload, "valor_total"))), 2)
        if expected_total <= 0:
            raise LegacyWriteError("Informe o total do envelope.")
        traceability = _envelope_traceability_payload(payload, conn=conn, form_id=moneyless_int(form_id), expected_total=expected_total)
        main_person_id = moneyless_int(_form_value(payload, "pessoa_id")) or _reference_id(_form_value(payload, "pessoa_ref")) or None
        if main_person_id and get_person(conn, main_person_id) is None:
            raise LegacyWriteError("Pessoa principal do envelope invalida.")
        main_contributor_id = (
            moneyless_int(_form_value(payload, "contribuinte_id"))
            or _reference_id(_form_value(payload, "contribuinte_ref"))
            or None
        )
        if main_contributor_id:
            main_contributor_id = _validated_contributor(conn, organization_id, main_contributor_id) or None
        lines = _envelope_line_payloads(
            payload,
            organization_id,
            conn,
            expected_total,
            moneyless_int(main_person_id),
            moneyless_int(main_contributor_id),
        )
        total = round(sum(float(line["valor"]) for line in lines), 2)
        if abs(total - expected_total) > 0.009:
            raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total do envelope ({expected_total:.2f}).")
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(lines[0]["tipo_contribuicao_id"]),
            moneyless_int(form_id),
            moneyless_int(lines[0]["campanha_id"]),
        )
        created_ids: list[int] = []
        item_ids: list[int] = []
        try:
            with conn:
                before = dict(envelope)
                conn.execute(
                    """
                    UPDATE envelopes
                       SET competencia = ?, competencia_ordem = ?, data_recebimento = ?,
                           total_informado = ?, total_linhas = ?, nome_informado = ?,
                           telefone_informado = ?, endereco_informado = ?, pessoa_id = ?,
                           contribuinte_id = ?, forma_recebimento_id = ?, origem_operacional = ?,
                           rastreio_forma_identificada = ?, rastreio_banco_operadora = ?,
                           rastreio_numero_cheque = ?, rastreio_numero_operacao = ?,
                           rastreio_nsu_tid = ?, rastreio_ultimos_digitos_cartao = ?,
                           rastreio_data_operacao = ?, rastreio_valor_operacao = ?,
                           rastreio_status_conciliacao = ?, rastreio_observacoes = ?,
                           status = 'lancado', observacoes = ?, justificativa = ?,
                           atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (
                        competence,
                        competence_order,
                        received_on,
                        expected_total,
                        total,
                        normalize_query(_form_value(payload, "nome_informado")),
                        normalize_query(_form_value(payload, "telefone_informado")),
                        normalize_query(_form_value(payload, "endereco_informado")),
                        main_person_id,
                        main_contributor_id,
                        form_id,
                        source_label,
                        traceability["rastreio_forma_identificada"],
                        traceability["rastreio_banco_operadora"],
                        traceability["rastreio_numero_cheque"],
                        traceability["rastreio_numero_operacao"],
                        traceability["rastreio_nsu_tid"],
                        traceability["rastreio_ultimos_digitos_cartao"],
                        traceability["rastreio_data_operacao"],
                        traceability["rastreio_valor_operacao"],
                        traceability["rastreio_status_conciliacao"],
                        traceability["rastreio_observacoes"],
                        header_notes,
                        justification,
                        envelope_id,
                    ),
                )
                update_suggestion_ids = _suggest_profile_updates_from_envelope(
                    conn,
                    organization_id,
                    envelope_id,
                    moneyless_int(main_person_id),
                    _form_value(payload, "telefone_informado"),
                    _form_value(payload, "endereco_informado"),
                    actor,
                )
                traceability_note = _traceability_note(traceability)
                origin_note = "\n".join(
                    item
                    for item in [
                        f"Envelope #{envelope_id} / lote #{lot_id}; imagem arquivada em {Path(str(envelope['caminho_imagem'] or '')).name}.",
                        traceability_note,
                    ]
                    if item
                )
                for line in lines:
                    contribution_cursor = conn.execute(
                        """
                        INSERT INTO contribuicoes (
                            organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, data_recebimento, competencia, competencia_ordem,
                            valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                            ativo, atualizado_em
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            received_on,
                            competence,
                            competence_order,
                            line["valor"],
                            form_id,
                            _line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                            status,
                        ),
                    )
                    contribution_id = moneyless_int(contribution_cursor.lastrowid)
                    created_ids.append(contribution_id)
                    item_cursor = conn.execute(
                        """
                        INSERT INTO envelope_itens (
                            envelope_id, organizacao_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, valor, observacoes, contribuicao_id, ativo, atualizado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            envelope_id,
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            line["valor"],
                            line["observacoes"],
                            contribution_id,
                        ),
                    )
                    item_ids.append(moneyless_int(item_cursor.lastrowid))
                    after = dict(get_contribution(conn, contribution_id) or {})
                    after["justificativa_operador"] = justification
                    after["envelope_id"] = envelope_id
                    after["envelope_lote_id"] = lot_id
                    write_audit_log(
                        conn,
                        organization_id,
                        "lancar_contribuicao_por_envelope_django",
                        "contribuicoes",
                        contribution_id,
                        None,
                        after,
                        actor=actor,
                    )
                _refresh_envelope_lot_totals(conn, lot_id, Path(str(envelope["caminho_imagem"] or "")).parent)
                after_envelope = dict(conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone() or {})
                after_envelope["contribuicoes"] = created_ids
                after_envelope["itens"] = item_ids
                after_envelope["atualizacoes_cadastrais"] = update_suggestion_ids
                after_envelope["rastreabilidade_financeira"] = traceability
                write_audit_log(
                    conn,
                    organization_id,
                    "lancar_envelope_pendente_django",
                    "envelopes",
                    envelope_id,
                    before,
                    after_envelope,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel lancar o envelope pendente: {exc}") from exc
    return {"envelope_id": envelope_id, "lot_id": lot_id, "contribution_ids": created_ids}


def update_launched_envelope(envelope_id: int, payload: Any, actor: str = "") -> dict[str, object]:
    with connect_legacy_write() as conn:
        ensure_envelope_support(conn)
        envelope = conn.execute(
            """
            SELECT e.*, l.data_padrao_recebimento, l.origem_operacional_padrao,
                   l.tipo_contribuicao_id_padrao, l.campanha_id_padrao, l.forma_recebimento_id_padrao
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
             WHERE e.id = ? AND e.ativo = 1
             LIMIT 1
            """,
            (envelope_id,),
        ).fetchone()
        if envelope is None:
            raise LegacyWriteError("Envelope nao encontrado.")
        if envelope["status"] != ENVELOPE_LAUNCHED_STATUS:
            raise LegacyWriteError("Somente envelopes ja lancados podem ser corrigidos por esta tela.")
        organization_id = moneyless_int(envelope["organizacao_id"])
        lot_id = moneyless_int(envelope["lote_id"])
        received_on = normalize_query(_form_value(payload, "data_recebimento")) or normalize_query(envelope["data_recebimento"])
        if not received_on:
            raise LegacyWriteError("Informe a data do envelope.")
        competence, competence_order = _competence_from_payload(payload, received_on)
        form_id = moneyless_int(_form_value(payload, "forma_recebimento_id")) or moneyless_int(envelope["forma_recebimento_id"]) or moneyless_int(envelope["forma_recebimento_id_padrao"]) or None
        status = normalize_query(_form_value(payload, "status_operacional", "regular")) or "regular"
        if status not in CONTRIBUTION_STATUS_OPTIONS:
            raise LegacyWriteError("Status operacional invalido para envelope.")
        justification = normalize_query(_form_value(payload, "justificativa"))
        if len(justification) < 8:
            raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
        header_notes = normalize_query(_form_value(payload, "observacoes"))
        source_label = normalize_query(_form_value(payload, "origem_operacional")) or normalize_query(envelope["origem_operacional"]) or "Envelope digitalizado"
        expected_total = round(float(parse_money(_form_value(payload, "valor_total"))), 2)
        if expected_total <= 0:
            raise LegacyWriteError("Informe o total do envelope.")
        traceability = _envelope_traceability_payload(payload, conn=conn, form_id=moneyless_int(form_id), expected_total=expected_total)
        main_person_id = moneyless_int(_form_value(payload, "pessoa_id")) or _reference_id(_form_value(payload, "pessoa_ref")) or None
        if main_person_id and get_person(conn, main_person_id) is None:
            raise LegacyWriteError("Pessoa principal do envelope invalida.")
        main_contributor_id = (
            moneyless_int(_form_value(payload, "contribuinte_id"))
            or _reference_id(_form_value(payload, "contribuinte_ref"))
            or None
        )
        if main_contributor_id:
            main_contributor_id = _validated_contributor(conn, organization_id, main_contributor_id) or None
        lines = _envelope_line_payloads(
            payload,
            organization_id,
            conn,
            expected_total,
            moneyless_int(main_person_id),
            moneyless_int(main_contributor_id),
        )
        total = round(sum(float(line["valor"]) for line in lines), 2)
        if abs(total - expected_total) > 0.009:
            raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total do envelope ({expected_total:.2f}).")
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(lines[0]["tipo_contribuicao_id"]),
            moneyless_int(form_id),
            moneyless_int(lines[0]["campanha_id"]),
        )
        old_item_rows = conn.execute(
            "SELECT * FROM envelope_itens WHERE envelope_id = ? AND ativo = 1 ORDER BY id",
            (envelope_id,),
        ).fetchall()
        old_contribution_ids = [
            moneyless_int(row["contribuicao_id"])
            for row in old_item_rows
            if moneyless_int(row["contribuicao_id"])
        ]
        before = dict(envelope)
        before["itens_ativos"] = [dict(row) for row in old_item_rows]
        before["contribuicoes_ativas"] = [
            dict(get_contribution(conn, contribution_id) or {})
            for contribution_id in old_contribution_ids
        ]
        created_ids: list[int] = []
        item_ids: list[int] = []
        try:
            with conn:
                for contribution_id in old_contribution_ids:
                    current = get_contribution(conn, contribution_id)
                    conn.execute(
                        """
                        UPDATE contribuicoes
                           SET ativo = 0,
                               observacoes = TRIM(COALESCE(observacoes, '') || CHAR(10) || ?),
                               atualizado_em = CURRENT_TIMESTAMP
                         WHERE id = ?
                        """,
                        (f"Substituida por correcao auditada do envelope #{envelope_id}.", contribution_id),
                    )
                    after_inactive = dict(get_contribution(conn, contribution_id) or {})
                    write_audit_log(
                        conn,
                        organization_id,
                        "desativar_contribuicao_por_correcao_envelope_django",
                        "contribuicoes",
                        contribution_id,
                        dict(current) if current else None,
                        after_inactive,
                        actor=actor,
                    )
                conn.execute(
                    """
                    UPDATE envelope_itens
                       SET ativo = 0, atualizado_em = CURRENT_TIMESTAMP
                     WHERE envelope_id = ? AND ativo = 1
                    """,
                    (envelope_id,),
                )
                conn.execute(
                    """
                    UPDATE envelopes
                       SET competencia = ?, competencia_ordem = ?, data_recebimento = ?,
                           total_informado = ?, total_linhas = ?, nome_informado = ?,
                           telefone_informado = ?, endereco_informado = ?, pessoa_id = ?,
                           contribuinte_id = ?, forma_recebimento_id = ?, origem_operacional = ?,
                           rastreio_forma_identificada = ?, rastreio_banco_operadora = ?,
                           rastreio_numero_cheque = ?, rastreio_numero_operacao = ?,
                           rastreio_nsu_tid = ?, rastreio_ultimos_digitos_cartao = ?,
                           rastreio_data_operacao = ?, rastreio_valor_operacao = ?,
                           rastreio_status_conciliacao = ?, rastreio_observacoes = ?,
                           status = 'lancado', observacoes = ?, justificativa = ?,
                           atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (
                        competence,
                        competence_order,
                        received_on,
                        expected_total,
                        total,
                        normalize_query(_form_value(payload, "nome_informado")),
                        normalize_query(_form_value(payload, "telefone_informado")),
                        normalize_query(_form_value(payload, "endereco_informado")),
                        main_person_id,
                        main_contributor_id,
                        form_id,
                        source_label,
                        traceability["rastreio_forma_identificada"],
                        traceability["rastreio_banco_operadora"],
                        traceability["rastreio_numero_cheque"],
                        traceability["rastreio_numero_operacao"],
                        traceability["rastreio_nsu_tid"],
                        traceability["rastreio_ultimos_digitos_cartao"],
                        traceability["rastreio_data_operacao"],
                        traceability["rastreio_valor_operacao"],
                        traceability["rastreio_status_conciliacao"],
                        traceability["rastreio_observacoes"],
                        header_notes,
                        justification,
                        envelope_id,
                    ),
                )
                update_suggestion_ids = _suggest_profile_updates_from_envelope(
                    conn,
                    organization_id,
                    envelope_id,
                    moneyless_int(main_person_id),
                    _form_value(payload, "telefone_informado"),
                    _form_value(payload, "endereco_informado"),
                    actor,
                )
                traceability_note = _traceability_note(traceability)
                origin_note = "\n".join(
                    item
                    for item in [
                        f"Envelope #{envelope_id} / lote #{lot_id}; correcao manual auditada; imagem preservada em {Path(str(envelope['caminho_imagem'] or '')).name}.",
                        traceability_note,
                    ]
                    if item
                )
                for line in lines:
                    contribution_cursor = conn.execute(
                        """
                        INSERT INTO contribuicoes (
                            organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, data_recebimento, competencia, competencia_ordem,
                            valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                            ativo, atualizado_em
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            received_on,
                            competence,
                            competence_order,
                            line["valor"],
                            form_id,
                            _line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                            status,
                        ),
                    )
                    contribution_id = moneyless_int(contribution_cursor.lastrowid)
                    created_ids.append(contribution_id)
                    item_cursor = conn.execute(
                        """
                        INSERT INTO envelope_itens (
                            envelope_id, organizacao_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, valor, observacoes, contribuicao_id, ativo, atualizado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            envelope_id,
                            organization_id,
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            line["valor"],
                            line["observacoes"],
                            contribution_id,
                        ),
                    )
                    item_ids.append(moneyless_int(item_cursor.lastrowid))
                    after_contribution = dict(get_contribution(conn, contribution_id) or {})
                    after_contribution["justificativa_operador"] = justification
                    after_contribution["envelope_id"] = envelope_id
                    after_contribution["envelope_lote_id"] = lot_id
                    after_contribution["substitui_contribuicoes"] = old_contribution_ids
                    write_audit_log(
                        conn,
                        organization_id,
                        "recriar_contribuicao_por_correcao_envelope_django",
                        "contribuicoes",
                        contribution_id,
                        None,
                        after_contribution,
                        actor=actor,
                    )
                _refresh_envelope_lot_totals(conn, lot_id, Path(str(envelope["caminho_imagem"] or "")).parent)
                after_envelope = dict(conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone() or {})
                after_envelope["contribuicoes_novas"] = created_ids
                after_envelope["itens_novos"] = item_ids
                after_envelope["contribuicoes_desativadas"] = old_contribution_ids
                after_envelope["atualizacoes_cadastrais"] = update_suggestion_ids
                after_envelope["rastreabilidade_financeira"] = traceability
                write_audit_log(
                    conn,
                    organization_id,
                    "corrigir_envelope_lancado_django",
                    "envelopes",
                    envelope_id,
                    before,
                    after_envelope,
                    actor=actor,
                )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel corrigir o envelope lancado: {exc}") from exc
    return {
        "envelope_id": envelope_id,
        "lot_id": lot_id,
        "contribution_ids": created_ids,
        "deactivated_contribution_ids": old_contribution_ids,
    }


def ignore_pending_envelope(envelope_id: int, reason: str, actor: str = "") -> int:
    reason = normalize_query(reason)
    if len(reason) < 8:
        raise LegacyWriteError("Informe uma justificativa para ignorar o envelope.")
    with connect_legacy_write() as conn:
        ensure_envelope_support(conn)
        envelope = conn.execute("SELECT * FROM envelopes WHERE id = ? AND ativo = 1", (envelope_id,)).fetchone()
        if envelope is None:
            raise LegacyWriteError("Envelope nao encontrado.")
        if envelope["status"] == ENVELOPE_LAUNCHED_STATUS:
            raise LegacyWriteError("Envelope ja lancado nao pode ser ignorado.")
        before = dict(envelope)
        organization_id = moneyless_int(envelope["organizacao_id"])
        with conn:
            conn.execute(
                """
                UPDATE envelopes
                   SET status = 'ignorado', observacoes = ?, justificativa = ?, atualizado_em = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (f"Ignorado pelo operador: {reason}", reason, envelope_id),
            )
            _refresh_envelope_lot_totals(conn, moneyless_int(envelope["lote_id"]), Path(str(envelope["caminho_imagem"] or "")).parent)
            after = dict(conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone() or {})
            write_audit_log(
                conn,
                organization_id,
                "ignorar_envelope_pendente_django",
                "envelopes",
                envelope_id,
                before,
                after,
                actor=actor,
            )
    return envelope_id


def split_contribution(contribution_id: int, payload: Any, actor: str = "") -> list[int]:
    with connect_legacy_write() as conn:
        current = get_contribution(conn, contribution_id)
        if current is None or not moneyless_int(current["ativo"]):
            raise LegacyWriteError("Contribuicao original nao encontrada.")
        organization_id = moneyless_int(current["organizacao_id"])
        justification = normalize_query(_form_value(payload, "justificativa"))
        if len(justification) < 8:
            raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para ratear a contribuicao.")
        lines = _manual_line_payloads(payload, organization_id, conn)
        original_total = round(float(current["valor"] or 0), 2)
        split_total = round(sum(float(line["valor"]) for line in lines), 2)
        if abs(split_total - original_total) > 0.009:
            raise LegacyWriteError(f"A soma do rateio ({split_total:.2f}) deve fechar com o valor original ({original_total:.2f}).")
        received_on = normalize_query(_form_value(payload, "data_recebimento")) or normalize_query(current["data_recebimento"])
        competence, competence_order = competencia_from_date(received_on)
        form_id = moneyless_int(_form_value(payload, "forma_recebimento_id")) or moneyless_int(current["forma_recebimento_id"]) or None
        status = normalize_query(_form_value(payload, "status_operacional", current["status_operacional"] or "regular")) or "regular"
        if status not in CONTRIBUTION_STATUS_OPTIONS:
            raise LegacyWriteError("Status operacional invalido para rateio.")
        _assert_valid_contribution_catalogs(
            conn,
            organization_id,
            moneyless_int(lines[0]["tipo_contribuicao_id"]),
            moneyless_int(form_id),
            0,
        )
        header_notes = normalize_query(_form_value(payload, "observacoes")) or normalize_query(current["observacoes"])
        origin_note = f"Rateio manual da contribuicao original #{contribution_id}; comprovante/envelope/e-mail conferido pelo operador."
        before = dict(current)
        created_ids: list[int] = [contribution_id]
        try:
            with conn:
                first = lines[0]
                conn.execute(
                    """
                    UPDATE contribuicoes
                       SET pessoa_id = ?, contribuinte_id = ?, tipo_contribuicao_id = ?, campanha_id = ?,
                           data_recebimento = ?, competencia = ?, competencia_ordem = ?,
                           valor = ?, forma_recebimento_id = ?, observacoes = ?, status_operacional = ?,
                           atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (
                        first["pessoa_id"],
                        first["contribuinte_id"],
                        first["tipo_contribuicao_id"],
                        first["campanha_id"],
                        received_on,
                        competence,
                        competence_order,
                        first["valor"],
                        form_id,
                        _line_observations(header_notes, str(first["observacoes"] or ""), origin_note),
                        status,
                        contribution_id,
                    ),
                )
                after = dict(get_contribution(conn, contribution_id) or {})
                after["justificativa_operador"] = justification
                after["rateio_manual_total_original"] = original_total
                after["rateio_manual_linhas"] = len(lines)
                write_audit_log(
                    conn,
                    organization_id,
                    "ratear_contribuicao_manual_django",
                    "contribuicoes",
                    contribution_id,
                    before,
                    after,
                    actor=actor,
                )
                for line in lines[1:]:
                    cursor = conn.execute(
                        """
                        INSERT INTO contribuicoes (
                            organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                            campanha_id, data_recebimento, competencia, competencia_ordem,
                            valor, forma_recebimento_id, conta_financeira_id, observacoes, import_lote_id,
                            pix_movimento_id, extrato_movimento_id, status_operacional, ativo, atualizado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """,
                        (
                            organization_id,
                            current["unidade_id"],
                            line["pessoa_id"],
                            line["contribuinte_id"],
                            line["tipo_contribuicao_id"],
                            line["campanha_id"],
                            received_on,
                            competence,
                            competence_order,
                            line["valor"],
                            form_id,
                            _row_value(current, "conta_financeira_id"),
                            _line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                            _row_value(current, "import_lote_id"),
                            _row_value(current, "pix_movimento_id"),
                            _row_value(current, "extrato_movimento_id"),
                            status,
                        ),
                    )
                    new_id = moneyless_int(cursor.lastrowid)
                    created_ids.append(new_id)
                    new_after = dict(get_contribution(conn, new_id) or {})
                    new_after["justificativa_operador"] = justification
                    new_after["rateio_origem_contribuicao_id"] = contribution_id
                    write_audit_log(
                        conn,
                        organization_id,
                        "criar_linha_rateio_contribuicao_django",
                        "contribuicoes",
                        new_id,
                        None,
                        new_after,
                        actor=actor,
                    )
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel ratear a contribuicao: {exc}") from exc
    return created_ids


def next_receipt_number(conn: sqlite3.Connection, organization_id: int, emission_date: str) -> str:
    digits = "".join(ch for ch in str(emission_date or "") if ch.isdigit())
    prefix = f"REC-{digits[:6] or date.today().strftime('%Y%m')}"
    row = conn.execute(
        """
        SELECT numero
          FROM recibos
         WHERE organizacao_id = ? AND numero LIKE ?
         ORDER BY numero DESC
         LIMIT 1
        """,
        (organization_id, f"{prefix}-%"),
    ).fetchone()
    next_seq = 1
    if row and row["numero"]:
        try:
            next_seq = int(str(row["numero"]).split("-")[-1]) + 1
        except ValueError:
            next_seq = 1
    return f"{prefix}-{next_seq:04d}"


def create_receipt(payload: Any, actor: str = "") -> int:
    person_id = moneyless_int(_form_value(payload, "pessoa_id"))
    getter = getattr(payload, "getlist", None)
    raw_ids = getter("contribuicao_id") if getter else payload.get("contribuicao_id", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    contribution_ids = [moneyless_int(value) for value in raw_ids if moneyless_int(value)]
    if not contribution_ids:
        raise LegacyWriteError("Selecione pelo menos uma contribuicao para o recibo.")
    emission_date = normalize_query(_form_value(payload, "data_emissao", date.today().isoformat()))
    if not emission_date:
        emission_date = date.today().isoformat()
    notes = normalize_query(_form_value(payload, "observacoes"))
    with connect_legacy_write() as conn:
        person = get_person(conn, person_id)
        if person is None:
            raise LegacyWriteError("Escolha uma pessoa valida para gerar o recibo.")
        organization_id = moneyless_int(person["organizacao_id"])
        placeholders = ",".join("?" for _ in contribution_ids)
        rows = conn.execute(
            f"""
            SELECT c.*
              FROM contribuicoes c
             WHERE c.id IN ({placeholders})
               AND c.pessoa_id = ?
               AND c.ativo = 1
               AND NOT EXISTS (
                    SELECT 1
                      FROM recibo_itens ri
                      JOIN recibos r ON r.id = ri.recibo_id
                     WHERE ri.contribuicao_id = c.id
                       AND r.status <> 'cancelado'
                       AND r.cancelado_em IS NULL
               )
             ORDER BY c.data_recebimento, c.id
            """,
            (*contribution_ids, person_id),
        ).fetchall()
        if len(rows) != len(contribution_ids):
            raise LegacyWriteError("Uma ou mais contribuicoes ja estao em recibo ativo ou nao pertencem a pessoa selecionada.")
        total = round(sum(float(row["valor"] or 0) for row in rows), 2)
        period_start = min(str(row["data_recebimento"]) for row in rows)
        period_end = max(str(row["data_recebimento"]) for row in rows)
        receipt_number = next_receipt_number(conn, organization_id, emission_date)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO recibos (
                        organizacao_id, pessoa_id, numero, data_emissao, periodo_inicio, periodo_fim,
                        valor_total, status, arquivo_path, observacoes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'emitido', NULL, ?)
                    """,
                    (organization_id, person_id, receipt_number, emission_date, period_start, period_end, total, notes),
                )
                receipt_id = moneyless_int(cursor.lastrowid)
                for row in rows:
                    conn.execute(
                        "INSERT INTO recibo_itens (recibo_id, contribuicao_id, valor) VALUES (?, ?, ?)",
                        (receipt_id, row["id"], row["valor"]),
                    )
                after = dict(get_receipt(conn, receipt_id) or {})
                after["contribuicoes"] = contribution_ids
                write_audit_log(conn, organization_id, "gerar_recibo_django", "recibos", receipt_id, None, after, actor=actor)
                return receipt_id
        except sqlite3.IntegrityError as exc:
            raise LegacyWriteError(f"Nao foi possivel gerar o recibo: {exc}") from exc


def _slugify_filename_text(value: object, fallback: str = "arquivo") -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
    return slug or fallback


def import_people_from_upload(
    filename: str,
    payload: bytes,
    allow_duplicate_file: bool = False,
) -> dict[str, object]:
    if not payload:
        raise LegacyWriteError("Selecione uma planilha Excel antes de importar pessoas.")
    if not filename.lower().endswith(".xlsx"):
        raise LegacyWriteError("Envie uma planilha Excel no formato .xlsx.")

    people_upload_dir = Path(settings.REPO_ROOT) / "data" / "people_uploads"
    reports_dir = Path(settings.REPO_ROOT) / "reports"
    people_upload_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    file_hash = hashlib.sha256(payload).hexdigest()
    with connect_legacy_write() as conn:
        if not allow_duplicate_file:
            existing = conn.execute(
                "SELECT id FROM import_lotes WHERE arquivo_hash = ? ORDER BY id DESC LIMIT 1",
                (file_hash,),
            ).fetchone()
            if existing is not None:
                raise LegacyWriteError(f"Esta planilha ja foi importada no lote de pessoas #{existing['id']}.")

    target_name = f"{date.today().isoformat()}_{_slugify_filename_text(Path(filename).stem, fallback='pessoas')}_{file_hash[:10]}.xlsx"
    stored_path = people_upload_dir / target_name
    stored_path.write_bytes(payload)
    report_path = reports_dir / f"RELATORIO_IMPORTACAO_INCREMENTAL_PESSOAS_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_hash[:10]}.md"
    try:
        from scripts.importar_membros_xlsx import import_members_incremental

        return import_members_incremental(
            stored_path,
            legacy_db_path(),
            report_path,
            allow_duplicate_file=allow_duplicate_file,
        )
    except Exception as exc:
        raise LegacyWriteError(str(exc)) from exc
