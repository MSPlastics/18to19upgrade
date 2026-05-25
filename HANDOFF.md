# 18to19upgrade — Handoff & State

> **Living document.** Umbrella tracker for the Odoo 18 → 19 cutover effort. Other repos have their own [HANDOFF.md](../MESv1.0/HANDOFF.md) files — this one captures cross-repo state + the audit pipeline + upgrade-specific runbooks.

**Last updated:** 2026-05-24 (very late evening) — Claude (Anthony's session) — **Postgres migration Phases 0-4 WIRED on test environment**. Phase 4 daemons (`mes-pg-replicator` + `mes-pg-verifier`) are running as systemd services on `mes-testing-pg` reading from GCS-mediated SQLite snapshots; verifier reports `green` across all 17 replicated tables. Now in the ≥7-day zero-drift soak before Phase 5 gate. Old SQLite stack untouched; operators continue using it.

---

## Snapshot — where everything stands

### Odoo
- **Production**: Live on v19 (cut over ~2026-04). `msppartialMO` is NOT installed on prod yet.
- **Staging** (`19_upgradetest2` branch on Odoo.sh): `https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/`. Has vendored `msppartialMO` v19.0.1.2.0. This is what the cloud test MES talks to.

### MES
- **Production** (https://mes.mountainstatesplastics.com or similar — confirm before touching): `master` branch @ `81c7779`. **Untouched by all in-flight branch work.**
- **Cloud test, SQLite stack** (https://34.67.173.228.nip.io, `mes-testing` GCP VM): `lanes-per-master-fix` @ `56d82fd` (Tier 1 patches + DATABASE_URL env support + length_ft Float). Operators use this URL. See [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md).
- **Cloud test, Postgres stack** (`mes-testing-pg` GCP VM, internal 10.128.0.4, external 34.57.35.195): same `lanes-per-master-fix` @ `56d82fd` code, connects to Cloud SQL `mes-pg-staging` via Auth Proxy on `127.0.0.1:5432`. **Not operator-facing.** Phase 4 daemons live: `mes-pg-replicator.service` (60s) + `mes-pg-verifier.service` (300s, /health on :5001). SQLite snapshots flow via GCS (`gs://msp-mes-backups/snapshots/mes_data_snapshot.db`) — 60s push cron on `mes-testing`, 60s pull cron on `mes-testing-pg`. Verifier reporting `green` across all 17 tables.

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
| `MESv1.0` | `lanes-per-master-fix` | `125869b` | Lane split, auth precedence, dashboard perf, nav partial, UoM=Thousands consumption + **pallet-qty UoM (2026-05-22)** |
| `MESv1.0` | `master` | `81c7779` | Heather's cleanup + v19 staging Odoo repoint + 2026-05-19 recursion fix |
| `operatorUI` | `lanes-per-master-fix` | `a51eec6` | Stitch tracker uses `lanes_per_master_roll`. Heather's `8d5da85` (Expected Wt UI) is on `main` — pick up via rebase when convenient. |
| `operatorUI` | `main` | `8d5da85` | Heather 2026-05-21: Expected master roll weight on stitch tracker for two-step orders |
| `msppartialMO` | `19_upgrade` | `d0583c8` | v19.0.1.3.0 — button_mark_done + BOM auto-fill cleanup |
| `odoo18` | `19_upgradetest2` | `b8b454c` | Vendored msppartialMO 19.0.1.3.0 + msp_pallet 19.0.1.0.4 (msp_unit_count) |
| `18to19upgrade` | `main` | `fa39c35` | Audit pipeline + per-run reports + umbrella HANDOFF |

Cmd to refresh all five at once:
```bash
cd "c:\Users\Anthony\Desktop\mes and operator ui"
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

### SQLite → Postgres migration — PHASES 0-4 WIRED on test, in 7-day soak before Phase 5
Full plan at [`POSTGRES_MIGRATION_RUNBOOK.md`](POSTGRES_MIGRATION_RUNBOOK.md). Script suite at [`workflow/pg_migration/`](workflow/pg_migration/). Status as of 2026-05-24:

- ✅ **Phase 0** — pre_flight_audit ran; 2 blockers found, both fixed (3 FK orphans NULLed, length_ft Integer→Float)
- ✅ **Phase 1** — Cloud SQL HA provisioned, new VM `mes-testing-pg` built, Auth Proxy + IAM + backups all working
- ✅ **Phase 2** — `Base.metadata.create_all()` ran clean; 18 tables, schema parity with SQLite verified
- ✅ **Phase 3** — bulk load via `sqlite_to_pg_migrate.py` (fixed to use topological order); 5,379 rows loaded clean
- ✅ **Phase 4** — daemons wired and running. Transport: GCS-mediated SQLite snapshot (60s push cron on `mes-testing` writes `sqlite3 .backup` → `gs://msp-mes-backups/snapshots/`; 60s pull cron on `mes-testing-pg` downloads to `/tmp/mes_data_snapshot.db`). Daemons: `mes-pg-replicator.service` (60s interval, watermarks where columns exist, full-refresh elsewhere) + `mes-pg-verifier.service` (300s interval, `/health` on :5001). First green report 2026-05-25 00:05 UTC. Replicator config + verifier config schema-corrected (qc_records/qc_reports/work_orders/products/sale_orders have no `created_at`/`updated_at`).
- ⏳ **Phase 5** (NEXT) — read parity validation, ≥7 consecutive days of `verifier: green`, then `render_compare.py --auto-mo-sample 50` against the two stacks
- ⏳ Phase 6 (cutover) follows Phase 5

**Phase 4 ops surface:**
- `mes-testing` cron (anthony): `* * * * * /usr/local/bin/snapshot_to_gcs.sh >> /var/log/mes-snapshot.log 2>&1`
- `mes-testing-pg` cron (root): `* * * * * /usr/local/bin/pull_snapshot_from_gcs.sh >> /var/log/mes-snapshot-pull.log 2>&1`
- Health: `curl http://127.0.0.1:5001/health` on `mes-testing-pg` (returns 200/green or 503/drift)
- Logs: `sudo journalctl -u mes-pg-replicator -f` and `sudo journalctl -u mes-pg-verifier -f`

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
| Cloud test MES | https://34.67.173.228.nip.io |
| Cloud test MES health | https://34.67.173.228.nip.io/api/health |
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
