from __future__ import annotations

import mimetypes
import os
import re
import threading
import unicodedata
from pathlib import Path

from django.conf import settings


PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")
PHOTO_MAX_BYTES = 8 * 1024 * 1024
_PHOTO_INDEX_LOCK = threading.Lock()
_PHOTO_INDEX_CACHE: dict[Path, tuple[int | None, tuple[Path, ...], set[str]]] = {}


class PhotoUploadError(ValueError):
    """Raised when a person photo upload is invalid."""


def photo_dir() -> Path:
    override = str(os.environ.get("POWER_CHURCH_PHOTO_DIR") or "").strip()
    if override:
        return Path(override)
    return settings.REPO_ROOT / "data" / "fotos_membros"


def member_photo_folder(person_id: int) -> Path:
    person_number = max(0, int(person_id or 0))
    shard = f"{person_number // 1000:04d}"
    return photo_dir() / shard


def slugify_filename_text(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
    return slug or "sem_nome"


def member_photo_stem(person_id: int, cpf: object, name: object) -> str:
    cpf_digits = "".join(ch for ch in str(cpf or "") if ch.isdigit())
    stem = f"membro_{int(person_id):06d}__{slugify_filename_text(name)}__id_{int(person_id):06d}"
    if cpf_digits:
        stem += f"__cpf_{cpf_digits}"
    return stem


def member_photo_example_filename(person_id: int, cpf: object, name: object, extension: str = ".jpg") -> str:
    return f"{member_photo_stem(person_id, cpf, name)}{extension}"


def _folder_signature(folder: Path) -> int | None:
    try:
        return folder.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _folder_photo_index(folder: Path) -> tuple[Path, ...]:
    signature = _folder_signature(folder)
    with _PHOTO_INDEX_LOCK:
        cached = _PHOTO_INDEX_CACHE.get(folder)
        if cached and cached[0] == signature:
            return cached[1]
    if signature is None:
        entries: tuple[Path, ...] = ()
    else:
        entries = tuple(
            sorted(
                match
                for match in folder.iterdir()
                if match.is_file() and match.suffix.lower() in PHOTO_EXTENSIONS
            )
        )
    names = {entry.name for entry in entries}
    with _PHOTO_INDEX_LOCK:
        _PHOTO_INDEX_CACHE[folder] = (signature, entries, names)
    return entries


def _folder_has_named_photo(folder: Path, filename: str) -> bool:
    signature = _folder_signature(folder)
    with _PHOTO_INDEX_LOCK:
        cached = _PHOTO_INDEX_CACHE.get(folder)
        if cached and cached[0] == signature:
            return filename in cached[2]
    _folder_photo_index(folder)
    with _PHOTO_INDEX_LOCK:
        cached = _PHOTO_INDEX_CACHE.get(folder)
        return bool(cached and filename in cached[2])


def _invalidate_photo_folder(folder: Path) -> None:
    with _PHOTO_INDEX_LOCK:
        _PHOTO_INDEX_CACHE.pop(folder, None)


def find_member_photo(person_id: int, cpf: object, name: object) -> Path | None:
    stem = member_photo_stem(person_id, cpf, name)
    for folder in (member_photo_folder(person_id), photo_dir()):
        for extension in PHOTO_EXTENSIONS:
            candidate = folder / f"{stem}{extension}"
            if _folder_has_named_photo(folder, candidate.name):
                return candidate
        prefix = f"membro_{int(person_id):06d}__"
        for match in _folder_photo_index(folder):
            if match.name.startswith(prefix):
                return match
    return None


def member_photo_url(person_id: int, cpf: object, name: object) -> str:
    return f"/people/photo/{int(person_id)}/" if find_member_photo(person_id, cpf, name) else ""


def photo_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def list_member_photo_variants(person_id: int) -> list[Path]:
    matches: list[Path] = []
    seen: set[Path] = set()
    for folder in (member_photo_folder(person_id), photo_dir()):
        prefix = f"membro_{int(person_id):06d}__"
        for match in _folder_photo_index(folder):
            if match.name.startswith(prefix) and match not in seen:
                matches.append(match)
                seen.add(match)
    return matches


def detect_photo_extension(filename: str, content_type: str = "") -> str:
    extension = Path(str(filename or "")).suffix.lower()
    if extension in PHOTO_EXTENSIONS:
        return extension
    by_content_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    guessed = by_content_type.get(str(content_type or "").lower(), "")
    if guessed in PHOTO_EXTENSIONS:
        return guessed
    raise PhotoUploadError("Formato de foto nao suportado. Use JPG, JPEG, PNG, WEBP, GIF, HEIC ou HEIF.")


def uploaded_photo_payload(upload: object | None) -> dict[str, object] | None:
    if upload is None:
        return None
    filename = str(getattr(upload, "name", "") or "")
    content_type = str(getattr(upload, "content_type", "") or "")
    chunks = getattr(upload, "chunks", None)
    payload = b"".join(chunks()) if chunks else bytes(upload)  # type: ignore[arg-type]
    if not payload:
        raise PhotoUploadError("Selecione um arquivo de foto antes de enviar.")
    if len(payload) > PHOTO_MAX_BYTES:
        raise PhotoUploadError("A foto excede o limite de 8 MB.")
    extension = detect_photo_extension(filename, content_type)
    return {
        "filename": filename,
        "content_type": content_type,
        "payload": payload,
        "extension": extension,
        "size": len(payload),
    }


def save_member_photo_payload(
    person_id: int,
    cpf: object,
    name: object,
    photo_payload: dict[str, object] | None,
) -> Path | None:
    if photo_payload is None:
        return None
    payload = bytes(photo_payload.get("payload") or b"")
    extension = str(photo_payload.get("extension") or "")
    if not payload:
        raise PhotoUploadError("Selecione um arquivo de foto antes de enviar.")
    if extension not in PHOTO_EXTENSIONS:
        raise PhotoUploadError("Formato de foto nao suportado.")
    folder = member_photo_folder(person_id)
    folder.mkdir(parents=True, exist_ok=True)
    for existing in list_member_photo_variants(person_id):
        existing.unlink(missing_ok=True)
    target = folder / member_photo_example_filename(person_id, cpf, name, extension)
    target.write_bytes(payload)
    _invalidate_photo_folder(folder)
    _invalidate_photo_folder(photo_dir())
    return target
