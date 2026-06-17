"""CLI: python -m etl.run <stage> --client <name> [--dry-run]"""
from __future__ import annotations
import argparse, sys
from .common import load_config
from .assess import run_assessment
from .normalize import run_normalize
from .import_odoo import run_import


def main():
    ap = argparse.ArgumentParser(description="QuickBooks -> Odoo migration ETL")
    ap.add_argument("stage", choices=["assess", "normalize", "import", "all"])
    ap.add_argument("--client", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.client)

    if a.stage in ("assess", "all"):
        r = run_assessment(a.client, cfg)
        print(f"[ASSESS] report={r['report']}")
        print(f"         blocking={r['blocking']} warnings={r['warnings']} "
              f"clean={r['clean']}")
        print(f"         counts={r['counts']}")
        if a.stage == "all" and not r["clean"]:
            print("Blocking issues present. Fix source data (Stage 2) and re-run.")
            sys.exit(1)

    if a.stage in ("normalize", "all"):
        w = run_normalize(a.client, cfg)
        for e, info in w.items():
            print(f"[NORMALIZE] {e}: {info['rows']} rows -> {info['path']}")

    if a.stage in ("import", "all"):
        r = run_import(a.client, cfg, dry_run=a.dry_run)
        print(f"[IMPORT] dry_run={r['dry_run']} log={r['log']}")
        print(f"         reconciliation={r['reconciliation']} all_match={r['all_match']}")


if __name__ == "__main__":
    main()
