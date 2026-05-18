#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
SCHEMA_PATH = ROOT / "schema_power_church_v0.sql"


def prepare_temp_db() -> Path:
    target = Path(tempfile.gettempdir()) / "power_church_write_probe.sqlite3"
    if target.exists():
        target.unlink()
    conn = sqlite3.connect(target)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            conn.execute("ALTER TABLE contribuicoes ADD COLUMN contribuinte_id INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        try:
            conn.execute("ALTER TABLE contribuicoes ADD COLUMN status_operacional TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        for column_def in [
            "pix_movimento_id INTEGER",
            "extrato_movimento_id INTEGER",
        ]:
            try:
                conn.execute(f"ALTER TABLE contribuicoes ADD COLUMN {column_def}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute(
            """
            CREATE TABLE contribuintes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                pessoa_id INTEGER,
                tipo TEXT NOT NULL DEFAULT 'pf',
                nome TEXT NOT NULL,
                nome_normalizado TEXT NOT NULL,
                documento_principal TEXT,
                documento_tipo TEXT,
                origem TEXT NOT NULL DEFAULT 'manual',
                qualidade TEXT NOT NULL DEFAULT 'doador',
                status TEXT NOT NULL DEFAULT 'ativo',
                observacoes TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE contribuintes_identificadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                pessoa_id INTEGER,
                contribuinte_id INTEGER,
                tipo TEXT NOT NULL,
                valor TEXT NOT NULL,
                principal INTEGER NOT NULL DEFAULT 0,
                ativo INTEGER NOT NULL DEFAULT 1,
                observacoes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT
            )
            """
        )
        conn.execute("INSERT INTO organizacoes(nome, tipo, status) VALUES ('Teste', 'igreja', 'ativa')")
        conn.execute("INSERT INTO unidades(organizacao_id, nome, tipo, ativa) VALUES (1, 'Sede', 'sede', 1)")
        conn.execute(
            """
            INSERT INTO tipos_contribuicao (
                organizacao_id, codigo, nome, exige_pessoa, natureza_receita, ativo
            ) VALUES (1, 'DIZIMO', 'Dizimo', 1, 'receita_operacional', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO formas_recebimento (organizacao_id, codigo, nome, ativo)
            VALUES (1, 'PIX', 'PIX', 1)
            """
        )
        conn.commit()
    finally:
        conn.close()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida escrita Django de pessoas em banco temporario.")
    parser.add_argument("--db", default="", help="Ignorado: o verificador sempre usa banco temporario.")
    parser.add_argument("--report", action="store_true", help="Aceito para compatibilidade com a bateria total.")
    parser.parse_args()
    if Path(sys.executable).resolve() != DJANGO_VENV_PYTHON.resolve() and DJANGO_VENV_PYTHON.exists():
        completed = subprocess.run(
            [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout.strip())
        if completed.stderr:
            print(completed.stderr.strip(), file=sys.stderr)
        return completed.returncode
    temp_db = prepare_temp_db()
    photo_temp_dir = Path(tempfile.gettempdir()) / "power_church_write_probe_photos"
    if photo_temp_dir.exists():
        shutil.rmtree(photo_temp_dir)
    envelope_temp_dir = Path(tempfile.gettempdir()) / "power_church_write_probe_envelopes"
    if envelope_temp_dir.exists():
        shutil.rmtree(envelope_temp_dir)
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(temp_db)
    os.environ["POWER_CHURCH_PHOTO_DIR"] = str(photo_temp_dir)
    os.environ["POWER_CHURCH_ENVELOPE_DIR"] = str(envelope_temp_dir)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

    import django

    django.setup()

    from power_church_django.services.legacy_bank_write import save_cent_rule_from_form
    from power_church_django.services.photos import find_member_photo, save_member_photo_payload
    from power_church_django.services.legacy_write import (
        LegacyWriteError,
        create_contribution,
        create_envelope_contribution_batch,
        create_envelope_image_lot,
        create_frequentador_from_contributor,
        create_manual_contribution_batch,
        create_person,
        create_person_relationship,
        create_receipt,
        deactivate_person_relationship,
        get_person_form_initial,
        ignore_pending_envelope,
        launch_pending_envelope,
        link_contributor_to_person_by_id,
        purge_secure_person_trash,
        split_contribution,
        soft_delete_person,
        suppress_family_suggestion,
        update_person_relationship,
        update_contribution,
        update_launched_envelope,
        update_person,
    )

    class FakeUpload:
        def __init__(self, name: str = "envelope_teste.png", payload: bytes = b"\x89PNG\r\n\x1a\nenvelope-teste-django"):
            self.name = name
            self.content_type = "image/png"
            self._payload = payload

        def chunks(self):
            yield self._payload

    payload = {
        "nome": "Pessoa Teste Django",
        "status": "frequentador",
        "cpf": "52998224725",
        "email_principal": "teste@example.com",
        "cep": "24000-000",
        "logradouro": "Rua Teste Django",
        "numero": "100",
        "complemento": "Casa",
        "bairro": "Centro",
        "cidade": "Niteroi",
        "uf": "RJ",
    }
    try:
        create_person({**payload, "nome": "CPF Invalido Teste", "cpf": "12345678901"}, actor="verificador")
    except Exception as exc:
        if "CPF invalido" not in str(exc):
            raise
    else:
        raise AssertionError("Cadastro manual aceitou CPF invalido.")
    try:
        create_person(
            {"nome": "Email Invalido Teste", "status": "frequentador", "email_principal": "email-invalido"},
            actor="verificador",
        )
    except Exception as exc:
        if "E-mail invalido" not in str(exc):
            raise
    else:
        raise AssertionError("Cadastro manual aceitou e-mail invalido.")
    person_id = create_person(payload, actor="verificador")
    try:
        create_person({**payload, "nome": "CPF Duplicado Teste", "email_principal": "duplicado@example.com"}, actor="verificador")
    except Exception as exc:
        if "CPF ja cadastrado" not in str(exc):
            raise
    else:
        raise AssertionError("Cadastro manual aceitou CPF duplicado em outra ficha ativa.")
    first_photo = save_member_photo_payload(
        person_id,
        payload["cpf"],
        payload["nome"],
        {
            "filename": "foto_teste.png",
            "content_type": "image/png",
            "extension": ".png",
            "payload": b"\x89PNG\r\n\x1a\nfoto-teste-django",
            "size": 25,
        },
    )
    if first_photo is None or not first_photo.exists():
        raise AssertionError("Upload de foto na criacao de pessoa nao gravou arquivo.")
    initial = get_person_form_initial(person_id)
    if initial is None:
        raise AssertionError("Ficha recem-criada nao retornou para edicao.")
    initial["nome"] = "Pessoa Teste Django Atualizada"
    update_person(person_id, initial, actor="verificador")
    updated_photo = save_member_photo_payload(
        person_id,
        initial["cpf"],
        initial["nome"],
        {
            "filename": "foto_teste_atualizada.png",
            "content_type": "image/png",
            "extension": ".png",
            "payload": b"\x89PNG\r\n\x1a\nfoto-teste-django-atualizada",
            "size": 37,
        },
    )
    if updated_photo is None or not updated_photo.exists() or updated_photo == first_photo:
        raise AssertionError("Upload de foto na edicao de pessoa nao substituiu arquivo.")
    current_photo = find_member_photo(person_id, initial["cpf"], initial["nome"])
    if current_photo is None or current_photo.name != updated_photo.name:
        raise AssertionError("Foto atual da pessoa nao foi encontrada apos edicao.")
    contribution_id = create_contribution(
        {
            "pessoa_id": str(person_id),
            "data_recebimento": "2026-05-08",
            "tipo_contribuicao_id": "1",
            "forma_recebimento_id": "1",
            "valor": "123,45",
            "status_operacional": "regular",
            "observacoes": "Lancamento manual de teste.",
            "justificativa": "Teste automatico de criacao manual.",
        },
        actor="verificador",
    )
    update_contribution(
        contribution_id,
        {
            "data_recebimento": "2026-05-09",
            "tipo_contribuicao_id": "1",
            "forma_recebimento_id": "1",
            "valor": "150,00",
            "status_operacional": "em_saneamento",
            "observacoes": "Lancamento manual de teste ajustado.",
            "justificativa": "Teste automatico de ajuste manual.",
        },
        actor="verificador",
    )
    manual_ids = create_manual_contribution_batch(
        {
            "data_recebimento": "2026-05-11",
            "valor_total": "100,00",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "origem_operacional": "Envelope de teste automatico.",
            "observacoes": "Rateio manual de teste.",
            "justificativa": "Teste automatico de rateio manual.",
            "line_count": "2",
            "linha_pessoa_id_1": str(person_id),
            "linha_tipo_contribuicao_id_1": "1",
            "linha_valor_1": "70,00",
            "linha_observacoes_1": "Dizimo principal.",
            "linha_contribuinte_nome_2": "Filho Teste Sem Cadastro",
            "linha_tipo_contribuicao_id_2": "1",
            "linha_valor_2": "30,00",
            "linha_observacoes_2": "Dizimo do filho.",
        },
        actor="verificador",
    )
    envelope_result = create_envelope_contribution_batch(
        {
            "data_recebimento": "2026-05-12",
            "competencia_mes": "2026-05",
            "nome_lote": "Envelopes Teste Maio 26",
            "valor_total": "454,00",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "pessoa_id": str(person_id),
            "nome_informado": "Pessoa Teste Django Atualizada",
            "telefone_informado": "21999999999",
            "endereco_informado": "Rua Teste Django, 100",
            "origem_operacional": "Envelope digitalizado de teste automatico.",
            "rastreio_forma_identificada": "cheque",
            "rastreio_banco_operadora": "Banco Teste",
            "rastreio_numero_cheque": "000123",
            "rastreio_numero_operacao": "OP-ENVELOPE-454",
            "rastreio_nsu_tid": "NSU123456",
            "rastreio_ultimos_digitos_cartao": "9876",
            "rastreio_data_operacao": "2026-05-12",
            "rastreio_valor_operacao": "454,00",
            "rastreio_status_conciliacao": "pendente",
            "rastreio_observacoes": "Comprovante de teste para conciliacao futura.",
            "observacoes": "Envelope manual com imagem de teste.",
            "justificativa": "Teste automatico de envelope com imagem arquivada.",
            "line_count": "3",
            "linha_pessoa_id_1": str(person_id),
            "linha_tipo_contribuicao_id_1": "1",
            "linha_valor_1": "304,00",
            "linha_observacoes_1": "Dizimo principal.",
            "linha_contribuinte_nome_2": "Campanha Terreno Teste",
            "linha_tipo_contribuicao_id_2": "1",
            "linha_valor_2": "100,00",
            "linha_observacoes_2": "Compra do terreno.",
            "linha_contribuinte_nome_3": "Ar Refrigerado Teste",
            "linha_tipo_contribuicao_id_3": "1",
            "linha_valor_3": "50,00",
            "linha_observacoes_3": "Ar refrigerado.",
        },
        FakeUpload(),
        actor="verificador",
    )
    local_envelope_path = Path(tempfile.gettempdir()) / "power_church_envelope_local_teste.png"
    local_envelope_path.write_bytes(b"\x89PNG\r\n\x1a\nenvelope-local-teste-django")
    simple_envelope_result = create_envelope_contribution_batch(
        {
            "data_recebimento": "2026-05-13",
            "competencia_mes": "2026-05",
            "nome_lote": "Envelopes Teste Maio 26",
            "valor_total": "25,00",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "pessoa_ref": f"{person_id} · Pessoa Teste Django Atualizada · Ficha 00001",
            "telefone_informado": "21988887777",
            "endereco_informado": "Rua Teste Django, 100",
            "origem_operacional": "Envelope digitalizado",
            "observacoes": "Envelope simples sem rateio manual.",
            "justificativa": "Envelope conferido manualmente; imagem anexada para auditoria.",
            "line_count": "10",
            "imagem_envelope_path": str(local_envelope_path),
        },
        None,
        actor="verificador",
    )
    editable_envelope_result = create_envelope_contribution_batch(
        {
            "data_recebimento": "2026-05-15",
            "competencia_mes": "2026-05",
            "nome_lote": "Envelopes Teste Maio 26",
            "valor_total": "60,00",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "pessoa_ref": f"Pessoa #{person_id} · Pessoa Teste Django Atualizada · Ficha 00001",
            "nome_informado": "Pessoa Teste Django Atualizada",
            "origem_operacional": "Envelope digitalizado",
            "observacoes": "Envelope criado para testar edicao segura.",
            "justificativa": "Teste automatico de envelope editavel.",
            "line_count": "1",
            "linha_participante_ref_1": f"Pessoa #{person_id} · Pessoa Teste Django Atualizada · Ficha 00001",
            "linha_tipo_contribuicao_id_1": "1",
            "linha_valor_1": "60,00",
            "linha_observacoes_1": "Linha original antes da correcao.",
        },
        FakeUpload("envelope_editavel_teste.png", b"\x89PNG\r\n\x1a\nenvelope-editavel-original"),
        actor="verificador",
    )
    editable_original_ids = list(editable_envelope_result["contribution_ids"])
    editable_update_result = update_launched_envelope(
        editable_envelope_result["envelope_id"],
        {
            "data_recebimento": "2026-05-16",
            "competencia_mes": "2026-05",
            "nome_lote": "Envelopes Teste Maio 26",
            "valor_total": "80,00",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "pessoa_ref": f"Pessoa #{person_id} · Pessoa Teste Django Atualizada · Ficha 00001",
            "nome_informado": "Pessoa Teste Django Atualizada",
            "origem_operacional": "Envelope digitalizado",
            "observacoes": "Envelope corrigido pelo verificador.",
            "justificativa": "Teste automatico de correcao auditada de envelope.",
            "line_count": "2",
            "linha_participante_ref_1": f"Pessoa #{person_id} · Pessoa Teste Django Atualizada · Ficha 00001",
            "linha_tipo_contribuicao_id_1": "1",
            "linha_valor_1": "50,00",
            "linha_observacoes_1": "Linha corrigida para pessoa do rol.",
            "linha_participante_ref_2": "Filho Teste Campo Unico",
            "linha_tipo_contribuicao_id_2": "1",
            "linha_valor_2": "30,00",
            "linha_observacoes_2": "Linha criada como contribuinte auxiliar pelo campo unico.",
        },
        actor="verificador",
    )
    conn = sqlite3.connect(temp_db)
    try:
        contributions_before_batch_lot = conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0]
    finally:
        conn.close()
    envelope_lot_result = create_envelope_image_lot(
        {
            "nome_lote": "Lote Digitalizado Teste Maio 26",
            "competencia_mes": "2026-05",
            "data_padrao_recebimento": "2026-05-01",
            "origem_operacional": "Envelope digitalizado",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "observacoes": "Lote criado pelo verificador.",
        },
        [
            FakeUpload("001_marilda_teste.png", b"\x89PNG\r\n\x1a\nenvelope-lote-001"),
            FakeUpload("002_ignorar_teste.png", b"\x89PNG\r\n\x1a\nenvelope-lote-002"),
        ],
        actor="verificador",
    )
    local_batch_dir = Path(tempfile.gettempdir()) / "power church lote envelopes com espacos"
    if local_batch_dir.exists():
        shutil.rmtree(local_batch_dir)
    local_batch_dir.mkdir(parents=True)
    (local_batch_dir / "001 caminho local.png").write_bytes(b"\x89PNG\r\n\x1a\nenvelope-lote-caminho-local")
    local_path_lot_result = create_envelope_image_lot(
        {
            "nome_lote": "Lote Caminho Local Teste Maio 26",
            "competencia_mes": "2026-05",
            "data_padrao_recebimento": "2026-05-02",
            "origem_operacional": "Envelope digitalizado",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "pasta_origem": local_batch_dir.as_uri(),
            "observacoes": "Lote criado por caminho file:// no verificador.",
        },
        [],
        actor="verificador",
    )
    if len(local_path_lot_result["envelope_ids"]) != 1:
        raise AssertionError("Lote de envelopes por caminho local/file:// nao criou envelope pendente.")
    pending_lot_ids = [int(value) for value in envelope_lot_result["envelope_ids"]]
    if len(pending_lot_ids) != 2:
        raise AssertionError("Lote de envelopes digitalizados nao criou dois envelopes pendentes.")
    conn = sqlite3.connect(temp_db)
    try:
        pending_before_launch = conn.execute(
            "SELECT COUNT(*) FROM envelopes WHERE lote_id = ? AND status = 'aguardando_digitacao' AND ativo = 1",
            (envelope_lot_result["lot_id"],),
        ).fetchone()[0]
        contributions_after_batch_creation = conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0]
    finally:
        conn.close()
    if int(pending_before_launch or 0) != 2:
        raise AssertionError("Lote de envelopes nao deixou os arquivos como aguardando digitacao.")
    if contributions_after_batch_creation != contributions_before_batch_lot:
        raise AssertionError("Criacao do lote de envelopes criou contribuicao financeira indevidamente.")
    conn = sqlite3.connect(temp_db)
    try:
        local_path_pending = conn.execute(
            "SELECT COUNT(*) FROM envelopes WHERE lote_id = ? AND status = 'aguardando_digitacao' AND ativo = 1",
            (local_path_lot_result["lot_id"],),
        ).fetchone()[0]
        contributions_after_local_path_lot = conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0]
    finally:
        conn.close()
    if int(local_path_pending or 0) != 1:
        raise AssertionError("Lote por caminho local/file:// nao deixou envelope aguardando digitacao.")
    if contributions_after_local_path_lot != contributions_before_batch_lot:
        raise AssertionError("Lote por caminho local/file:// criou contribuicao financeira indevidamente.")
    launched_pending_result = launch_pending_envelope(
        pending_lot_ids[0],
        {
            "lote_id": str(envelope_lot_result["lot_id"]),
            "data_recebimento": "2026-05-14",
            "competencia_mes": "2026-05",
            "valor_total": "40,00",
            "tipo_contribuicao_id_padrao": "1",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "pessoa_id": str(person_id),
            "origem_operacional": "Envelope digitalizado",
            "rastreio_forma_identificada": "cartao_credito",
            "rastreio_banco_operadora": "Operadora Teste",
            "rastreio_numero_operacao": "AUT-40",
            "rastreio_nsu_tid": "TID40",
            "rastreio_data_operacao": "2026-05-14",
            "rastreio_valor_operacao": "40,00",
            "observacoes": "Envelope pendente digitado pelo verificador.",
            "justificativa": "Teste automatico de lancamento de envelope pendente.",
            "line_count": "10",
        },
        actor="verificador",
    )
    ignore_pending_envelope(
        pending_lot_ids[1],
        "Teste automatico de envelope ignorado no lote.",
        actor="verificador",
    )
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    try:
        contribution_count_after_batch_lot = conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0]
        batch_lot_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'lancado' THEN 1 ELSE 0 END) AS lancados,
                SUM(CASE WHEN status = 'ignorado' THEN 1 ELSE 0 END) AS ignorados,
                SUM(CASE WHEN status = 'aguardando_digitacao' THEN 1 ELSE 0 END) AS pendentes,
                SUM(CASE WHEN status = 'lancado' THEN total_informado ELSE 0 END) AS total_lancado
              FROM envelopes
             WHERE lote_id = ? AND ativo = 1
            """,
            (envelope_lot_result["lot_id"],),
        ).fetchone()
        batch_launched_envelope = conn.execute(
            """
            SELECT e.status, e.data_recebimento, e.total_informado, c.data_recebimento AS data_contribuicao,
                   c.valor AS valor_contribuicao, e.rastreio_forma_identificada, e.rastreio_numero_operacao,
                   e.rastreio_nsu_tid, e.rastreio_valor_operacao
              FROM envelopes e
              JOIN envelope_itens ei ON ei.envelope_id = e.id AND ei.ativo = 1
              JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
             WHERE e.id = ?
            """,
            (pending_lot_ids[0],),
        ).fetchone()
        batch_ignored_contributions = conn.execute(
            """
            SELECT COUNT(*)
              FROM envelope_itens ei
              JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
             WHERE ei.envelope_id = ?
            """,
            (pending_lot_ids[1],),
        ).fetchone()[0]
    finally:
        conn.close()
    split_ids = split_contribution(
        contribution_id,
        {
            "data_recebimento": "2026-05-09",
            "forma_recebimento_id": "1",
            "status_operacional": "regular",
            "observacoes": "Rateio da contribuicao original.",
            "justificativa": "Teste automatico de divisao de contribuicao.",
            "line_count": "2",
            "linha_pessoa_id_1": str(person_id),
            "linha_tipo_contribuicao_id_1": "1",
            "linha_valor_1": "100,00",
            "linha_contribuinte_nome_2": "Filho Teste Sem Cadastro",
            "linha_tipo_contribuicao_id_2": "1",
            "linha_valor_2": "50,00",
        },
        actor="verificador",
    )
    rule_id = save_cent_rule_from_form(
        {
            "codigo_centavos": "98",
            "nome_destinacao": "Teste Centavos Django",
            "tipo_contribuicao_id": "",
            "ativo": "1",
        }
    )
    receipt_id = create_receipt(
        {
            "pessoa_id": str(person_id),
            "contribuicao_id": [str(contribution_id)],
            "data_emissao": "2026-05-10",
            "observacoes": "Recibo de teste automatico.",
        },
        actor="verificador",
    )
    conn = sqlite3.connect(temp_db)
    try:
        linked_contributor_id = conn.execute(
            """
            INSERT INTO contribuintes (
                organizacao_id, pessoa_id, tipo, nome, nome_normalizado, documento_principal,
                documento_tipo, origem, qualidade, status, ativo
            ) VALUES (1, NULL, 'pf', 'Contribuinte Para Vinculo Django', 'CONTRIBUINTE PARA VINCULO DJANGO',
                      NULL, NULL, 'teste', 'doador', 'ativo', 1)
            """
        ).lastrowid
        frequentador_contributor_id = conn.execute(
            """
            INSERT INTO contribuintes (
                organizacao_id, pessoa_id, tipo, nome, nome_normalizado, documento_principal,
                documento_tipo, origem, qualidade, status, ativo
            ) VALUES (1, NULL, 'pf', 'Frequentador Criado Pelo Django', 'FREQUENTADOR CRIADO PELO DJANGO',
                      NULL, NULL, 'teste', 'doador', 'ativo', 1)
            """
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    link_contributor_to_person_by_id(linked_contributor_id, person_id, actor="verificador")
    frequentador_person_id = create_frequentador_from_contributor(frequentador_contributor_id, actor="verificador")
    frequentador_initial = get_person_form_initial(frequentador_person_id)
    if frequentador_initial is None:
        raise AssertionError("Frequentador criado nao retornou para edicao.")
    frequentador_initial.update(
        {
            "cep": "24000-000",
            "logradouro": "Rua Teste Django",
            "numero": "100",
            "complemento": "Casa",
            "bairro": "Centro",
            "cidade": "Niteroi",
            "uf": "RJ",
        }
    )
    update_person(frequentador_person_id, frequentador_initial, actor="verificador")
    delete_candidate_id = create_person(
        {
            "nome": "Pessoa Para Lixeira Segura",
            "status": "visitante",
            "cpf": "",
            "observacoes": "Criada somente para testar lixeira segura.",
        },
        actor="verificador",
    )
    trash_id = soft_delete_person(delete_candidate_id, "Teste automatico de lixeira segura.", actor="verificador")
    purge_candidate_id = create_person(
        {
            "nome": "Pessoa Para Purga Segura",
            "status": "visitante",
            "cpf": "",
            "observacoes": "Criada somente para testar purga segura.",
        },
        actor="verificador",
    )
    purge_trash_id = soft_delete_person(purge_candidate_id, "Teste automatico de lixeira antes da purga.", actor="verificador")
    purged_person_id = purge_secure_person_trash(purge_trash_id, "Teste automatico de purga segura.", actor="verificador")
    blocked_candidate_id = create_person(
        {
            "nome": "Pessoa Com Contribuicao Bloqueia Purga",
            "status": "visitante",
            "cpf": "",
            "observacoes": "Criada somente para testar bloqueio de purga.",
        },
        actor="verificador",
    )
    create_contribution(
        {
            "pessoa_id": str(blocked_candidate_id),
            "data_recebimento": "2026-05-09",
            "tipo_contribuicao_id": "1",
            "forma_recebimento_id": "1",
            "valor": "10,00",
            "status_operacional": "regular",
            "observacoes": "Lancamento para bloquear purga.",
            "justificativa": "Teste automatico de bloqueio de purga segura.",
        },
        actor="verificador",
    )
    blocked_trash_id = soft_delete_person(blocked_candidate_id, "Teste automatico de bloqueio de purga.", actor="verificador")
    try:
        purge_secure_person_trash(blocked_trash_id, "Tentativa deve ser bloqueada.", actor="verificador")
    except LegacyWriteError as exc:
        if "Purga bloqueada" not in str(exc):
            raise
    else:
        raise AssertionError("Purga segura permitiu apagar pessoa com contribuicao vinculada.")
    conn = sqlite3.connect(temp_db)
    try:
        relationship_row = conn.execute(
            """
            SELECT id
              FROM pessoa_relacionamentos
             WHERE ativo = 1
               AND tipo_relacionamento = 'nucleo_familiar'
               AND (
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
                    OR
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
               )
             LIMIT 1
            """,
            (person_id, frequentador_person_id, frequentador_person_id, person_id),
        ).fetchone()
    finally:
        conn.close()
    if relationship_row is None:
        raise AssertionError("Familia domiciliar automatica por endereco nao foi criada.")
    relationship_id = relationship_row[0]
    frequentador_initial = get_person_form_initial(frequentador_person_id)
    if frequentador_initial is None:
        raise AssertionError("Frequentador criado nao retornou para testar correcao de familia domiciliar.")
    frequentador_initial.update(
        {
            "cep": "24000-000",
            "logradouro": "Rua Teste Django",
            "numero": "999",
            "complemento": "Casa",
            "bairro": "Centro",
            "cidade": "Niteroi",
            "uf": "RJ",
        }
    )
    update_person(frequentador_person_id, frequentador_initial, actor="verificador")
    conn = sqlite3.connect(temp_db)
    try:
        inactive = conn.execute("SELECT ativo FROM pessoa_relacionamentos WHERE id = ?", (relationship_id,)).fetchone()[0]
    finally:
        conn.close()
    if int(inactive or 0) != 0:
        raise AssertionError("Sincronizacao por endereco nao desativou familia domiciliar automatica quando o endereco mudou.")
    frequentador_initial = get_person_form_initial(frequentador_person_id)
    if frequentador_initial is None:
        raise AssertionError("Frequentador criado nao retornou para restaurar endereco.")
    frequentador_initial.update(
        {
            "cep": "24000-000",
            "logradouro": "Rua Teste Django",
            "numero": "100",
            "complemento": "Casa",
            "bairro": "Centro",
            "cidade": "Niteroi",
            "uf": "RJ",
        }
    )
    update_person(frequentador_person_id, frequentador_initial, actor="verificador")
    conn = sqlite3.connect(temp_db)
    try:
        relationship_row = conn.execute(
            """
            SELECT id
              FROM pessoa_relacionamentos
             WHERE ativo = 1
               AND tipo_relacionamento = 'nucleo_familiar'
               AND (
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
                    OR
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
               )
             ORDER BY id DESC
             LIMIT 1
            """,
            (person_id, frequentador_person_id, frequentador_person_id, person_id),
        ).fetchone()
    finally:
        conn.close()
    if relationship_row is None:
        raise AssertionError("Sincronizacao por endereco nao recriou familia domiciliar automatica quando o endereco voltou a coincidir.")
    relationship_id = relationship_row[0]
    suppressed_person_id = create_person(
        {
            "nome": "Pessoa Para Ignorar Familia Domiciliar Automatica",
            "status": "visitante",
            "cpf": "",
            "email_principal": "",
            "cep": "24000-000",
            "logradouro": "Rua Teste Django",
            "numero": "100",
            "complemento": "Casa",
            "bairro": "Centro",
            "cidade": "Niteroi",
            "uf": "RJ",
        },
        actor="verificador",
    )
    suppress_family_suggestion(person_id, suppressed_person_id, actor="verificador")
    update_person(person_id, get_person_form_initial(person_id) or {}, actor="verificador")
    conn = sqlite3.connect(temp_db)
    try:
        suppressed_active = conn.execute(
            """
            SELECT COUNT(*)
              FROM pessoa_relacionamentos
             WHERE ativo = 1
               AND (
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
                    OR
                    (pessoa_id = ? AND pessoa_relacionada_id = ?)
               )
            """,
            (person_id, suppressed_person_id, suppressed_person_id, person_id),
        ).fetchone()[0]
    finally:
        conn.close()
    if int(suppressed_active or 0) != 0:
        raise AssertionError("Sugestao familiar ignorada foi recriada automaticamente pela sincronizacao.")
    duplicate_id = create_person_relationship(
        person_id,
        {
            "related_person_id": str(frequentador_person_id),
            "tipo_relacionamento": "nucleo_familiar",
            "observacoes": "Tentativa duplicada deve reaproveitar vinculo existente.",
        },
        actor="verificador",
    )
    if duplicate_id != relationship_id:
        raise AssertionError("Criacao manual duplicou relacao familiar existente.")
    update_person_relationship(
        person_id,
        relationship_id,
        {
            "tipo_relacionamento": "familia_estendida",
            "observacoes": "Teste automatico de modificacao do vinculo.",
        },
        actor="verificador",
    )

    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    try:
        people = conn.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0]
        active_people = conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0]
        audit = conn.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0]
        contacts = conn.execute("SELECT COUNT(*) FROM pessoa_contatos").fetchone()[0]
        contributors = conn.execute("SELECT COUNT(*) FROM contribuintes").fetchone()[0]
        contribution = conn.execute(
            "SELECT valor, data_recebimento, status_operacional FROM contribuicoes WHERE id = ?",
            (contribution_id,),
        ).fetchone()
        contribution_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE tabela = 'contribuicoes'
               AND registro_id = ?
               AND acao IN ('lancar_contribuicao_django', 'ajustar_contribuicao_manual_django')
            """,
            (contribution_id,),
        ).fetchone()[0]
        manual_total = conn.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
              FROM contribuicoes
             WHERE id IN (%s)
            """
            % ",".join("?" for _ in manual_ids),
            tuple(manual_ids),
        ).fetchone()[0]
        envelope = conn.execute(
            """
            SELECT e.id, e.lote_id, e.total_informado, e.total_linhas, e.caminho_imagem, e.imagem_hash,
                   e.rastreio_forma_identificada, e.rastreio_banco_operadora, e.rastreio_numero_cheque,
                   e.rastreio_numero_operacao, e.rastreio_nsu_tid, e.rastreio_ultimos_digitos_cartao,
                   e.rastreio_data_operacao, e.rastreio_valor_operacao, e.rastreio_status_conciliacao,
                   e.rastreio_observacoes, l.caminho_pasta
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
             WHERE e.id = ?
            """,
            (envelope_result["envelope_id"],),
        ).fetchone()
        envelope_item_count = conn.execute(
            "SELECT COUNT(*) FROM envelope_itens WHERE envelope_id = ? AND ativo = 1",
            (envelope_result["envelope_id"],),
        ).fetchone()[0]
        envelope_total = conn.execute(
            """
            SELECT COALESCE(SUM(c.valor), 0)
              FROM envelope_itens ei
              JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
             WHERE ei.envelope_id = ? AND ei.ativo = 1
            """,
            (envelope_result["envelope_id"],),
        ).fetchone()[0]
        simple_envelope = conn.execute(
            """
            SELECT e.id, e.total_informado, e.total_linhas, e.caminho_imagem
              FROM envelopes e
             WHERE e.id = ?
            """,
            (simple_envelope_result["envelope_id"],),
        ).fetchone()
        simple_envelope_item = conn.execute(
            """
            SELECT ei.valor, ei.pessoa_id, ei.tipo_contribuicao_id, c.valor AS contribuicao_valor
              FROM envelope_itens ei
              JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
             WHERE ei.envelope_id = ? AND ei.ativo = 1
            """,
            (simple_envelope_result["envelope_id"],),
        ).fetchall()
        editable_envelope = conn.execute(
            """
            SELECT id, data_recebimento, total_informado, total_linhas, status
              FROM envelopes
             WHERE id = ?
            """,
            (editable_envelope_result["envelope_id"],),
        ).fetchone()
        editable_active_items = conn.execute(
            """
            SELECT COUNT(*) AS qtd, COALESCE(SUM(c.valor), 0) AS total
              FROM envelope_itens ei
              JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
             WHERE ei.envelope_id = ? AND ei.ativo = 1
            """,
            (editable_envelope_result["envelope_id"],),
        ).fetchone()
        editable_old_active = conn.execute(
            """
            SELECT COUNT(*)
              FROM contribuicoes
             WHERE id IN (%s) AND ativo = 1
            """
            % ",".join("?" for _ in editable_original_ids),
            tuple(editable_original_ids),
        ).fetchone()[0]
        editable_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE acao IN (
                'corrigir_envelope_lancado_django',
                'desativar_contribuicao_por_correcao_envelope_django',
                'recriar_contribuicao_por_correcao_envelope_django'
             )
            """
        ).fetchone()[0]
        profile_update_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM envelope_atualizacoes_cadastrais
             WHERE status = 'pendente'
               AND envelope_id IN (?, ?)
               AND pessoa_id = ?
            """,
            (envelope_result["envelope_id"], simple_envelope_result["envelope_id"], person_id),
        ).fetchone()[0]
        envelope_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE acao IN ('registrar_envelope_manual_django', 'lancar_contribuicao_por_envelope_django')
            """
        ).fetchone()[0]
        split_total = conn.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
              FROM contribuicoes
             WHERE id IN (%s)
            """
            % ",".join("?" for _ in split_ids),
            tuple(split_ids),
        ).fetchone()[0]
        split_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE tabela = 'contribuicoes'
               AND acao IN ('ratear_contribuicao_manual_django', 'criar_linha_rateio_contribuicao_django', 'lancar_contribuicao_manual_rateada_django')
            """
        ).fetchone()[0]
        rule = conn.execute(
            """
            SELECT r.*, tc.codigo AS tipo_codigo, pc.codigo AS conta_codigo, ca.nome AS campanha_nome
              FROM pix_centavo_regras r
              LEFT JOIN tipos_contribuicao tc ON tc.id = r.tipo_contribuicao_id
              LEFT JOIN plano_contas pc ON pc.id = r.plano_conta_id
              LEFT JOIN campanhas ca ON ca.id = r.campanha_id
             WHERE r.id = ?
            """,
            (rule_id,),
        ).fetchone()
        receipt = conn.execute(
            "SELECT valor_total, status FROM recibos WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        receipt_items = conn.execute(
            "SELECT COUNT(*) FROM recibo_itens WHERE recibo_id = ?",
            (receipt_id,),
        ).fetchone()[0]
        receipt_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE tabela = 'recibos'
               AND registro_id = ?
               AND acao = 'gerar_recibo_django'
            """,
            (receipt_id,),
        ).fetchone()[0]
        linked_contributor = conn.execute(
            "SELECT pessoa_id FROM contribuintes WHERE id = ?",
            (linked_contributor_id,),
        ).fetchone()
        frequentador = conn.execute(
            "SELECT nome, status FROM pessoas WHERE id = ?",
            (frequentador_person_id,),
        ).fetchone()
        frequentador_contributor = conn.execute(
            "SELECT pessoa_id FROM contribuintes WHERE id = ?",
            (frequentador_contributor_id,),
        ).fetchone()
        contributor_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE tabela IN ('contribuintes', 'pessoas')
               AND acao IN ('vincular_contribuinte_pessoa', 'criar_frequentador_por_contribuinte_django')
            """
        ).fetchone()[0]
        family_relationship = conn.execute(
            """
            SELECT pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo
              FROM pessoa_relacionamentos
             WHERE id = ?
            """,
            (relationship_id,),
        ).fetchone()
        family_audit = conn.execute(
            """
            SELECT COUNT(*)
              FROM auditoria
             WHERE tabela = 'pessoa_relacionamentos'
               AND registro_id = ?
               AND acao IN ('criar_nucleo_familiar_endereco_django', 'atualizar_vinculo_familiar_django')
            """,
            (relationship_id,),
        ).fetchone()[0]
        trash = conn.execute(
            """
            SELECT pessoa_id, operador, restaurado, snapshot_json
              FROM pessoas_lixeira_segura
             WHERE id = ?
            """,
            (trash_id,),
        ).fetchone()
        purged_person = conn.execute("SELECT id FROM pessoas WHERE id = ?", (purged_person_id,)).fetchone()
        purged_trash = conn.execute("SELECT id FROM pessoas_lixeira_segura WHERE id = ?", (purge_trash_id,)).fetchone()
        purge_tombstone = conn.execute(
            """
            SELECT pessoa_id_original, lixeira_id, tombstone_json
              FROM pessoas_purga_segura
             WHERE pessoa_id_original = ?
            """,
            (purged_person_id,),
        ).fetchone()
        blocked_trash = conn.execute(
            "SELECT id FROM pessoas_lixeira_segura WHERE id = ?",
            (blocked_trash_id,),
        ).fetchone()
    finally:
        conn.close()
    if people != 5 or active_people != 3:
        raise AssertionError(f"Esperado 5 pessoas totais e 3 ativas no banco temporario, retornou {people}/{active_people}.")
    if audit < 2:
        raise AssertionError(f"Esperado auditoria de criacao e edicao, retornou {audit}.")
    if contacts < 1:
        raise AssertionError("Contato principal nao foi sincronizado.")
    if contributors < 1:
        raise AssertionError("Identidade financeira basica nao foi sincronizada.")
    if contribution is None:
        raise AssertionError("Contribuicao manual criada pelo Django nao foi encontrada.")
    if round(float(contribution["valor"] or 0), 2) != 100.0 or contribution["status_operacional"] != "regular":
        raise AssertionError("Rateio da contribuicao original nao persistiu valor/status esperados.")
    if contribution_audit < 2:
        raise AssertionError("Criacao e ajuste manual da contribuicao nao geraram auditoria suficiente.")
    if len(manual_ids) != 2 or round(float(manual_total or 0), 2) != 100.0:
        raise AssertionError("Lancamento manual rateado nao fechou duas linhas somando 100,00.")
    if envelope is None:
        raise AssertionError("Envelope manual com imagem nao foi encontrado.")
    if round(float(envelope["total_informado"] or 0), 2) != 454.0 or round(float(envelope_total or 0), 2) != 454.0:
        raise AssertionError("Envelope manual nao preservou total informado e soma das contribuicoes.")
    if envelope_item_count != 3 or len(envelope_result["contribution_ids"]) != 3:
        raise AssertionError("Envelope manual nao criou exatamente tres linhas vinculadas.")
    if (
        envelope["rastreio_forma_identificada"] != "cheque"
        or envelope["rastreio_numero_cheque"] != "000123"
        or envelope["rastreio_numero_operacao"] != "OP-ENVELOPE-454"
        or envelope["rastreio_nsu_tid"] != "NSU123456"
        or envelope["rastreio_ultimos_digitos_cartao"] != "9876"
        or envelope["rastreio_data_operacao"] != "2026-05-12"
        or round(float(envelope["rastreio_valor_operacao"] or 0), 2) != 454.0
        or envelope["rastreio_status_conciliacao"] != "pendente"
    ):
        raise AssertionError("Envelope manual nao preservou rastreabilidade financeira de cheque/cartao.")
    if simple_envelope is None:
        raise AssertionError("Envelope simples sem rateio nao foi encontrado.")
    if round(float(simple_envelope["total_informado"] or 0), 2) != 25.0 or round(float(simple_envelope["total_linhas"] or 0), 2) != 25.0:
        raise AssertionError("Envelope simples sem rateio nao preservou total principal.")
    if len(simple_envelope_item) != 1:
        raise AssertionError("Envelope simples deveria criar exatamente uma contribuicao principal.")
    simple_item = simple_envelope_item[0]
    if (
        round(float(simple_item["valor"] or 0), 2) != 25.0
        or round(float(simple_item["contribuicao_valor"] or 0), 2) != 25.0
        or int(simple_item["pessoa_id"] or 0) != person_id
        or int(simple_item["tipo_contribuicao_id"] or 0) != 1
    ):
        raise AssertionError("Envelope simples nao usou pessoa principal e tipo principal corretamente.")
    if (
        editable_envelope is None
        or editable_envelope["status"] != "lancado"
        or editable_envelope["data_recebimento"] != "2026-05-16"
        or round(float(editable_envelope["total_informado"] or 0), 2) != 80.0
        or round(float(editable_envelope["total_linhas"] or 0), 2) != 80.0
    ):
        raise AssertionError("Edicao segura de envelope nao atualizou cabecalho/data/total.")
    if (
        int(editable_active_items["qtd"] or 0) != 2
        or round(float(editable_active_items["total"] or 0), 2) != 80.0
        or int(editable_old_active or 0) != 0
        or len(editable_update_result["contribution_ids"]) != 2
    ):
        raise AssertionError("Edicao segura de envelope nao recriou linhas ativas ou nao desativou a versao anterior.")
    if editable_audit < 4:
        raise AssertionError("Edicao segura de envelope nao gerou auditoria suficiente.")
    if contribution_count_after_batch_lot != contributions_before_batch_lot + 1:
        raise AssertionError("Fluxo de lote de envelopes deveria criar contribuicao somente no envelope lancado.")
    if (
        batch_lot_summary is None
        or int(batch_lot_summary["total"] or 0) != 2
        or int(batch_lot_summary["lancados"] or 0) != 1
        or int(batch_lot_summary["ignorados"] or 0) != 1
        or int(batch_lot_summary["pendentes"] or 0) != 0
        or round(float(batch_lot_summary["total_lancado"] or 0), 2) != 40.0
    ):
        raise AssertionError("Resumo do lote de envelopes nao refletiu lancado/ignorado/total corretamente.")
    if (
        batch_launched_envelope is None
        or batch_launched_envelope["status"] != "lancado"
        or batch_launched_envelope["data_recebimento"] != "2026-05-14"
        or batch_launched_envelope["data_contribuicao"] != "2026-05-14"
        or round(float(batch_launched_envelope["valor_contribuicao"] or 0), 2) != 40.0
        or batch_launched_envelope["rastreio_forma_identificada"] != "cartao_credito"
        or batch_launched_envelope["rastreio_numero_operacao"] != "AUT-40"
        or batch_launched_envelope["rastreio_nsu_tid"] != "TID40"
        or round(float(batch_launched_envelope["rastreio_valor_operacao"] or 0), 2) != 40.0
    ):
        raise AssertionError("Envelope pendente lancado nao preservou data individual, valor e rastreabilidade digitados.")
    if int(batch_ignored_contributions or 0) != 0:
        raise AssertionError("Envelope ignorado entrou indevidamente no financeiro.")
    if profile_update_count < 1:
        raise AssertionError("Envelope com telefone/endereco diferente nao gerou pendencia cadastral sugerida.")
    envelope_path = Path(str(envelope["caminho_imagem"] or ""))
    if not envelope_path.exists() or not str(envelope_path).startswith(str(envelope_temp_dir)):
        raise AssertionError("Imagem do envelope nao foi arquivada na pasta temporaria esperada.")
    simple_envelope_path = Path(str(simple_envelope["caminho_imagem"] or ""))
    if not simple_envelope_path.exists() or not str(simple_envelope_path).startswith(str(envelope_temp_dir)):
        raise AssertionError("Caminho local de envelope nao foi copiado para a pasta de auditoria.")
    if f"{int(envelope['lote_id']):06d}" not in str(envelope_path) or "202605_maio_26" not in str(envelope_path):
        raise AssertionError("Imagem do envelope nao foi separada por competencia e lote.")
    if not str(envelope["imagem_hash"] or ""):
        raise AssertionError("Envelope manual nao gravou hash da imagem.")
    if envelope_audit < 6:
        raise AssertionError("Envelope manual nao gerou auditoria para envelope e linhas.")
    if len(split_ids) != 2 or round(float(split_total or 0), 2) != 150.0:
        raise AssertionError("Rateio de contribuicao existente nao preservou soma original de 150,00.")
    if split_audit < 3:
        raise AssertionError("Rateio/lancamento manual assistido nao gerou auditoria suficiente.")
    if rule is None:
        raise AssertionError("Regra de centavos criada pelo Django nao foi encontrada.")
    if rule["codigo_centavos"] != "98" or rule["tipo_codigo"] != "CENT_98" or rule["conta_codigo"] != "CENT.98":
        raise AssertionError("Regra de centavos nao sincronizou codigo, tipo e conta corretamente.")
    if rule["campanha_nome"] != "Teste Centavos Django":
        raise AssertionError("Regra de centavos nao sincronizou campanha automaticamente.")
    if receipt is None:
        raise AssertionError("Recibo criado pelo Django nao foi encontrado.")
    if round(float(receipt["valor_total"] or 0), 2) != 100.0 or receipt["status"] != "emitido":
        raise AssertionError("Recibo criado pelo Django nao persistiu total/status esperados.")
    if receipt_items != 1:
        raise AssertionError("Recibo criado pelo Django nao vinculou exatamente uma contribuicao.")
    if receipt_audit < 1:
        raise AssertionError("Recibo criado pelo Django nao gerou auditoria.")
    if linked_contributor is None or int(linked_contributor["pessoa_id"] or 0) != person_id:
        raise AssertionError("Vinculo de contribuinte a pessoa pelo Django nao persistiu.")
    if frequentador is None or frequentador["status"] != "frequentador":
        raise AssertionError("Criacao de frequentador pelo contribuinte nao persistiu ficha correta.")
    if frequentador_contributor is None or int(frequentador_contributor["pessoa_id"] or 0) != frequentador_person_id:
        raise AssertionError("Frequentador criado nao ficou vinculado ao contribuinte de origem.")
    if contributor_audit < 3:
        raise AssertionError("Vinculo/criacao de frequentador nao gerou auditoria suficiente.")
    relationship_pair = {
        int(family_relationship["pessoa_id"] or 0),
        int(family_relationship["pessoa_relacionada_id"] or 0),
    } if family_relationship is not None else set()
    if (
        family_relationship is None
        or relationship_pair != {person_id, frequentador_person_id}
        or family_relationship["tipo_relacionamento"] != "familia_estendida"
        or int(family_relationship["ativo"] or 0) != 1
    ):
        raise AssertionError("Relacao familiar automatica/modificada pelo Django nao persistiu corretamente.")
    if "modificacao" not in str(family_relationship["observacoes"] or ""):
        raise AssertionError("Modificacao manual da relacao familiar nao persistiu observacao.")
    if family_audit < 2:
        raise AssertionError("Relacao familiar automatica/modificada pelo Django nao gerou auditoria suficiente.")
    if (
        trash is None
        or int(trash["pessoa_id"] or 0) != delete_candidate_id
        or int(trash["restaurado"] or 0) != 0
        or "Pessoa Para Lixeira Segura" not in str(trash["snapshot_json"] or "")
    ):
        raise AssertionError("Lixeira segura nao preservou snapshot da ficha excluida.")
    if purged_person is not None or purged_trash is not None:
        raise AssertionError("Purga segura nao removeu a ficha e a entrada original da lixeira.")
    if (
        purge_tombstone is None
        or int(purge_tombstone["pessoa_id_original"] or 0) != purged_person_id
        or int(purge_tombstone["lixeira_id"] or 0) != purge_trash_id
        or "nome_hash" not in str(purge_tombstone["tombstone_json"] or "")
    ):
        raise AssertionError("Purga segura nao preservou tombstone minimo.")
    if blocked_trash is None:
        raise AssertionError("Purga bloqueada removeu indevidamente a lixeira da pessoa com contribuicao.")
    photo_files = sorted(photo_temp_dir.rglob(f"membro_{person_id:06d}__*"))
    if len(photo_files) != 1 or photo_files[0].name != updated_photo.name or "__id_" not in photo_files[0].name:
        raise AssertionError("Upload de foto nao preservou apenas a versao atual da pessoa.")
    deactivate_person_relationship(person_id, relationship_id, actor="verificador")
    conn = sqlite3.connect(temp_db)
    try:
        inactive = conn.execute("SELECT ativo FROM pessoa_relacionamentos WHERE id = ?", (relationship_id,)).fetchone()[0]
    finally:
        conn.close()
    if int(inactive or 0) != 0:
        raise AssertionError("Remocao operacional da relacao familiar nao persistiu.")
    print(
        "OK escrita Django em banco temporario: "
        f"pessoas={people}, ativas={active_people}, auditoria={audit}, contatos={contacts}, contribuintes={contributors}, "
        f"contribuicao={contribution_id}, regra_centavos={rule_id}, recibo={receipt_id}, "
        f"manual_rateado={len(manual_ids)}, split={len(split_ids)}, "
        f"envelope={envelope_result['envelope_id']}, envelope_linhas={len(envelope_result['contribution_ids'])}, "
        f"envelope_editado={editable_envelope_result['envelope_id']}, envelope_edit_linhas={len(editable_update_result['contribution_ids'])}, "
        f"envelope_simples={simple_envelope_result['envelope_id']}, lote_envelopes={envelope_lot_result['lot_id']}, "
        f"lote_caminho_local={local_path_lot_result['lot_id']}, "
        f"envelope_pendente_lancado={launched_pending_result['envelope_id']}, rastreabilidade_envelope=1, pendencias_cadastrais={profile_update_count}, "
        f"vinculo_contribuinte={linked_contributor_id}, frequentador={frequentador_person_id}, "
        f"vinculo_familiar={relationship_id}, foto=1, lixeira={trash_id}, purga={purge_trash_id}"
    )
    print(f"Banco temporario: {temp_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
