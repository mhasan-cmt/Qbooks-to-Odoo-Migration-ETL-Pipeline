"""Stage 1 & 3: Data Assessment.
Runs validation rules per entity, emits an issues report.
A clean run (zero blocking issues) = Stage 3 'clean report'.
"""
from __future__ import annotations
import re
from datetime import datetime
import pandas as pd
from .common import (
    read_source, apply_mapping, out_path, parse_num, date_valid,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ASSESS_ORDER = ["chart_of_accounts", "partners", "invoices", "bills", "trial_balance"]


def _add(issues, entity, row_idx, ref, severity, field, message):
    issues.append({
        "entity": entity, "row": row_idx, "ref": ref,
        "severity": severity, "field": field, "issue": message,
    })


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
        if pd.notna(ob) and parse_num(ob) is None:
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
        if not date_valid(r.get("invoice_date")):
            _add(issues, entity, i, ref, "BLOCK", "invoice_date",
                 f"Invalid/missing date '{r.get('invoice_date')}'")
        amt = parse_num(r.get("amount_total"))
        if amt is None:
            _add(issues, entity, i, ref, "BLOCK", "amount_total",
                 f"Non-numeric amount '{r.get('amount_total')}'")
        residual = parse_num(r.get("amount_residual"))
        if pd.notna(r.get("amount_residual")) and residual is None:
            _add(issues, entity, i, ref, "BLOCK", "amount_residual",
                 f"Non-numeric open balance '{r.get('amount_residual')}'")
        elif amt is not None and residual is not None and residual > amt + 0.005:
            _add(issues, entity, i, ref, "WARN", "amount_residual",
                 f"Open balance ({residual}) exceeds total amount ({amt})")
        ac = r.get("account_code")
        if coa_codes and pd.notna(ac) and str(ac) not in coa_codes:
            _add(issues, entity, i, ref, "WARN", "account_code",
                 f"Account '{ac}' not in chart of accounts")


def assess_trial_balance(df, cfg, issues):
    e = "trial_balance"
    coa_codes = _coa_codes(cfg)
    total_debit = 0.0
    total_credit = 0.0
    for i, r in df.iterrows():
        ref = r.get("code") or i
        code = r.get("code")
        if pd.isna(code) or not str(code).strip():
            _add(issues, e, i, ref, "BLOCK", "code", "Missing account code")
        elif coa_codes and str(code) not in coa_codes:
            _add(issues, e, i, ref, "BLOCK", "code",
                 f"Account '{code}' not in chart of accounts")
        debit_raw, credit_raw = r.get("debit"), r.get("credit")
        debit = parse_num(debit_raw) if pd.notna(debit_raw) else 0.0
        credit = parse_num(credit_raw) if pd.notna(credit_raw) else 0.0
        if pd.notna(debit_raw) and debit is None:
            _add(issues, e, i, ref, "BLOCK", "debit",
                 f"Non-numeric debit '{debit_raw}'")
            debit = 0.0
        if pd.notna(credit_raw) and credit is None:
            _add(issues, e, i, ref, "BLOCK", "credit",
                 f"Non-numeric credit '{credit_raw}'")
            credit = 0.0
        total_debit += debit or 0.0
        total_credit += credit or 0.0
    if abs(round(total_debit - total_credit, 2)) > 0.005:
        _add(issues, e, "-", "trial_balance", "WARN", "balance",
             f"Trial balance out of balance: debits={total_debit} credits={total_credit}")


def assess_config(cfg, issues):
    """Cross-entity config checks that depend on loaded source data."""
    ob_acc = cfg.get("opening_balance_account")
    if not ob_acc:
        return
    coa_codes = _coa_codes(cfg)
    if coa_codes and str(ob_acc) not in coa_codes:
        _add(issues, "config", "-", ob_acc, "BLOCK", "opening_balance_account",
             f"Opening balance account '{ob_acc}' not in chart of accounts")


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
    "trial_balance": assess_trial_balance,
}


def run_assessment(client: str, cfg: dict) -> dict:
    issues = []
    counts = {}
    for entity in ASSESS_ORDER:
        raw = read_source(client, cfg, entity)
        if raw is None:
            continue
        df = apply_mapping(raw, cfg, entity)
        counts[entity] = len(df)
        ASSESSORS[entity](df, cfg, issues)

    assess_config(cfg, issues)

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
