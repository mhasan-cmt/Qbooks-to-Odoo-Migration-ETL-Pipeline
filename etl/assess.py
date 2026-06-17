"""Stage 1 & 3: Data Assessment.
Runs validation rules per entity, emits an issues report.
A clean run (zero blocking issues) = Stage 3 'clean report'.
"""
from __future__ import annotations
import re
from datetime import datetime
import pandas as pd
from .common import read_source, apply_mapping, out_path, ENTITY_ORDER

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _add(issues, entity, row_idx, ref, severity, field, message):
    issues.append({
        "entity": entity, "row": row_idx, "ref": ref,
        "severity": severity, "field": field, "issue": message,
    })


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


def _date_ok(v):
    if v is None or pd.isna(v):
        return False
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y"):
        try:
            datetime.strptime(str(v).strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def assess_coa(df, cfg, issues):
    e = "chart_of_accounts"
    type_map = cfg.get("account_type_map", {})
    seen = set()
    for i, r in df.iterrows():
        ref = r.get("code") or r.get("name") or i
        if pd.isna(r.get("name")):
            _add(issues, e, i, ref, "BLOCK", "name", "Missing account name")
        code = r.get("code")
        if pd.isna(code):
            _add(issues, e, i, ref, "BLOCK", "code", "Missing account code")
        elif code in seen:
            _add(issues, e, i, ref, "BLOCK", "code", f"Duplicate account code {code}")
        else:
            seen.add(code)
        qbt = r.get("qb_type")
        if pd.isna(qbt):
            _add(issues, e, i, ref, "BLOCK", "qb_type", "Missing account type")
        elif qbt not in type_map:
            _add(issues, e, i, ref, "BLOCK", "qb_type",
                 f"Account type '{qbt}' has no Odoo mapping in config")


def assess_partners(df, cfg, issues):
    e = "partners"
    seen = {}
    for i, r in df.iterrows():
        name = r.get("name")
        ref = name or i
        if pd.isna(name):
            _add(issues, e, i, ref, "BLOCK", "name", "Missing partner name")
        else:
            key = str(name).strip().lower()
            if key in seen:
                _add(issues, e, i, ref, "WARN", "name",
                     f"Possible duplicate of row {seen[key]}")
            seen[key] = i
        rel = r.get("relation")
        if pd.notna(rel) and str(rel).lower() not in ("customer", "vendor", "both"):
            _add(issues, e, i, ref, "WARN", "relation",
                 f"Unrecognized relation '{rel}'")
        email = r.get("email")
        if pd.notna(email) and not EMAIL_RE.match(str(email).strip()):
            _add(issues, e, i, ref, "WARN", "email", f"Invalid email '{email}'")
        ob = r.get("opening_balance")
        if pd.notna(ob) and _num(ob) is None:
            _add(issues, e, i, ref, "BLOCK", "opening_balance",
                 f"Non-numeric opening balance '{ob}'")


def assess_documents(df, cfg, issues, entity):
    """invoices / bills."""
    coa_codes = _coa_codes(cfg)
    partners = _partner_names(cfg)
    for i, r in df.iterrows():
        ref = r.get("ref") or i
        if pd.isna(r.get("partner")):
            _add(issues, entity, i, ref, "BLOCK", "partner", "Missing partner")
        elif partners and str(r.get("partner")).strip().lower() not in partners:
            _add(issues, entity, i, ref, "BLOCK", "partner",
                 f"Partner '{r.get('partner')}' not found in partners file")
        if not _date_ok(r.get("invoice_date")):
            _add(issues, entity, i, ref, "BLOCK", "invoice_date",
                 f"Invalid/missing date '{r.get('invoice_date')}'")
        amt = _num(r.get("amount_total"))
        if amt is None:
            _add(issues, entity, i, ref, "BLOCK", "amount_total",
                 f"Non-numeric amount '{r.get('amount_total')}'")
        ac = r.get("account_code")
        if coa_codes and pd.notna(ac) and str(ac) not in coa_codes:
            _add(issues, entity, i, ref, "WARN", "account_code",
                 f"Account '{ac}' not in chart of accounts")


def _coa_codes(cfg):
    df = read_source(cfg["_client"], cfg, "chart_of_accounts")
    if df is None:
        return set()
    df = apply_mapping(df, cfg, "chart_of_accounts")
    return set(df.get("code", pd.Series(dtype=str)).dropna().astype(str))


def _partner_names(cfg):
    df = read_source(cfg["_client"], cfg, "partners")
    if df is None:
        return set()
    df = apply_mapping(df, cfg, "partners")
    return set(df.get("name", pd.Series(dtype=str)).dropna()
              .astype(str).str.strip().str.lower())


ASSESSORS = {
    "chart_of_accounts": assess_coa,
    "partners": assess_partners,
    "invoices": lambda d, c, i: assess_documents(d, c, i, "invoices"),
    "bills": lambda d, c, i: assess_documents(d, c, i, "bills"),
}


def run_assessment(client: str, cfg: dict) -> dict:
    issues = []
    counts = {}
    for entity in ENTITY_ORDER:
        if entity not in ASSESSORS:
            continue
        raw = read_source(client, cfg, entity)
        if raw is None:
            continue
        df = apply_mapping(raw, cfg, entity)
        counts[entity] = len(df)
        ASSESSORS[entity](df, cfg, issues)

    idf = pd.DataFrame(issues, columns=["entity", "row", "ref", "severity", "field", "issue"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx = out_path(client, "assessment", f"issues_{stamp}.xlsx")
    with pd.ExcelWriter(xlsx) as xw:
        summary = (idf.groupby(["entity", "severity"]).size()
                   .reset_index(name="count") if len(idf)
                   else pd.DataFrame(columns=["entity", "severity", "count"]))
        summary.to_excel(xw, sheet_name="summary", index=False)
        (idf if len(idf) else pd.DataFrame([{"status": "CLEAN - no issues"}])
         ).to_excel(xw, sheet_name="issues", index=False)
        pd.DataFrame([{"entity": k, "record_count": v}
                      for k, v in counts.items()]).to_excel(
            xw, sheet_name="record_counts", index=False)

    blocking = int((idf["severity"] == "BLOCK").sum()) if len(idf) else 0
    warnings = int((idf["severity"] == "WARN").sum()) if len(idf) else 0
    return {"report": str(xlsx), "blocking": blocking,
            "warnings": warnings, "counts": counts, "clean": blocking == 0}
