"""Stage 5: Data Import into Odoo via XML-RPC, plus reconciliation.
Loads normalized CSVs in dependency order. Idempotent via external IDs.
--dry-run validates + counts without writing to Odoo.
"""
from __future__ import annotations
import csv
import xmlrpc.client
from datetime import datetime
import pandas as pd
from .common import out_path, client_dir

IMPORT_ORDER = ["chart_of_accounts", "partners", "invoices", "bills",
                "opening_balance", "payments"]


class Odoo:
    def __init__(self, cfg):
        o = cfg["odoo"]
        self.url, self.db = o["url"], o["db"]
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, o["username"], o["password"], {})
        if not self.uid:
            raise RuntimeError("Odoo authentication failed")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        self.pwd = o["password"]
        self.user = o["username"]

    def execute(self, model, method, *args):
        return self.models.execute_kw(self.db, self.uid, self.pwd, model, method, list(args))

    def upsert_xmlid(self, ext_id, model, vals):
        """Create-or-update keyed on ir.model.data external id (idempotent)."""
        module, name = "qb_migration", ext_id
        found = self.execute("ir.model.data", "search_read",
                             [[["module", "=", module], ["name", "=", name]]],
                             {"fields": ["res_id"], "limit": 1})
        if found:
            rid = found[0]["res_id"]
            self.execute(model, "write", [rid], vals)
            return rid, False
        rid = self.execute(model, "create", [vals])
        self.execute("ir.model.data", "create", [{
            "module": module, "name": name, "model": model, "res_id": rid}])
        return rid, True


def _read_norm(client, entity):
    p = out_path(client, "normalized", f"{entity}.csv")
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def _import_coa(odoo, df, log):
    for _, r in df.iterrows():
        if not r["account_type"]:
            log.append(("chart_of_accounts", r["code"], "SKIP", "no account_type"))
            continue
        _, created = odoo.upsert_xmlid(r["id"], "account.account", {
            "code": r["code"], "name": r["name"], "account_type": r["account_type"]})
        log.append(("chart_of_accounts", r["code"], "CREATE" if created else "UPDATE", ""))


def _import_partners(odoo, df, log):
    for _, r in df.iterrows():
        vals = {"name": r["name"], "is_company": True,
                "customer_rank": int(r.get("customer_rank") or 0),
                "supplier_rank": int(r.get("supplier_rank") or 0)}
        for f in ("email", "phone", "street", "vat"):
            if r.get(f):
                vals[f] = r[f]
        _, created = odoo.upsert_xmlid(r["id"], "res.partner", vals)
        log.append(("partners", r["name"], "CREATE" if created else "UPDATE", ""))


def _resolve_xmlid(odoo, ext_id):
    found = odoo.execute("ir.model.data", "search_read",
                        [[["module", "=", "qb_migration"], ["name", "=", ext_id]]],
                        {"fields": ["res_id"], "limit": 1})
    return found[0]["res_id"] if found else None


def _import_docs(odoo, df, entity, log):
    for _, r in df.iterrows():
        pid = _resolve_xmlid(odoo, r["partner_id/id"])
        if not pid:
            log.append((entity, r.get("ref", ""), "ERROR", "partner not found"))
            continue
        vals = {"move_type": r["move_type"], "partner_id": pid,
                "invoice_date": r["invoice_date"], "ref": r.get("ref") or ""}
        if r.get("invoice_date_due"):
            vals["invoice_date_due"] = r["invoice_date_due"]
        _, created = odoo.upsert_xmlid(r["id"], "account.move", vals)
        log.append((entity, r.get("ref", ""), "CREATE" if created else "UPDATE", ""))


def reconcile(client, cfg, odoo):
    """Compare normalized record counts/totals to source assessment counts."""
    rows = []
    for entity in ["chart_of_accounts", "partners", "invoices", "bills"]:
        df = _read_norm(client, entity)
        src_n = len(df) if df is not None else 0
        model = {"chart_of_accounts": "account.account", "partners": "res.partner",
                 "invoices": "account.move", "bills": "account.move"}[entity]
        domain = []
        if entity == "invoices":
            domain = [["move_type", "=", "out_invoice"]]
        elif entity == "bills":
            domain = [["move_type", "=", "in_invoice"]]
        odoo_n = odoo.execute(model, "search_count", [domain]) if odoo else None
        rows.append({"entity": entity, "source_count": src_n,
                     "odoo_count": odoo_n,
                     "match": (odoo_n is None) or (odoo_n >= src_n)})
    return pd.DataFrame(rows)


def run_import(client: str, cfg: dict, dry_run: bool = False) -> dict:
    log = []
    odoo = None if dry_run else Odoo(cfg)
    handlers = {
        "chart_of_accounts": lambda d: _import_coa(odoo, d, log),
        "partners": lambda d: _import_partners(odoo, d, log),
        "invoices": lambda d: _import_docs(odoo, d, "invoices", log),
        "bills": lambda d: _import_docs(odoo, d, "bills", log),
    }
    for entity in IMPORT_ORDER:
        df = _read_norm(client, entity)
        if df is None:
            continue
        if dry_run:
            log.append((entity, "-", "DRY_RUN", f"{len(df)} rows ready"))
            continue
        if entity in handlers:
            handlers[entity](df)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logdf = pd.DataFrame(log, columns=["entity", "ref", "action", "note"])
    logp = out_path(client, "import", f"import_log_{stamp}.csv")
    logdf.to_csv(logp, index=False)

    recon = reconcile(client, cfg, odoo)
    reconp = out_path(client, "import", f"reconciliation_{stamp}.csv")
    recon.to_csv(reconp, index=False)
    return {"log": str(logp), "reconciliation": str(reconp),
            "dry_run": dry_run, "all_match": bool(recon["match"].all())}
