from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django  # noqa: E402

django.setup()

from power_church_django.services.legacy_bank_write import close_bank_lot, create_statement_lot_from_upload  # noqa: E402


FILE_PATH = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/SANTANDER_Maio2026.pdf")


def main() -> int:
    print("START", flush=True)
    lot_id = create_statement_lot_from_upload(
        FILE_PATH.name,
        FILE_PATH.read_bytes(),
        layout_code="SANTANDER_AUTO",
        pdf_provider_mode="swift_pdfkit",
    )
    print(f"LOT {lot_id}", flush=True)
    result = close_bank_lot("statement", lot_id, actor="lancamento_extrato_santander")
    print(f"RESULT {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
