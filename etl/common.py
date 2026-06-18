"""Config loading, path resolution, and CSV/Excel IO helpers."""
from __future__ import annotations
import os, re
from datetime import datetime
from pathlib import Path
import yaml
import pandas as pd

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y")


def parse_num(v):
    """Return a float, or None if the value is not numeric."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def parse_num_default(v, default=0.0):
    """Return a rounded float, using default when the value is not numeric."""
    n = parse_num(v)
    return round(n, 2) if n is not None else default


def parse_date(v):
    """Return an ISO date string, or empty string if unparseable."""
    if v is None or pd.isna(v):
        return ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(v).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def date_valid(v):
    """Return True if the value is a parseable date."""
    return bool(parse_date(v))

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
    df = pd.read_csv(p, dtype=str, keep_default_na=False).replace("", pd.NA)
    return df


def apply_mapping(df: pd.DataFrame, cfg: dict, entity: str) -> pd.DataFrame:
    """Rename QB columns -> canonical fields; keep only mapped columns."""
    m = cfg.get("mappings", {}).get(entity, {})
    present = {qb: canon for qb, canon in m.items() if qb in df.columns}
    out = df[list(present.keys())].rename(columns=present).copy()
    return out
