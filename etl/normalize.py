"""Stage 4: Data Normalization for Import.
Transforms canonical data into Odoo-import-ready CSVs (one per entity),
using Odoo column names / external IDs so they load via UI import or API.
"""
from __future__ import annotations
import re
import pandas as pd
from .common import read_source, apply_mapping, out_path, parse_num_default, parse_date


def _slug(v):
    return re.sub(r"[^a-z0-9]+", "_", str(v).strip().lower()).strip("_")


def norm_coa(client, cfg):
    raw = read_source(client, cfg, "chart_of_accounts")
    if raw is None:
        return None
    df = apply_mapping(raw, cfg, "chart_of_accounts")
    tmap = cfg.get("account_type_map", {})
    out = pd.DataFrame({
        "id": df["code"].apply(lambda c: f"acc_{_slug(c)}"),   # external id
        "code": df["code"],
        "name": df["name"],
        "account_type": df["qb_type"].map(tmap),
    })
    return out


def norm_partners(client, cfg):
    raw = read_source(client, cfg, "partners")
    if raw is None:
        return None
    df = apply_mapping(raw, cfg, "partners")
    rel = df.get("relation", pd.Series([""] * len(df))).fillna("").str.lower()
    out = pd.DataFrame({
        "id": df["name"].apply(lambda n: f"partner_{_slug(n)}"),
        "name": df["name"],
        "is_company": True,
        "customer_rank": rel.apply(lambda r: 1 if r in ("customer", "both") else 0),
        "supplier_rank": rel.apply(lambda r: 1 if r in ("vendor", "both") else 0),
        "email": df.get("email"),
        "phone": df.get("phone"),
        "street": df.get("street"),
        "vat": df.get("vat"),
    })
    return out


def _norm_docs(client, cfg, entity, move_type):
    raw = read_source(client, cfg, entity)
    if raw is None:
        return None
    df = apply_mapping(raw, cfg, entity)
    out = pd.DataFrame({
        "id": [f"{entity[:3]}_{i}" for i in range(len(df))],
        "move_type": move_type,
        "partner_id/id": df["partner"].apply(lambda n: f"partner_{_slug(n)}"),
        "ref": df.get("ref"),
        "invoice_date": df["invoice_date"].apply(parse_date),
        "invoice_date_due": df.get("date_due", pd.Series([None]*len(df))).apply(parse_date),
        "amount_total": df["amount_total"].apply(parse_num_default),
        "amount_residual": df.get("amount_residual",
                                   df["amount_total"]).apply(parse_num_default),
    })
    return out


def norm_invoices(client, cfg):
    return _norm_docs(client, cfg, "invoices", "out_invoice")


def norm_bills(client, cfg):
    return _norm_docs(client, cfg, "bills", "in_invoice")


def build_opening_balance(client, cfg):
    """One balanced journal entry from the trial balance, as of cutoff."""
    raw = read_source(client, cfg, "trial_balance")
    if raw is None:
        return None
    raw = raw.rename(columns={c: c.strip() for c in raw.columns})
    df = apply_mapping(raw, cfg, "trial_balance")
    lines = []
    for _, r in df.iterrows():
        code = r.get("code")
        debit = parse_num_default(r.get("debit"), 0.0)
        credit = parse_num_default(r.get("credit"), 0.0)
        if pd.isna(code) or not str(code).strip() or (debit == 0 and credit == 0):
            continue
        lines.append({"account_id/id": f"acc_{_slug(code)}",
                      "debit": debit, "credit": credit})
    df = pd.DataFrame(lines)
    if df.empty:
        return None
    diff = round(df["debit"].sum() - df["credit"].sum(), 2)
    if abs(diff) > 0.005:    # balance against suspense account
        ob_acc = cfg.get("opening_balance_account")
        df = pd.concat([df, pd.DataFrame([{
            "account_id/id": f"acc_{_slug(ob_acc)}",
            "debit": -diff if diff < 0 else 0.0,
            "credit": diff if diff > 0 else 0.0}])], ignore_index=True)
    df.insert(0, "date", cfg.get("cutoff_date"))
    df.insert(0, "ref", "Opening Balance Migration")
    return df


NORMALIZERS = {
    "chart_of_accounts": norm_coa,
    "partners": norm_partners,
    "invoices": norm_invoices,
    "bills": norm_bills,
    "opening_balance": build_opening_balance,
}


def run_normalize(client: str, cfg: dict) -> dict:
    written = {}
    for entity, fn in NORMALIZERS.items():
        out = fn(client, cfg)
        if out is None or out.empty:
            continue
        p = out_path(client, "normalized", f"{entity}.csv")
        out.to_csv(p, index=False)
        written[entity] = {"path": str(p), "rows": len(out)}
    return written
