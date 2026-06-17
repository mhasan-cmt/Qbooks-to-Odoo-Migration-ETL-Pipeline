# QuickBooks → Odoo Migration ETL

Config-driven ETL pipeline for migrating QuickBooks data into Odoo.
For each new client: create a config + drop raw CSV exports. No code changes.

## Pipeline Stages (CLI)

```
python -m etl.run assess     --client acme    # Stage 1: Data Assessment -> issues report
# (client fixes source, re-exports)
python -m etl.run assess     --client acme    # Stage 3: re-run -> clean report
python -m etl.run normalize  --client acme    # Stage 4: Odoo-ready exports
python -m etl.run import      --client acme [--dry-run]   # Stage 5: load into Odoo + recon
python -m etl.run all         --client acme
```

Stage 2 (Data Quality Review with Customer) is a manual loop: send `02_assessment/issues_*.xlsx`,
get a corrected re-export, replace files in `01_raw/`, then re-run `assess`.

## Per-client layout
```
clients/<client>/
  01_raw/            <- QuickBooks CSV exports go here
  02_assessment/     <- issue reports (Stage 1/3 output)
  03_normalized/     <- Odoo-import-ready files (Stage 4 output)
  04_import_logs/    <- import results + reconciliation (Stage 5 output)
config:  configs/<client>.yaml
```

## Entities handled
chart_of_accounts, partners (customer/vendor), products, invoices, bills, payments, opening_balance

## Import dependency order (enforced)
coa -> taxes -> partners -> products -> invoices/bills -> opening_balance -> payments
# Qbooks-to-Odoo-Migration-ETL-Pipeline
