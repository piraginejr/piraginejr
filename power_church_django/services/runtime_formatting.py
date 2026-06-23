from __future__ import annotations

from power_church_core.formatting import br_money


STATUS_LABELS = {
    "membro_ativo": "Membro ativo",
    "membro_inativo": "Membro inativo",
    "frequentador": "Frequentador",
    "visitante": "Visitante",
    "arquivo_morto": "Arquivo morto",
}


STATUS_SIGLAS = {
    "membro_ativo": "SA",
    "membro_inativo": "SI",
    "frequentador": "NF",
    "visitante": "NV",
    "arquivo_morto": "NM",
}


CONTRIBUTION_STATUS_OPTIONS = {
    "classificacao_pendente",
    "duplicidade_suspeita",
    "em_saneamento",
    "regular",
    "revisar_destinacao",
    "sem_associacao",
}


HUMAN_PENDING_REVIEW_STATUSES = (
    "pendente",
    "revisar_pessoa",
    "revisar_destinacao",
    "classificacao_pendente",
)


def human_pending_review_sql(alias: str = "m") -> str:
    direct_statuses = ",".join(f"'{value}'" for value in HUMAN_PENDING_REVIEW_STATUSES)
    return (
        f"({alias}.review_status IN ({direct_statuses}) "
        f"OR ({alias}.review_status = 'revisar_duplicidade' AND COALESCE({alias}.imported_contribution_id, 0) = 0))"
    )


def format_status(status: object) -> str:
    text = str(status or "").strip()
    return STATUS_LABELS.get(text, text.replace("_", " ").title() if text else "Sem status")


def status_sigla(status: object, has_person: bool = True) -> str:
    if not has_person:
        return "NR"
    return STATUS_SIGLAS.get(str(status or "").strip(), "NR")


def _money(value: object) -> str:
    return br_money(value)


def _clean_optional_text(value: object) -> str:
    return str(value or "").strip()
