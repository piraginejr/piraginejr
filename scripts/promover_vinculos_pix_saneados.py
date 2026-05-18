from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
APP_PATH = ROOT / "power_church_demo.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--lot", type=int, default=0)
    args = parser.parse_args()

    app = load_app_module()
    db = app.PowerChurchDB(Path(args.db))
    try:
        promoted_linked = db.promote_linked_pix_sem_associacao(args.lot)
        promoted_strong = db.promote_strong_pix_sem_associacao(args.lot)
        db.conn.commit()
        print({"lot_id": args.lot or "all", "promoted_linked": promoted_linked, "promoted_strong": promoted_strong})
    finally:
        db.close()


if __name__ == "__main__":
    main()
