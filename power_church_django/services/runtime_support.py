from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings

from power_church_core.normalization import normalize_match_name, normalize_query


PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "hotmail.com.br",
    "outlook.com",
    "outlook.com.br",
    "live.com",
    "live.com.br",
    "msn.com",
    "yahoo.com",
    "yahoo.com.br",
    "icloud.com",
    "me.com",
    "mac.com",
    "uol.com.br",
    "bol.com.br",
    "terra.com.br",
    "globo.com",
    "globomail.com",
    "proton.me",
    "protonmail.com",
}

EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def envelope_upload_root() -> Path:
    configured = os.environ.get("POWER_CHURCH_ENVELOPE_DIR")
    if configured:
        return Path(configured)
    return Path(settings.POWER_CHURCH_LEGACY_DB_PATH).resolve().parent / "envelope_uploads"


def _manual_email_or_blank(value: object) -> str:
    email = normalize_query(value).lower()
    if not email:
        return ""
    if len(email) > 254 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        return ""
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
        return ""
    return email


def split_email_candidates(value: object) -> list[str]:
    raw = normalize_query(value)
    if not raw:
        return []
    prepared = raw.replace("\n", ";")
    prepared = re.sub(r"\s+(?:e|ou)\s+", ";", prepared, flags=re.IGNORECASE)
    prepared = prepared.replace(",", ";")
    tokens = [normalize_query(chunk).lower() for chunk in prepared.split(";")]
    emails: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        candidate = _manual_email_or_blank(token)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        emails.append(candidate)
    return emails


def _email_candidate_score(email: str, person_name: object = "") -> tuple[int, int, int, str]:
    local, _, domain = email.partition("@")
    score = 100 if domain in PERSONAL_EMAIL_DOMAINS else 40
    normalized_name = normalize_match_name(person_name)
    tokens = [token.lower() for token in normalized_name.split() if len(token) >= 3]
    local_compact = re.sub(r"[^a-z0-9]+", "", local.lower())
    matches = 0
    for token in tokens:
        if token in local_compact:
            score += 12
            matches += 1
    if tokens:
        first = tokens[0]
        last = tokens[-1]
        if first in local_compact:
            score += 8
        if last in local_compact:
            score += 10
        if local_compact.startswith(first[:1]) and last in local_compact:
            score += 6
    return (score, matches, -len(local_compact), email)


def preferred_delivery_email(value: object, person_name: object = "") -> str:
    emails = split_email_candidates(value)
    if not emails:
        return ""
    ranked = sorted(emails, key=lambda email: _email_candidate_score(email, person_name), reverse=True)
    return ranked[0] if ranked else ""
