# 18to19upgrade — Handoff & State

> **Living document.** Umbrella tracker for the Odoo 18 → 19 cutover effort. Other repos have their own [HANDOFF.md](../MESv1.0/HANDOFF.md) files — this one captures cross-repo state + the audit pipeline + upgrade-specific runbooks.

---

**Last updated:** 2026-06-21 — Claude (Anthony) — cloud operatorUI multi-station + **RESUME-FROM-MES** root-cause fix shipped to prod.

### Branch HEADs (all clean + pushed)
| Repo | Branch | HEAD | Notes |
|------|--------|------|-------|
| **MESv1.0** | `master` | `3e69d60` | **canonical = PROD** (lanes-per-master-fix is STALE @ 06-17 — do not use). |
| **operatorUI** | `main` | `1dfa4ea` | resume-from-MES + cloud config + PWA. |
| **18to19upgrade** | `main` | `42f6f76` | (06-18 "create-scripts UNCOMMITTED" note below is RESOLVED — committed `af1c1c2`/`42f6f76`.) |
| **odoo18** | `msp_production` | `fdbb92f` | unchanged today. |
| **msppartialMO** | `19_upgrade` | `0ba9514` | unchanged today. |

- **RESUME-FROM-MES (root-cause fix).** operatorUI seeded pallet/history/label state from the LOCAL tablet session, not MES → fresh/cloud/post-power-cycle sessions were blind to prior production (dup roll #s, "pallet shows only this session," wrong unit # on reprinted labels, post-outage confusion). MES now exposes current-pallet + produced-rolls (`3e69d60`); operatorUI rebuilds history/pallet/slip/label from MES (`1dfa4ea` et al, incl. the `[UI_UNIT]` reprint bug `fd117b6`). Per-repo HANDOFFs + memory `operatorui-resume-from-mes.md`.
- **Cloud operatorUI:** 8 per-line instances on GCP VM `operatorui-debug` (proj `msp-mes-492315`) → prod MES, installable PWA, behind auth. Debug/bridge while stabilizing; floor LOCAL builds unchanged. memory `operatorui-cloud-debug-instance.md`.
- **Also deployed to prod:** Heather's reprint pallet sheet (MES `78b6359`) + footer overlap fix (`99fdb01`).
- **Spawned background task:** rotate the hardcoded Odoo API key in MES maintenance scripts (`update_open_mos.py`, `mass_update_boms.py`, etc.).

---

**Last updated:** 2026-06-18 — Claude (Anthony) — MSP reports fixed + deployed to **PROD**; ⚠️ create-scripts edited but **UNCOMMITTED**.

- **Pick sheet + delivery slip deployed to PROD Odoo** (the active MES work is tracked in [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md) top entry + memory `msp-odoo-reports.md`). Prod was running the stale "one row per `move_line`" design → one line per finished unit (current MES makes one lot per unit `WH/MO/<mo>-U<n>`). Fixed in `workflow/create_msp_pick_sheet.py` (per-pallet) + `create_msp_delivery_slip.py` (per-product): generalized the lot→MO collapse from `-R<n>`-only to any trailing serial (`name.rsplit('-',1)[0] if '-' in name else name`), and delivery "Pack Qty" col → **"Total Pallets"**. Deployed via the canonical idempotent upsert (driven through the prod MES VM's `_get_odoo_connection()` since `.env` isn't on this machine).
- **⚠️ TODO — commit + snapshot.** `workflow/create_msp_{pick_sheet,delivery_slip}.py` are MODIFIED in the working tree but NOT committed. Commit them, then `python workflow/snapshot_msp_reports.py --target prod` per `STAGING_TO_PROD_RUNBOOK.md` §Phase 4 (needs `.env` with `ODOO_PROD_*`, which lives only on Anthony's laptop — local python is the Store stub, so this runs there or via the VM).
- **Render-with-API-key gotcha:** the API key is RPC-scope only — `render_pallet_sheet.py`'s "Odoo accepts API keys as the Basic password for HTTP routes" is **WRONG** for Odoo 19 (both Basic-auth on `/report/pdf` and `/web/session/authenticate` reject it). Render via a throwaway `ir.actions.server` calling `_render_qweb_pdf`; see memory `msp-odoo-reports.md`.
- **Branch-table below is STALE** (2026-05-30): msppartialMO is now **19.0.1.5.0** on `odoo18/msp_production` (prod, MO-close `production_id` + `action_reconcile_close`); MESv1.0 `master` @ `722f48d` (prod). Treat the table as historical.

---

**Last updated:** 2026-05-30 — Claude (Anthony's session) — cross-repo snapshot for picking up on a different machine.

### Current branch + HEAD of every repo (all clean + pushed to origin)
| Repo | Branch | HEAD | Notes |
|------|--------|------|-------|
| **MESv1.0** | `lanes-per-master-fix` | `982f742` (code `fba98f8`) | dev branch; `master` = PRODUCTION (untouched). Staging VM deployed at `fba98f8`. |
| **operatorUI** | `main` | `e8cb174` | work directly on `main` (Heather pushes here too — fetch first). Station `C:\OperatorUI` refreshed + live. |
| **18to19upgrade** | `main` | `6c073a8`+ | this umbrella repo. |
| **odoo18** | `msp_production` | `b11176c` | `msp_packaging` addon. |
| **msppartialMO** | `19_upgrade` | `d0583c8` | partial-MO addon v19.0.1.3.0. |

⚠️ **The rich `.claude` memory notes live on THIS machine only** (`C:\Users\antho\.claude\projects\…\memory\`) and do NOT travel via git. On the other computer the **per-repo HANDOFF.md files are the source of truth** — start with [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md).

### What shipped 2026-05-29 → 05-30 (all on STAGING; production untouched)
- **Operator compliance + changeover-capture system** (MES + operatorUI): forced-close at 100% (server is authority via `produced_feet`; walk-away sweep + 30-min Office escalation), scrap silo-debit fix, `changeover_events` model + migration + `/api/changeover/*` + auto-capture + analytics tab, Start-Changeover button, scrap-reason picker. **System-wide E2E verified** (multi-agent), 3 defects found+fixed. Full plan: `MESv1.0/COMPLIANCE_CHANGEOVER_PLAN.md`.
- **Department-routed action-items** inbox (QC / Shipping / Office / Production cascade).
- **operatorUI Stop-chooser** (`31d1dd4`): merged confusing "Request Close" + "End Run" into one **Stop** button → "what's happening?" chooser (finish / changeover / stop-line). Station refreshed + live.
- **Schedule fixes** (`b3fa9b1`+`fba98f8`): (1) play/pause now drives REAL line control (`/api/line/<wc>/start|pause`); (2) schedule "running" now polls the same `/api/plant-health/snapshot` plant-health uses (was state-based → broke when the 5-min Odoo sync reset `state`); (3) **new orders land in the per-WC HOLDING AREA** (`sort_index=999`, was 998). Audit confirmed the schedule is MES-owned — Odoo planned start dates do NOT position orders; only due-date/product/qty/run-rate reference data is consumed.

### Open items
1. **Deploy MES to PRODUCTION** — everything above is staging-only (`mes-testing-pg` @ https://34.57.35.195.nip.io). Needs Anthony's explicit per-session OK + the new migration (`migrate_changeover_events.py`).
2. **operatorUI Phase 2c — hard-everywhere badge identity** — deliberately NOT shipped (rejects writes lacking a known badge → could block production).
3. Optional: one-time sweep of existing `sort_index=998` orders into holding.
4. Full MES test suite: **197 passed** (run in dev env; `pytest` is not installed on the staging VM).

---

**Last updated:** 2026-05-28 (late evening) — Claude (Anthony's session) — big day across login, tablet view, fresh-install path, full DB wipe + rebuild, and a 4-parallel-agent audit that caught + fixed 2 sev-1 bugs. See [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md) for the full per-feature breakdown and [../MESv1.0/docs/2026-05-28-audit.md](../MESv1.0/docs/2026-05-28-audit.md) for the bug list + fixes.

**TL;DR**: MES on `lanes-per-master-fix` ended the day at `1bc39f2` with: badge-based employee login + role-gated /admin/users, tablet view mode for shipping/QC pages, single-button fresh-install script (`install_fresh.sh` + `INSTALL_POSTGRES.md` runbook), and **4 merged fix branches** that resolve 2 sev-1 + 5 sev-2 + several sev-3 bugs the audit pipeline surfaced. Sev-1 highlights: (1) silo inventory was never actually decremented by rolls — `consume_silo_fifo` was defined but never called, gauges purely decorative — now wired with idempotency + Postgres `with_for_update()` row lock; (2) multi-step MO closure was routing to wrong step because `get_work_order_by_id` used `.first()` with no ordering on `WorkOrder.work_order_number` — now disambiguated via optional `work_order_id` body param + deterministic ordering. Live test: 8/8 verification cases PASS on `mes-testing-pg`. Math validation on a fresh-synced staging DB: 17/17 PASS (master roll → finished roll → progress %).

**New ops infra**: `gs://msp-mes-pg-backups/` GCS bucket created + Cloud SQL service account given `roles/storage.objectAdmin` for native `gcloud sql export sql` dumps. Today's pre-wipe snapshot at `gs://msp-mes-pg-backups/mes_pre_wipe_20260528T040250Z.sql` (204 MB).

---

**Last updated:** 2026-05-26 (late evening) — Claude (Anthony's session) — long active day with **three** major features delivered. See per-repo HANDOFFs for full detail.

**1. Tablet-based shipping pick + VICS BOL workflow** (MES `5e6d4cb` → `742b72d` → `c51071f`) — replaces the paper pick-sheet → fill-by-hand → office-types-BOL loop. Office still creates pickings in Odoo as today; warehouse picks on a tablet (scan each pallet, validate); office prints a pre-filled VICS-format BOL at `/shipping/bol/<id>`. New tables `Shipment`/`ShipmentPicking`/`ShipmentPallet`. Multi-MO trucks via shipment merge. Odoo writeback is manual — office presses `button_validate` only after the truck leaves. Lot-based matching (`pallet.wo_number == lot_id.name`) + manual-pallet entry for legacy pre-MES MOs. Unreserved-line warnings surface when Odoo can't reserve stock for some line items. Verified live against WH/OUT/01390 / S01134 Amerisource.

**2. Postgres-strict cascade fix + session-rollback safety net** (MES `5c62142`) — `get_work_order_by_id(int_fk)` was issuing `WHERE work_order_number = <int>` which Postgres rejects, aborting the scoped_session and 500-cascading every subsequent request on the gunicorn worker. SQLite silently coerced; latent until cutover. Fixed with type-dispatch in the helper + new `@app.teardown_request` rollback as the safety net so future Postgres-strict bugs can't cascade. End-LineEvent on admin close (`ccba377`) also in this run.

**3. msp_packaging 19.0.1.5.0 — restored 'Product Unit of Measure' decimal.precision** (`odoo18 6439446` on `19_upgradetest2`, `b11176c` on `msp_production`). v19 renamed the decimal.precision record from `'Product Unit of Measure'` (v18) to `'Product Unit'` (v19, digits=3). `msp_packaging.product_packaging.qty` and **12 fields** in third-party `gt_secondary_uom` still reference the v18 name on `digits=` Float fields, fall back to default 2-digit precision, store 0.035 as 0.03. Fixed via data file that defines the legacy record at digits=4. **Per Anthony's explicit one-time authorization, deployed to production Odoo** alongside staging — XML-RPC `button_immediate_upgrade`, verified write/restore on packaging row id=258. Standing prod-touch rule restored immediately. **gt_secondary_uom is uninstalled on prod** (confirmed during the inspection) — only msp_packaging needed touching.

**Also this session**: shipping dashboard rewritten for MO-level aggregation + finished-only progress (`956f40c`); "Ready to Ship" filter tightened to require pallets > 0 (`9cb7f28`); WO number links on shipping rows (`83394a5`); FPM gate hardened in operatorUI (`229ec24`).

MES migrations to run before deploy: `migrate_wo_closure_workflow.py`, `migrate_silo_lots.py`, then **`migrate_shipments.py`** (new). Postgres env wrapper required.

---

## Snapshot — where everything stands

### Odoo
- **Production**: Live on v19 (cut over ~2026-04). `msppartialMO` is NOT installed on prod yet.
- **Staging** (`19_upgradetest2` branch on Odoo.sh): `https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/`. Has vendored `msppartialMO` v19.0.1.2.0. This is what the cloud test MES talks to.

### MES
- **Production** (https://mes.mountainstatesplastics.com or similar — confirm before touching): `master` branch @ `81c7779`. **Untouched by all in-flight branch work.**
- **Cloud test, SQLite stack** (`mes-testing` GCP VM): **RETIRED 2026-05-25 — host decommissioned, do not use.** Superseded by the Postgres stack below. (Was `lanes-per-master-fix` @ `56d82fd`.)
- **Cloud test, Postgres stack** (`mes-testing-pg` GCP VM, internal 10.128.0.4, external 34.57.35.195, **public URL https://34.57.35.195.nip.io**): `lanes-per-master-fix` @ `2a6f7fb`, connects to Cloud SQL `mes-pg-staging` via Auth Proxy on `127.0.0.1:5432`. **Now fully independent** — runs its own Offline Sync Worker + Periodic Inbound Sync against the same staging Odoo. **This is the dev/test stack going forward.** Replicator + verifier daemons stopped and disabled 2026-05-25 ~01:22 UTC. Snapshot push/pull crons removed. `READ_ONLY_MODE` no longer set in `/etc/mes-pg.env`. Cloud SQL backups (HA + PITR + pg_dump cron → GCS every 15 min) remain active.

### Cloud SQL Postgres (new)
- `mes-pg-staging` in `us-central1`, ENTERPRISE edition Postgres 16.13, HA (sync standby in us-central1-b), PITR + 7-day backups
- Private IP `10.82.240.2`, peered with default VPC via `mes-pg-vpc-range` (`10.82.240.0/20`)
- Database `mes`, user `mes_app`, password in Secret Manager `mes-pg-app-password` v1
- pg_dump cron → `gs://msp-mes-backups/postgres/` every 15 min (mirrors SQLite backup cadence)
- 5,379 rows loaded across 18 tables (2026-05-24). Schema match with SQLite verified.

### operatorUI
- **Each operator station** runs its own local Flask via .bat installer. Currently on whatever the most-recent installer build picked up from `main` @ `e6612e4`.
- **Local dev** (Anthony's box): `lanes-per-master-fix` @ `b1d8da5`, points at cloud test MES. See [../operatorUI/HANDOFF.md](../operatorUI/HANDOFF.md).

### msppartialMO
- `19_upgrade` branch @ `d0583c8` — v19.0.1.3.0 (BOM auto-fill cleanup in `action_increment_qty_producing` on top of `button_mark_done` override). Source of truth for the addon.
- Vendored into `odoo18` repo's `19_upgradetest2` branch for Odoo.sh staging install.
- **NOT on production yet.** Production cut over to v19 without this addon. Will need install + module upgrade as part of staging→prod rollout.

### msp_pallet (in odoo18)
- Lives in `odoo18/msp_pallet/`, currently at `19.0.1.0.4` (b8b454c, 2026-05-22 — added `msp_unit_count` Integer field on `stock.package`).
- **NOT on production yet.** Whole module ships with the v19-staging cutover.

---

## Cross-repo HEAD reference

| Repo | Branch | HEAD | What's on it |
|---|---|---|---|
| `MESv1.0` | `lanes-per-master-fix` | `93798fa` | + 2026-05-25 evening: Health/OEE + LineEvent table; + 2026-05-25 late evening: winder calc asymmetric layouts (`a891f51`), Postgres-strict FK ordering fix in record_roll (`ff28d7b`), `/api/work-orders` excludes done/cancel (`93798fa`). Deployed to `mes-testing-pg`. |
| `MESv1.0` | `master` | `81c7779` | Dormant. Heather's cleanup + v19 staging Odoo repoint + 2026-05-19 recursion fix. Operators are NOT on this — they run whatever `lanes-per-master-fix` is at on `mes-testing-pg`. |
| `operatorUI` | `lanes-per-master-fix` | `b9b1e44` | + 2026-05-25 late evening: installer config.txt default → Postgres URL (`bb6b6a1`), 4-layer gusset master roll clickable (`084ee16`), stitch tracker Edit + type-DELETE-to-confirm (`2fbcf06`/`4102718`), gusset visual high-contrast blue+amber (`a8855f4`/`b9b1e44`). Local `C:\OperatorUI` refreshed. Installer NOT rebuilt for plant-floor stations. |
| `operatorUI` | `main` | `8d5da85` | Heather 2026-05-21: Expected master roll weight on stitch tracker for two-step orders. Has not picked up today's `lanes-per-master-fix` work yet. |
| `msppartialMO` | `19_upgrade` | `d0583c8` | v19.0.1.3.0 — button_mark_done + BOM auto-fill cleanup. (No change today.) |
| `odoo18` | `19_upgradetest2` | `b8b454c` | Vendored msppartialMO 19.0.1.3.0 + msp_pallet 19.0.1.0.4 (msp_unit_count). (No change today.) |
| `18to19upgrade` | `main` | `fa39c35` | Audit pipeline + per-run reports + umbrella HANDOFF. (This file.) |

Cmd to refresh all five at once (workspace path renamed 2026-05-25 — was `mes and operator ui`, now `mes and ui`):
```bash
cd "c:\Users\antho\Desktop\mes and ui"
for r in MESv1.0 operatorUI msppartialMO odoo18 18to19upgrade; do
  echo "=== $r ===" && (cd $r && git fetch --all --quiet && \
  echo "  branch: $(git rev-parse --abbrev-ref HEAD), HEAD: $(git rev-parse --short HEAD)" && \
  echo "  ahead/behind origin: $(git rev-list --left-right --count HEAD...@{u} 2>/dev/null | awk '{print "ahead="$1", behind="$2}')")
done
```

---

## Audit pipeline (workflow/audit/)

State-driven Odoo SO → MO → roll → pallet → invoice test pipeline. Product-agnostic since 2026-05-10.

**Most recent audit reports** (in this repo):
- `AUDIT_2026-05-09_11158.md` — Roll-sold product, baseline reference.
- `AUDIT_2026-05-10_11158_fixverify.md` — Verified silo lot + pallet rewire fixes.
- `AUDIT_2026-05-10_10083.md` — Lb-sold product, multi-step. Surfaced FG double-write + pallet UoM bugs.

**To start a fresh audit** see `PLAYBOOK.md` in this repo. The pipeline currently handles weight-tracked and unit-tracked products correctly. Hardcoded check-side cleanup is the only open TODO (see below).

---

## Pending — cross-repo

### Validation needed
- [ ] **Lanes fix end-to-end on operator station.** Cloud test MES + local operatorUI both have the fix. Need to record real master rolls on the 4-master SWS test order and confirm progress now matches reality (1× not 4×).

### Audit pipeline cleanup (cosmetic — checks fail on lb-stocked products even though functionality is correct)
- [ ] `03_observe_production.py --finalize`: hardcoded `MO/` lot prefix check. Generalize to use whatever pattern state has.
- [ ] `04_verify_pallets.py`: `pallet contains {PER_PALLET} units` check — for lb-stocked, multiply by `FG_PER_ROLL`.
- [ ] `05_verify_pick_sheet.py`: similar issue likely.
- [ ] `08_trace_lot.py`: hardcodes 11158's resin set + seed lot. Read from `state['blend_recipe']`.

### Production rollout (pending staging-validation finish)
The 2026-05-10 → 2026-05-22 fixes are all staging-verified or in-flight:
- Silo lot validation, pallet rewire, FG zero-out + cancel handling, UoM-aware pack qty (2026-05-10)
- `msppartialMO` v19.0.1.2.0 `button_mark_done` override (2026-05-10)
- `lanes_per_master_roll` + `masters_per_doff` schema split, slitter cap, Odoo auth precedence (2026-05-14)
- Dashboard perf (cached_property + batch pre-fetch + pre-warm), nav partial, DR docs, VM-IP recovery (2026-05-19)
- UoM=Thousands consumption fix + msppartialMO `19.0.1.3.0` BOM auto-fill cleanup (2026-05-21)
- **Pallet-qty UoM fix + msp_pallet `19.0.1.0.4` (msp_unit_count field) (2026-05-22)**

When ready: follow [STAGING_TO_PROD_RUNBOOK.md](STAGING_TO_PROD_RUNBOOK.md) Phase 0 dry-run first.

### SQLite → Postgres migration — DISCONNECTED (early Phase 6, soft cutover for dev/test)

**Documentation set:**
- [`POSTGRES_MIGRATION_RUNBOOK.md`](POSTGRES_MIGRATION_RUNBOOK.md) — the one-time migration plan, phase-by-phase (historical)
- [`NEW_STAGING_RUNBOOK.md`](NEW_STAGING_RUNBOOK.md) — **repeatable recipe** for spinning up another Postgres-backed env from scratch
- [`OPS_RUNBOOK.md`](OPS_RUNBOOK.md) — daily/weekly checks, PITR + backup procedures, failure modes, cost
- Script suite at [`workflow/pg_migration/`](workflow/pg_migration/)

Final state as of 2026-05-25 ~01:22 UTC:

- ✅ Phases 0-4 completed (audit, provision, schema, bulk load, replication wired + verifier green)
- 🛑 **Phase 5 (parity soak) skipped** — Anthony validated by clicking around and called it good enough for dev/test purposes. Two stacks now disconnected; both run independently against the same staging Odoo.
- 🛑 **Phase 6 (formal cutover) deferred indefinitely** — operators stay on SQLite stack until/unless Anthony explicitly decides to migrate them too. There's no rush; both work.
- 🟢 **Active dev/test environment is now the Postgres stack** (https://34.57.35.195.nip.io). All bug-finding + feature development happens here.

**What was un-wired during disconnection:**
- `mes-pg-replicator.service` + `mes-pg-verifier.service` stopped + disabled (unit files still exist if you want to re-enable)
- snapshot push cron removed from `mes-testing` (anthony user)
- snapshot pull cron removed from `mes-testing-pg` (root)
- `READ_ONLY_MODE=1` removed from `/etc/mes-pg.env`; `mes.service` restarted; sync workers now run
- `gs://msp-mes-backups/snapshots/` left in place (last snapshot is stale; can delete or ignore)

**What's still active on the Postgres stack:**
- `cloud-sql-proxy.service` — proxy on 127.0.0.1:5432
- `mes.service` — gunicorn + full sync workers
- `nginx.service` — TLS termination
- `/etc/cron.d/` pg_dump backup → `gs://msp-mes-backups/postgres/` every 15 min
- Cloud SQL HA + PITR + daily snapshots (all GCP-managed)

**To re-enable replication later** (e.g. if you decide to do a formal parity soak):
1. `systemctl enable --now mes-pg-replicator mes-pg-verifier` on `mes-testing-pg`
2. Add the snapshot pull cron back (`/usr/local/bin/pull_snapshot_from_gcs.sh` is still installed)
3. SSH to `mes-testing`, add snapshot push cron back
4. Set `READ_ONLY_MODE=1` in `/etc/mes-pg.env`; restart `mes.service`
5. After ≥7 days of `verifier: green`, declare cutover

See [`project_resume_2026_05_24.md`](../../../.claude/projects/c--Users-Anthony-Desktop-mes-and-operator-ui/memory/project_resume_2026_05_24.md) memory for full state.

Tier 1 SQLite stopgap patches DEPLOYED 2026-05-24 on `mes-testing` (`MESv1.0/bf36be6`): per-phase commits + busy_timeout 5000→30000ms + WAL on mes_schedule.db. Operators no longer see DB-locked 500s. Stays in place until Postgres cutover; then removed.

### Future work mentioned in conversation
- Operator-set lane override on extrusion setup screen.
- Ft-based progress in LBS + Unit trackers (today they're lbs-based; the data is there but not displayed).
- 3rd-product audit (Thousands-sold inline single-step) — was on the original audit plan.
- Machine status / telemetry ingestion (the driver for Postgres + future TimescaleDB or partitioned tables).

---

## Useful endpoints

| Service | URL |
|---|---|
| Cloud test MES | https://34.57.35.195.nip.io |
| Cloud test MES health | https://34.57.35.195.nip.io/api/health |
| Staging Odoo | https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/ |
| Production Odoo | (live v19 instance — confirm URL before any contact) |
| Local operatorUI dev | http://127.0.0.1:5010 (when `python app.py` is running) |

---

## Maintaining this document

- Update the snapshot section after every cross-repo deploy or branch change.
- Add new gotchas / runbook updates to the per-repo HANDOFF.md files, then summarize here if it affects cross-repo state.
- Keep the HEAD reference table accurate — it's the fastest way to answer "where are we?" from a new computer.
- Remove resolved pending items.
- Aim for under 200 lines; link to longer runbooks for the heavy detail.
