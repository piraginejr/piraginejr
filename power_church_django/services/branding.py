from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings


BRAND_LOGO_URL = "/branding/logo"


def brand_logo_path() -> Path:
    return Path(settings.POWER_CHURCH_BRAND_LOGO_PATH)


def brand_logo_available() -> bool:
    return brand_logo_path().exists()


def branding_context(_request: object) -> dict[str, Any]:
    return {
        "brand_logo_available": brand_logo_available(),
        "brand_logo_url": BRAND_LOGO_URL,
        "brand_title": "Primeira Igreja Batista de Niterói",
        "brand_subtitle": "Gestão e contribuições",
    }
