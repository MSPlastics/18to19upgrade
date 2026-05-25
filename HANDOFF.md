# 18to19upgrade — Handoff & State

> **Living document.** Umbrella tracker for the Odoo 18 → 19 cutover effort. Other repos have their own [HANDOFF.md](../MESv1.0/HANDOFF.md) files — this one captures cross-repo state + the audit pipeline + upgrade-specific runbooks.

**Last updated:** 2026-05-25 (late evening) — Claude (Anthony's session) — full session focused on the Postgres stack, **SQLite VM (`mes-testing`) formally retired** (root has no GitHub creds; can't pull). Six MES + operatorUI fixes shipped end-to-end (commit → push → `sudo git pull` + `systemctl restart` on `mes-testing-pg`) — see [`../MESv1.0/HANDOFF.md`](../MESv1.0/HANDOFF.md) and [`../operatorUI/HANDOFF.md`](../operatorUI/HANDOFF.md) for per-repo detail. Highlights: MES winder calc (MO 01557) now prefers fewer-master asymmetric layouts; Postgres-strict FK violation on `master_rolls_pallet_id_fkey` fixed by reordering record_roll; `/api/work-orders` now hides done/cancelled WOs so partial-shipment originals (`WH/MO/00976` vs active `-002` backorder) stop appearing as selectable; operatorUI stitch tracker got Edit + type-DELETE-to-confirm + 4-layer gusset clickable + complementary blue/amber gusset palette. **Earlier (morning):** Postgres + SQLite stacks DISCONNECTED, Phase 5 soak cancelled; Anthony declared migration validated.

---

## Snapshot — where everything stands

### Odoo
- **Production**: Live on v19 (cut over ~2026-04). `msppartialMO` is NOT installed on prod yet.
- **Staging** (`19_upgradetest2` branch on Odoo.sh): `https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/`. Has vendored `msppartialMO` v19.0.1.2.0. This is what the cloud test MES talks to.

### MES
- **Production** (https://mes.mountainstatesplastics.com or similar — confirm before touching): `master` branch @ `81c7779`. **Untouched by all in-flight branch work.**
- **Cloud test, SQLite stack** (https://34.67.173.228.nip.io, `mes-testing` GCP VM): `lanes-per-master-fix` @ `56d82fd` (Tier 1 patches + DATABASE_URL env support + length_ft Float). Operators use this URL. See [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md).
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
| Cloud test MES (SQLite, operators) | https://34.67.173.228.nip.io |
| Cloud test MES (SQLite) health | https://34.67.173.228.nip.io/api/health |
| Cloud test MES (Postgres, validation) | https://34.57.35.195.nip.io |
| Cloud test MES (Postgres) health | https://34.57.35.195.nip.io/api/health |
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
