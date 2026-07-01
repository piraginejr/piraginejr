from __future__ import annotations

import re
import unicodedata

MOJIBAKE_MARKERS = ("Ã", "Â", "�", "├", "┬", "╟", "╢", "╣", "║", "╗", "╝", "╚", "╔", "╩", "╦", "╠", "═", "╬")


def moneyless_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def repair_mojibake_text(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text or not any(marker in text for marker in MOJIBAKE_MARKERS):
        return text
    candidates = [text]
    for encoding in ("cp437", "latin-1", "cp1252"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidates.append(candidate)

    def score(candidate: str) -> tuple[int, int, int, int]:
        suspicious = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
        replacement = candidate.count("\ufffd")
        alpha_penalty = -sum(ch.isalpha() for ch in candidate)
        return (suspicious, replacement, alpha_penalty, len(candidate))

    best = min(candidates, key=score)
    return best if score(best) < score(text) else text


def normalize_query(value: object) -> str:
    return repair_mojibake_text(value)


def normalize_match_name(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9 ]+", " ", raw).upper()
    return re.sub(r"\s+", " ", normalized).strip()


def format_cpf(value: object) -> str:
    cpf = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(cpf) != 11:
        return str(value or "").strip()
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[-2:]}"


def document_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def format_document(value: object) -> str:
    digits = document_digits(value)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[-2:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[-2:]}"
    return normalize_query(value)


def strip_leading_document_fragment(value: object) -> str:
    text = normalize_query(value)
    match = re.match(r"^([0-9][0-9.\-/\s]{2,})(.+)$", text)
    if not match:
        return text
    prefix_digits = document_digits(match.group(1))
    suffix = normalize_query(match.group(2))
    if len(prefix_digits) >= 6 and any(ch.isalpha() for ch in suffix):
        return suffix
    return text


NAME_PARTICLES = {"a", "as", "da", "das", "de", "di", "do", "dos", "e"}


def _title_name_token(token: str, *, first_word: bool = False) -> str:
    if not token:
        return token
    if "-" in token:
        return "-".join(
            _title_name_token(part, first_word=first_word and index == 0)
            for index, part in enumerate(token.split("-"))
        )
    lowered = token.lower()
    if not first_word and lowered in NAME_PARTICLES:
        return lowered
    if not any(ch.isalpha() for ch in lowered):
        return token
    return lowered[:1].upper() + lowered[1:]


def title_case_name(value: object) -> str:
    text = strip_leading_document_fragment(value)
    words = text.split()
    return " ".join(_title_name_token(word, first_word=index == 0) for index, word in enumerate(words))


def is_report_name(value: object) -> bool:
    text = strip_leading_document_fragment(value)
    if not any(ch.isalpha() for ch in text):
        return False
    normalized = normalize_match_name(text)
    return normalized not in {
        "CONTRIBUINTE NAO IDENTIFICADO",
        "CONTRIBUINTE NAO VINCULADO",
        "DOCUMENTO NAO IDENTIFICADO",
        "SEM REMETENTE",
    }


def contribution_report_identity(person_name: object, contributor_name: object, document: object) -> dict[str, str]:
    person_text = normalize_query(person_name)
    if is_report_name(person_text):
        name = title_case_name(person_text)
        return {
            "name": name,
            "sort_key": normalize_match_name(name),
            "group_kind": "nome",
            "group_label": "Contribuintes com nome",
            "document": format_document(document),
            "raw_name": normalize_query(contributor_name),
        }

    contributor_text = normalize_query(contributor_name)
    cleaned_contributor = strip_leading_document_fragment(contributor_text)
    if is_report_name(cleaned_contributor):
        name = title_case_name(cleaned_contributor)
        return {
            "name": name,
            "sort_key": normalize_match_name(name),
            "group_kind": "nome",
            "group_label": "Contribuintes com nome",
            "document": format_document(document),
            "raw_name": contributor_text,
        }

    document_text = format_document(document) or format_document(contributor_text)
    document_text = document_text or "Documento nao identificado"
    return {
        "name": document_text,
        "sort_key": normalize_match_name(document_text) or document_digits(document_text),
        "group_kind": "documento",
        "group_label": "Somente documento/numero",
        "document": document_text,
        "raw_name": contributor_text,
    }


def clean_cpf(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits if len(digits) == 11 else text


def valid_cpf(value: object) -> bool:
    cpf = "".join(ch for ch in str(value or "") if ch.isdigit())
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


def cleaned_document_token(value: object) -> str:
    token = "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "*")
    return token.strip()


def masked_document_matches(masked_value: object, candidate_value: object) -> bool:
    masked = cleaned_document_token(masked_value)
    candidate = cleaned_document_token(candidate_value)
    if not masked or not candidate or len(masked) != len(candidate):
        return False
    return all(left == "*" or right == "*" or left == right for left, right in zip(masked, candidate))


def document_query_matches(query_value: object, candidate_value: object) -> bool:
    query_text = str(query_value or "").strip()
    candidate_text = str(candidate_value or "").strip()
    if not query_text or not candidate_text:
        return False
    if normalize_query(query_text).lower() == normalize_query(candidate_text).lower():
        return True
    if masked_document_matches(query_text, candidate_text) or masked_document_matches(candidate_text, query_text):
        return True
    query_digits = "".join(ch for ch in query_text if ch.isdigit())
    candidate_digits = "".join(ch for ch in candidate_text if ch.isdigit())
    if query_digits and candidate_digits and (query_digits in candidate_digits or candidate_digits in query_digits):
        return True
    return False


def santander_document_type(document_value: object) -> str:
    digits = "".join(ch for ch in str(document_value or "") if ch.isdigit())
    if len(digits) == 14:
        return "cnpj"
    if len(digits) == 11:
        return "cpf"
    return "documento"


def pix_code_from_amount(value: float) -> str:
    cents = int(round(float(value or 0) * 100)) % 100
    return f"{cents:02d}"
