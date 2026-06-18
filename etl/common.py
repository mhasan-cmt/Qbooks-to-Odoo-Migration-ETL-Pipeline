"""Config loading, path resolution, and CSV/Excel IO helpers."""
from __future__ import annotations
import os, re
from pathlib import Path
import yaml
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ENTITY_ORDER = [
    "chart_of_accounts", "partners", "products",
    "invoices", "bills", "opening_balance", "payments",
]


def _expand_env(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(client: str) -> dict:
    path = ROOT / "configs" / f"{client}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No config at {path}")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["odoo"] = _expand_env(cfg.get("odoo", {}))
    cfg["_client"] = client
    return cfg


def client_dir(client: str) -> Path:
    return ROOT / "clients" / client


def raw_path(client: str, filename: str) -> Path:
    return client_dir(client) / "01_raw" / filename


def out_path(client: str, stage: str, filename: str) -> Path:
    sub = {"assessment": "02_assessment", "normalized": "03_normalized",
           "import": "04_import_logs"}[stage]
    d = client_dir(client) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d / filename


def read_source(client: str, cfg: dict, entity: str) -> pd.DataFrame | None:
    src = cfg.get("sources", {}).get(entity)
    if not src:
        return None
    p = raw_path(client, src)
    if not p.exists():
        return None
    
    # Detect file type and read accordingly
    if p.suffix.lower() in ['.xlsx', '.xls']:
        # Excel file
        df = pd.read_excel(p, dtype=str)
    else:
        # CSV file - try multiple encodings
        encodings = [None, 'latin-1', 'cp1252', 'utf-8-sig']
        df = None
        for enc in encodings:
            try:
                if enc is None:
                    df = pd.read_csv(p, dtype=str, keep_default_na=False).replace("", pd.NA)
                else:
                    df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding=enc).replace("", pd.NA)
                return df
            except (UnicodeDecodeError, LookupError):
                continue
            except Exception as e:
                print(f"Error reading {entity} from {p}: {e}")
                raise
        # Last resort: read with error replacement
        df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding='utf-8', errors='replace').replace("", pd.NA)
    
    return df


def apply_mapping(df: pd.DataFrame, cfg: dict, entity: str) -> pd.DataFrame:
    """Rename QB columns -> canonical fields; keep only mapped columns."""
    m = cfg.get("mappings", {}).get(entity, {})
    present = {qb: canon for qb, canon in m.items() if qb in df.columns}
    out = df[list(present.keys())].rename(columns=present).copy()
    return out
