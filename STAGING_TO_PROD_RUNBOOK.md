# Staging → Production Deployment Runbook

**Purpose**: deploy the full set of MSP customizations (built and verified on staging) to production. Sister doc to `PLAYBOOK.md` (which covers the v18→v19 platform upgrade itself).

**Strict rule**: every step in this doc is for the day you've decided to push staging-verified work to prod. Until then, **do not run any `--commit`/restart command against prod**.

**Targets**:
- Production Odoo: `https://msplastics-odoo18.odoo.com` (Odoo 19.0+e since 2026-05-03)
- Production MES VM: `mesproduction` at `34.56.101.124`

---

## What's currently on staging but NOT on prod (as of 2026-05-10)

| Component | Where it lives | Status on staging | Status on prod |
|---|---|---|---|
| **`msp_pallet` Odoo addon** | `odoo18/msp_pallet/` (branch `19_upgradetest2`) v19.0.1.0.3 | installed | **not installed** |
| **`msppartialMO` v19 port** | `msppartialMO/` (branch `19_upgrade`) v19.0.1.1.0 | installed | **not installed** (prod still on the v18-era version on `msp_production` branch) |
| **MES code (silo+pallet fixes, reconcile sync, kiosk endpoints)** | `MESv1.0/` master | deployed to `mes-testing` VM | **not deployed** to prod MES VM |
| **MSP QWeb reports** (6 of them) | `workflow/create_msp_*.py` upserters → write `ir.ui.view` records on the target Odoo | created on staging | **not created** on prod |
| **Audit lots** (e.g. `CLR-REPRO-AUDIT-001` for Clear Repro) | created on staging Odoo via `setup_silos.py` ensure-clear-repro-lot | exists on staging | **not created** on prod (and prod's Clear Repro inventory still has the no-lot data quality issue) |
| **MES silo lot mappings** | local sqlite on `mes-testing` VM | configured to point at staging Odoo lots | prod MES silos use **prod Odoo lots** (different IDs) |

---

## Phase 0 — pre-flight (week-of)

1. **Communication**: notify operators 48hr ahead. Specifically:
   - The pick sheet, delivery slip, invoice, pallet sheet PDFs will appear in Print menus once deployed
   - Silo update form will start rejecting free-text lot numbers (must pick from dropdown)
   - Pallets will start auto-syncing to Odoo as `stock.package` records
2. **Backup**: confirm the latest Odoo.sh production backup is recent (Odoo retains 7 days; the dashboard shows the timestamp). Same for the MES production VM disk snapshot.
3. **Env vars**: the `18to19upgrade/.env` file is gitignored and lives only on Anthony's laptop. Confirm `ODOO_PROD_URL`, `ODOO_PROD_DB`, `ODOO_PROD_API_KEY`, `ODOO_PROD_USER` are set. The deployment scripts read these via `--target prod`.
4. **Working tree**: confirm all 5 repos are clean and on the right branches.
   ```bash
   for d in MESv1.0 odoo18 msppartialMO operatorUI 18to19upgrade; do
     echo === $d ===; cd "../$d"; git status -s; git log --oneline -1
   done
   ```
   Expected branches: `MESv1.0:master`, `odoo18:19_upgradetest2`, `msppartialMO:19_upgrade`, `operatorUI:main`, `18to19upgrade:main`.
5. **Dry-run everything**: each upserter and installer accepts `--target prod` without `--commit` to print what it would change.

---

## Phase 1 — install the `msp_pallet` Odoo addon on production

This addon defines:
- Custom fields on `stock.package` (`msp_gross_weight_lb`, `msp_length_in/width_in/height_in`, `msp_finalized_at`, `msp_unit_numbers_summary`)
- Compute fields `msp_mo_ids`, `msp_lot_ids`, `msp_dimensions_display` on `stock.package`
- `msp_pallet_ids` compute on `stock.picking` (drives the pallet listing on the form)
- Default `MSP Pallet` `stock.package.type`
- Form view extensions for `stock.package` and `stock.picking`

### Steps

1. **Branch the addon source into the production deployment branch.** On `MSPlastics/odoo18`:
   ```bash
   cd ../odoo18
   git checkout msp_production               # or whatever branch Odoo.sh prod tracks
   git merge 19_upgradetest2 -- msp_pallet/  # subdirectory merge
   git push origin msp_production
   ```
   Wait for Odoo.sh to rebuild prod (~2-5 min). The build will fail if the addon has a manifest issue — Odoo.sh dashboard logs it.

2. **Trigger install via XMLRPC** (similar to `workflow/install_msp_pallet.py` which targets staging — modify or branch for prod):
   ```python
   # Manually adapt install_msp_pallet.py to read ODOO_PROD_* and run against prod.
   # Or use Odoo Apps menu: Apps → Update Apps List → search "MSP Pallet" → Install.
   ```

3. **Verify**: log into prod, go to Inventory → Operations → Packages → form view should show "MSP Pallet Info", "Origins", and "Reserved Cases" sections.

4. **Rollback**: Apps menu → MSP Pallet → Uninstall. (Will delete custom fields + their data.)

**Estimated downtime**: none if Odoo.sh handles the rebuild without restart. ~5 min Apps menu install.

---

## Phase 2 — vendor in the `msppartialMO` v19 port

The MES depends on `action_increment_qty_producing`, `action_ship_partial_batch`, `action_close_and_backorder` from this addon. The v19 port lives on the `19_upgrade` branch; prod still has the v18-era version (which won't work in v19).

### Steps

1. **Vendor the addon** (similar pattern to staging where it's vendored into `odoo18/msppartialMO/` on `19_upgradetest2`):
   ```bash
   cd ../odoo18
   git checkout msp_production
   git rm -r msppartialMO/                                # remove v18 version
   git checkout 19_upgradetest2 -- msppartialMO/          # take v19 port
   git commit -m "vendor msppartialMO v19.0.1.1.0 from 19_upgradetest2 branch"
   git push origin msp_production
   ```
   Odoo.sh rebuilds. If the manifest version on disk is newer than the installed version, the next "Update Apps List" will offer Upgrade.

2. **Apps menu → MSP Partial MO → Upgrade**.

3. **Verify**: create a test MO on prod (small qty, scrap product), record one roll via the MES → it should produce + auto-ship-partial. Confirm via:
   ```python
   mo = env['mrp.production'].search([('name','=','WH/MO/...')])
   mo.qty_producing  # should advance per FG record
   ```

4. **Rollback**: revert the addon dir on `msp_production` branch + push, then Apps → MSP Partial MO → Upgrade (will reload v18 version).

**Risk**: prod operators may be mid-MO when this hits. Schedule for a no-MR-active window.

---

## Phase 3 — deploy MES code to production VM

Brings prod MES up to MESv1.0 master, which includes:
- `ac919b1`: silo lot validation + auto-rewire pickings to packages
- `030a094`, `f7a36e1`, `47f9c1c`, etc.: pallet shipping reconciliation sync, kiosk pallet-scale endpoint
- All the lot-tracking sync-path fixes from earlier sessions

### Steps

1. **SSH + pull + restart** (analogous to `mes-testing` pattern):
   ```bash
   gcloud compute ssh mesproduction --zone=us-central1-a --command="
     cd /opt/mes &&
     sudo -u anthony git pull &&
     sudo systemctl restart mes &&
     sleep 5 &&
     sudo systemctl status mes --no-pager | head -10
   "
   ```

2. **If venv is broken** (e.g. missing dependency after pull):
   ```bash
   sudo rm -rf /opt/mes/venv
   sudo -u anthony python3 -m venv /opt/mes/venv
   sudo -u anthony /opt/mes/venv/bin/pip install -r /opt/mes/requirements.txt
   sudo -u anthony /opt/mes/venv/bin/pip install python-dateutil
   sudo systemctl restart mes
   ```

3. **Run database migration if needed** (Pallet table got `gross_weight_lb`, `is_finalized`, `finalized_at` columns):
   ```bash
   gcloud compute ssh mesproduction --zone=us-central1-a --command="
     cd /opt/mes && sudo -u anthony /opt/mes/venv/bin/python migrate_pallet_finalize.py
   "
   ```
   (This is idempotent — checks columns exist before adding.)

4. **Verify**: `curl -H "X-API-KEY: <prod-key>" https://<prod-mes-host>/api/work-orders | head -c 200` should return JSON.

5. **Verify silo validation**: try POSTing a bogus lot — should get 400.

6. **Rollback**: `git reset --hard <prior_commit>` + restart. The DB migration is forward-only (don't drop the new columns; old code ignores them).

**Estimated downtime**: ~30s for systemd restart.

---

## Phase 4 — deploy the QWeb reports to production Odoo

All 6 upserters in `workflow/create_msp_*.py` are idempotent and follow the same `--target {staging|prod} [--commit]` pattern.

### Recommended deploy order

| # | Script | Purpose | Bound to | Print menu appears on |
|---|---|---|---|---|
| 1 | `create_msp_sale_report.py` | Modern MSP sale order | `sale.order` | Quotation/Sale Order form |
| 2 | `create_msp_delivery_slip.py` | Customer-facing delivery slip | `stock.picking` | Delivery Order form |
| 3 | `create_msp_pick_sheet.py` | Warehouse pick sheet w/ pallets | `stock.picking` (outgoing) | Delivery Order form |
| 4 | `create_msp_pallet_sheet.py` | Per-pallet sheet w/ QR + contents | `stock.package` | Package form (depends on Phase 1!) |
| 5 | `create_msp_invoice.py` | MSP-styled invoice with lot column | `account.move` | Invoice form |
| 6 | `create_msp_dashboard.py` | Open Sales Orders dashboard | `spreadsheet.dashboard` | Dashboards menu |

### Per-script protocol

```bash
cd 18to19upgrade

# 0. PRE-FLIGHT: drift check against staging - did anyone hand-edit a view?
python workflow/diff_msp_reports.py --target staging --summary-only
# Expected: "no drift". If drift detected, decide whether to:
#   (a) accept live arch into the script's QWEB_ARCH constant (replace the source), OR
#   (b) revert the live edit on staging by re-running the upserter against staging
# Either way, resolve drift BEFORE pushing to prod.

# 1. Snapshot pre-deploy state (audit trail):
python workflow/snapshot_msp_reports.py --target prod
git add workflow/snapshots/qweb_reports/prod/
git commit -m "snapshot: prod MSP reports pre-deploy YYYY-MM-DD"

# 2. Always dry-run first (no --commit) to see what would change:
python workflow/create_msp_sale_report.py --target prod

# 3. Then commit:
python workflow/create_msp_sale_report.py --target prod --commit

# 4. Post-deploy snapshot for the audit trail + DR backup:
python workflow/snapshot_msp_reports.py --target prod
git add workflow/snapshots/qweb_reports/prod/
git commit -m "snapshot: prod MSP reports post-deploy YYYY-MM-DD"
```

Each script:
- Searches for existing view by key + report_name; updates in place if found, creates if missing
- Prints view id + action id to stdout
- Idempotent — safe to re-run if you tweak the QWeb arch

### After all 6 commit, verify

For each report, open a record on prod, click the Print menu, confirm the new option appears, then click it and verify the PDF renders without `Â…`/`Ã…` artifacts (those are wkhtmltopdf encoding issues with em-dash/middle-dot/multiplication-sign that we already fixed by using ASCII).

**Manual visual check**: Pick sheet on a real outbound delivery, confirm the unified Pick Checklist groups by `-PAL-N` correctly, the Order Summary at the bottom matches checklist order, and the Grand Total per-UoM breakdown adds up.

### Rollback

For any report, run the matching script with `--target prod --delete` (some scripts have a `--delete` mode; check each script's `--help`). Or manually delete the `ir.ui.view` and `ir.actions.report` records from Odoo's dev mode.

---

## Phase 5 — fix-data: production Clear Repro lot

The 2026-05-10 audit caught that staging's Clear Repro inventory had **no lot tracking** — 166k lb sat in a no-lot quant. We fixed that on staging by creating `CLR-REPRO-AUDIT-001` and assigning the existing qty to it (see `setup_silos.py::ensure_clear_repro_lot`).

**The same data issue almost certainly exists on prod** (since staging is rebuilt from prod periodically). Before MES production resin consumption can produce clean lot traceability for Clear Repro, the prod data needs the same fix.

### Steps

1. **Probe prod** to confirm the issue exists (read-only):
   ```python
   # In a Python REPL with prod credentials:
   quants = odoo.search_read('stock.quant',
       [('product_id.name','=','Clear Repro'), ('location_id.name','=','Stock'),
        ('lot_id','=',False), ('quantity','>',0)],
       ['quantity'])
   # If non-empty, the issue exists.
   ```

2. **Decide on the prod lot name** with Anthony — likely something like `CLR-REPRO-PROD-001` or `CLR-REPRO-LEGACY-001` (do NOT reuse the AUDIT- name).

3. **Run the fix** (adapt `setup_silos.py::ensure_clear_repro_lot` for prod target — it currently reads `ODOO_STAGING_*` via `_common.py`):
   - Create `stock.lot` for Clear Repro with the chosen name
   - For each no-lot quant in WH/Stock, write `lot_id` = new lot id

4. **Verify**: re-run the probe — should return 0 no-lot positive quants.

5. **Rollback**: re-write `lot_id=False` on the affected quants. Lot record can stay (no harm).

---

## Phase 6 — re-bind production MES silos to **production** Odoo lots

The cloud test MES (`mes-testing`) currently has its silos pointing at **staging** Odoo lot names (`5615421-01`, `22508010A`, `M26010164A`, etc. — these happen to exist on staging because staging is a copy of prod, but production MES needs verification that the same lot IDs/names exist on prod with positive qty).

### Steps

1. **Probe each material on prod** to confirm a positive-free lot exists for each. Use the same approach as `setup_silos.py` but with `ODOO_PROD_*`:
   ```python
   # For Butene1-BF, Frac1-A, Exeed 1018.RA, Clear Repro, conSLIP fast,
   # conANTIBLOCK clarity — find the largest-positive-free lot on prod
   ```

2. **Update production MES silos** via the operator UI (`/silo/control` page) — the new server-side validation (commit `ac919b1`) will reject any lot that doesn't exist on prod Odoo, so the operator picks from the dropdown which now lists only positive-qty lots.

3. **Verify**: try posting a bogus lot via curl — should 400. Try a real one — should 200.

---

## Phase 7 — post-deploy verification (the real audit)

Once everything above is on prod, run the audit pipeline against prod **read-only** for one in-flight or recently-completed MO. Do NOT create test SOs on prod.

```bash
# Adapt 00_baseline.py to point at prod for read-only:
# Read an existing prod MO (e.g. WH/MO/01200), inspect its FG lot,
# verify the new tools work — pick sheet for its delivery, lot trace,
# pallet records, etc.
```

This catches deployment gaps without polluting prod data with synthetic tests.

---

## Quick reference: deploy order summary

```
0. Pre-flight (comms, backups, env vars, dry-runs)
1. Install msp_pallet addon on prod Odoo
2. Vendor msppartialMO v19 port + upgrade on prod Odoo
3. git pull + systemctl restart mes on production MES VM
4. Run all 6 create_msp_*.py upserters with --target prod --commit
5. Fix Clear Repro no-lot data quality issue on prod
6. Re-bind production MES silos to production Odoo lots
7. Read-only verification using audit pipeline tools
```

Each phase is reversible. Each phase is gated on the previous one passing visual verification.

---

## What this runbook does NOT cover

- The actual v18→v19 platform upgrade (covered by `PLAYBOOK.md`)
- The pallet shipping architecture rationale (covered by `PALLET_SHIPPING_PLAN.md`)
- The audit pipeline mechanics (covered by `AUDIT_PROCEDURE.md`)
- Operator training on the new pick sheet / silo picker / pallet kiosk
- DR rollback for the MES VM itself (covered by GCP snapshot policy)

---

## Owner notes

- Treat each phase as its own change-management ticket
- Wait for `--commit` confirmation per phase before the next one
- The pick sheet Phase 4 is the most operator-visible; do it last in the sequence so they have a "before" to compare
- The audit pipeline (`workflow/audit/`) is staging-only by design — adapt with explicit `ODOO_PROD_*` env wiring if used for prod read-only verification
