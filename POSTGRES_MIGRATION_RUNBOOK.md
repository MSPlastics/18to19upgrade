# SQLite → PostgreSQL Migration Runbook

**Purpose**: migrate the MES backing store from SQLite to Google Cloud SQL for PostgreSQL with high availability, **without taking operators down at any point**. Old SQLite stack remains live and untouched through the entire migration; new Postgres stack is built in parallel and validated before any cutover.

**Strict rule**: every step in this doc is scoped to the **test VM environment first** (`mes-testing` + new `mes-testing-pg`). Production migration is a separate scheduled event that re-runs this same pattern against prod data, **only after the test cutover has been stable for ≥14 days**.

**Driver**: SQLite is hitting writer-contention errors under current load (122 rolls / 8 lines). At full prod scale + future machine-status telemetry, SQLite cannot scale. Cloud SQL Postgres gives us HA, point-in-time recovery, concurrent writers, and a foundation for time-series telemetry via native partitioning.

**Targets**:
- Old stack (untouched until cutover): `mes-testing` VM at `https://34.67.173.228.nip.io`, SQLite (`mes_data.db`, `local_db.sqlite`, `mes_schedule.db`)
- New stack (built fresh): Cloud SQL Postgres 16 HA + new `mes-testing-pg` VM in `us-central1-a`

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  OLD STACK (untouched, operators use this)                   │
│                                                              │
│  mes-testing VM (us-central1-a)                              │
│    ├─ gunicorn (MES app, lanes-per-master-fix)               │
│    ├─ mes_data.db   (SQLite, WAL)                            │
│    ├─ local_db.sqlite                                        │
│    └─ mes_schedule.db                                        │
│           │                                                  │
│           │ (read by replication daemon below)               │
│           ▼                                                  │
└───────────┼──────────────────────────────────────────────────┘
            │
            │ delta replication every 60s
            ▼
┌──────────────────────────────────────────────────────────────┐
│  NEW STACK (we build, validate, then cut over to)            │
│                                                              │
│  Cloud SQL for PostgreSQL HA (us-central1)                   │
│    ├─ Primary (us-central1-a)                                │
│    ├─ Sync standby (us-central1-b) ← auto-failover           │
│    ├─ Daily backups + 7d PITR                                │
│    └─ Optional read replica (us-central1-c)                  │
│                ▲                                             │
│                │ via Cloud SQL Auth Proxy                    │
│                │                                             │
│  mes-testing-pg VM (us-central1-a, e2-medium)                │
│    ├─ gunicorn (same MES code, postgres connection string)   │
│    ├─ replication daemon (SQLite → Postgres delta sync)      │
│    └─ verification daemon (row counts + checksums + alerts)  │
└──────────────────────────────────────────────────────────────┘
```

Operators continue hitting `mes-testing` VM throughout the entire migration. Cutover = re-pointing operator station `mes_base_url` at the new VM. Reversible in seconds by reverting the station URL.

---

## What's intentionally NOT in this migration

- **Production MES** stays on SQLite. Prod migration is a separate scheduled event after test proves out.
- **No schema redesign**. We migrate the existing schema as-is. Future schema improvements happen after we're stable on Postgres.
- **No TimescaleDB**. Cloud SQL doesn't support it. Machine-status telemetry will use Postgres-native time-range partitioning, which handles millions of rows/day fine. If we ever outgrow that, we add TimescaleDB on a separate sidecar instance.
- **Tier 1 SQLite patches** (per-phase commits + busy_timeout bump + WAL on the two non-WAL DBs) ship separately this week as a stopgap. They become irrelevant after cutover but keep operators productive during the 5-week migration window.

---

## Phase 0 — Pre-flight audits (~3 hrs, no infra changes)

Run these on the existing `mes-testing` VM. They surface every issue we'd hit during migration, so we fix them BEFORE provisioning Postgres.

| Step | What | Check that proves it worked | Tool |
|---|---|---|---|
| 0.1 | Codebase SQLite-pattern audit: `import sqlite3`, `julianday()`, `strftime()`, `PRAGMA`, `json_extract()`, raw connection strings | Punch list of file:line locations — known scope, not estimate | Explore agent |
| 0.2 | Data quality audit on current SQLite (FK orphans, type mismatches, datetime format inconsistencies, duplicate primary keys, mixed-type columns) | Concrete count of issues per check; each gets a remediation note before Phase 3 | `workflow/pg_migration/pre_flight_audit.py` |
| 0.3 | Schema-consolidation decision: 1 Postgres database with 1 schema (merging the 3 SQLite DBs) — or schemas per source DB | Documented decision with reasoning | manual |
| 0.4 | Inventory the 3 SQLite DBs: classify each as "consolidate," "deprecated," or "still in use" | Each DB has a documented disposition | `pre_flight_audit.py --classify-dbs` |

```bash
# Run the audit script on the test VM
gcloud compute ssh anthony@mes-testing --zone=us-central1-a --command="
  cd /opt/mes && ./venv/bin/python /opt/mes/scripts/pre_flight_audit.py \
    --db data/mes_data.db \
    --db data/local_db.sqlite \
    --db data/mes_schedule.db \
    --report /tmp/pre_flight_report.md
"
gcloud compute scp anthony@mes-testing:/tmp/pre_flight_report.md \
  ./pre_flight_report_$(date +%Y%m%d).md --zone=us-central1-a
```

**Gate**: Phase 1 doesn't start until the audit report is reviewed and each blocker has a remediation plan.

### Phase 0 audit findings — 2026-05-24 run against `mes-testing` VM

Report archived as `pre_flight_report_2026-05-24.md`. Summary:

| DB | Tables | Rows | Pragmas | Disposition |
|---|---|---|---|---|
| `data/mes_data.db` | 18 | 5,379 | WAL, busy_timeout=5000 | **Source of truth** — migrate as-is |
| `data/local_db.sqlite` | 0 | 0 | delete | **Deprecated** — empty file, ignore in migration |
| `data/mes_schedule.db` | 6 | 732 | delete | **Consolidate** — `work_order_sorting` table (732 rows) is the only live data here; merge into main Postgres schema |

Tables that exist in both `mes_data.db` and `mes_schedule.db`:

| Table | mes_data.db rows | mes_schedule.db rows | Source of truth |
|---|---|---|---|
| `master_rolls` | 1,155 | 0 | mes_data.db |
| `pallets` | 82 | 0 | mes_data.db |
| `qc_records` | 0 | 0 | mes_data.db |
| `qc_reports` | 0 | 0 | mes_data.db |

Decision: keep `mes_data.db` as the source of truth for these. The empty duplicates in `mes_schedule.db` are leftover from an older schema layout and get dropped during migration. The only thing we actually pull from `mes_schedule.db` is `work_order_sorting`.

**Blockers to remediate before Phase 3 bulk load** (2 total):

1. `master_rolls.work_order_id` → `work_orders.id`: **3 orphan rows** would fail Postgres FK enforcement.
   - Remediation options (decide before Phase 3): (a) backfill the referenced work_order rows, (b) NULL the FK on the 3 orphans, (c) delete the 3 orphan roll rows. Likely (b) since rolls are append-only operator data we don't want to lose.

2. `master_rolls.length_ft` declared `INTEGER` but actually stores `REAL` values (e.g. `1333.333333`).
   - Remediation: declare the Postgres column as `DOUBLE PRECISION` (Float in SQLAlchemy). One-line change in `db_models.py` before Phase 2's `create_all()`.

**Warnings (not blockers, fix opportunistically):**

- `work_orders.date_deadline` has mixed format strings (some `date_only`, some `unknown`/empty). Normalize during migration script or accept NULLs on the unparseable ones.

---

## Phase 1 — Provision Cloud SQL + new VM (~1 day)

| Step | What | Check |
|---|---|---|
| 1.1 | Provision Cloud SQL Postgres 16 HA: `db-custom-2-7680`, HA enabled, 7d backup + PITR, private IP in same VPC | `gcloud sql instances describe mes-pg-staging` shows `HIGH_AVAILABILITY` |
| 1.2 | Create database `mes`, app user `mes_app` with limited grants | `psql -U mes_app` connects, has CRUD on `public` schema only |
| 1.3 | Store credentials in Secret Manager | `gcloud secrets versions list mes-pg-app-password` returns a version |
| 1.4 | Provision `mes-testing-pg` VM (e2-medium, us-central1-a, same VPC) | `gcloud compute instances describe mes-testing-pg` returns RUNNING |
| 1.5 | Install Cloud SQL Auth Proxy on new VM as systemd service | `psql -h 127.0.0.1 -U mes_app -d mes` works from VM |
| 1.6 | Clone MES repo + install Python deps + psycopg | `pip list` matches `requirements.txt` plus `psycopg[binary]` |
| 1.7 | Set up `pg_dump` cron every 15 min → `gs://msp-mes-backups/postgres/` | First backup file lands in GCS, restore test from it succeeds |
| 1.8 | Configure systemd unit for gunicorn on new VM mirroring existing setup | `systemctl status mes` shows active |

### Provisioning commands

```bash
# 1.1 — Cloud SQL HA instance
gcloud sql instances create mes-pg-staging \
  --database-version=POSTGRES_16 \
  --tier=db-custom-2-7680 \
  --region=us-central1 \
  --availability-type=REGIONAL \
  --backup-start-time=07:00 \
  --enable-point-in-time-recovery \
  --network=default \
  --no-assign-ip \
  --storage-auto-increase

# 1.2 — Create database + app user
gcloud sql databases create mes --instance=mes-pg-staging
gcloud sql users create mes_app --instance=mes-pg-staging --password='SET_VIA_SECRET_MANAGER'

# 1.3 — Secret Manager
echo -n 'STRONG_PASSWORD_HERE' | gcloud secrets create mes-pg-app-password \
  --replication-policy=automatic --data-file=-

# 1.4 — New VM
gcloud compute instances create mes-testing-pg \
  --zone=us-central1-a \
  --machine-type=e2-medium \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=20GB \
  --service-account=<existing-mes-sa> \
  --scopes=cloud-platform
```

The full provisioning script is at `workflow/pg_migration/provision.sh` (not yet written — drafted at execution time after Phase 0 confirms scope).

---

## Phase 2 — Schema + code changes (~1 day)

| Step | What | Check |
|---|---|---|
| 2.1 | `DATABASE_URL` env var switchable between sqlite and postgres. Default new VM to postgres; existing VM stays sqlite. | Both VMs start successfully against their respective DBs |
| 2.2 | Fix SQLite-isms from Phase 0 audit (replace `julianday()`, `strftime()`, `PRAGMA` calls with cross-dialect equivalents) | Existing test suite passes on both SQLite (locally) and Postgres (test VM) |
| 2.3 | Run `Base.metadata.create_all()` against empty Postgres. Diff resulting schema vs SQLite schema (tables, columns, types, indexes, FKs). | Table count match. Column-by-column diff produces zero discrepancies for types we care about (datetimes, booleans, JSON). |
| 2.4 | New VM startup smoke test: empty Postgres, app comes up, dashboard renders empty state, health endpoint OK | `curl http://mes-testing-pg/api/health` returns 200 |

---

## Phase 3 — Initial bulk load (~half day)

| Step | What | Check |
|---|---|---|
| 3.1 | Take SQLite backup from current VM (point-in-time snapshot) | `.tar.gz` of 3 DB files in GCS, verified non-empty |
| 3.2 | Run `sqlite_to_pg_migrate.py` (SQLAlchemy with source+dest engines, batched copy, explicit type coercion). pgloader as fallback if our custom script chokes on something. | Migration completes with no errors |
| 3.3 | Per-table row count verification | All deltas = 0 |
| 3.4 | Sample record diff: 10 random rows per table, all columns | Zero column mismatches |
| 3.5 | Sequence reset: `ALTER SEQUENCE` for every autoincrement column | Test insert produces expected next ID |
| 3.6 | FK constraint verification | `SELECT count(*) FROM ... WHERE foreign_id NOT IN ...` returns 0 for every relationship |

```bash
# Run on mes-testing-pg VM
cd /opt/mes && ./venv/bin/python scripts/sqlite_to_pg_migrate.py \
  --sqlite-source-host mes-testing \
  --sqlite-source-path /opt/mes/data/mes_data.db \
  --postgres-url 'postgresql://mes_app@127.0.0.1:5432/mes' \
  --verify
```

---

## Phase 4 — Continuous replication + verification (runs Day 5 → cutover)

| Step | What | Check |
|---|---|---|
| 4.1 | Start replication daemon on `mes-testing-pg`: pulls SQLite rows newer than last-sync timestamp every 60s, upserts into Postgres | Replication lag stays <90s under normal operation |
| 4.2 | Start verification daemon: every 5 min compares row counts + checksums, alerts on drift | After 24h: zero drift alerts |
| 4.3 | Health dashboard: Flask page on new VM showing per-table lag, last sync time, drift status | Page loads, all green |
| 4.4 | Stress test: 10x replication frequency for 1 hour | No connection pool exhaustion, no errors |

```bash
# Start daemons as systemd services on mes-testing-pg
sudo systemctl enable --now mes-pg-replicator.service
sudo systemctl enable --now mes-pg-verifier.service

# Health dashboard
curl http://mes-testing-pg:5001/health
```

---

## Phase 5 — Read parity validation (Week 2-3, observation only)

| Step | What | Check |
|---|---|---|
| 5.1 | New VM read-accessible to operators in read-only mode (POSTs blocked at app layer) | Operators report "looks identical" |
| 5.2 | Per-endpoint comparison via `render_compare.py`: render same MO/pallet/dashboard on both stacks, diff HTML | Outputs match byte-for-byte (modulo timestamps) for ≥99% of sampled targets |
| 5.3 | Run for **7 consecutive days** with zero drift, zero rendering mismatches, zero replication failures | Daily report green for 7 days. **This is the gate to cutover.** |

---

## Phase 6 — Cutover (Day ~21, one quiet window)

**Gate: Phase 5 must show 7 consecutive days of zero drift before this phase starts.**

| Step | What | Check |
|---|---|---|
| 6.1 | Pick the quietest window (overnight, between shifts) — coordinate with Heather + ops | Confirmed window, comms sent |
| 6.2 | Stop replication daemon | `systemctl stop mes-pg-replicator` — confirmed not running |
| 6.3 | Final delta sync (one-shot, verifies no straggler rows) | Post-final-sync drift = 0 |
| 6.4 | Re-enable writes on new VM (remove read-only block) | Test POST from script succeeds |
| 6.5 | Roll plant-floor stations to new VM URL one at a time (`station_data.db` `mes_base_url` update) | Each station POSTs a test roll, verified in Postgres |
| 6.6 | Old VM continues running healthy, read-only, for 14 days as rollback safety | Old VM up, no writes, used only for diff comparisons |

**Rollback if anything goes wrong in next 48h:**
1. Re-point all station URLs to old VM (`mes_base_url` revert)
2. Run reverse-replication script (Postgres → SQLite) to backfill rolls that landed on Postgres since cutover
3. Old VM resumes as source of truth

The reverse-replication script is **pre-built and tested as part of Phase 4** so it's not improvised in the moment.

---

## Phase 7 — Decommission (Day ~35)

| Step | What | Check |
|---|---|---|
| 7.1 | After 14 days of incident-free operation, archive old SQLite files to GCS (compressed) | `gs://msp-mes-backups/sqlite-archive/2026-MM-DD-final.tar.gz` exists |
| 7.2 | Decommission old VM (delete instance, KEEP disk snapshot 90 days) | Snapshot exists in GCP console |
| 7.3 | Update `MESv1.0/HANDOFF.md`, `VM_SETUP_GUIDE.sh`, memory | Docs reflect new architecture; SQLite paths removed |

---

## Calendar

| Week | Work |
|---|---|
| This week (Mon-Wed) | Tier 1 SQLite patches + prod MES feature deploy (separate track, not part of this runbook) |
| Week 1 (next Mon-Fri) | Phase 0 audits → Phase 1 provisioning → Phase 2 schema → Phase 3 bulk load |
| Week 2 | Phase 4 replication + verification running, Phase 5 starts |
| Week 3 | Phase 5 read parity (full week of zero-drift required to gate Phase 6) |
| Week 4 | Phase 6 cutover in a quiet window |
| Week 5-6 | Phase 7 decommission after 14 days clean |

---

## Cost

**During migration (5 weeks):**
- Cloud SQL HA `db-custom-2-7680`: ~$130/month
- (Optional) +1 read replica: ~$40/month
- Storage 10 GB: ~$2/month
- Backups: first 7 days free
- New VM `e2-medium`: ~$25/month
- Old VM (still running): ~$25/month
- **Total: ~$180-220/month during migration window**

After Phase 7 decommission: old VM removed, ~$155/month steady-state.

---

## Files in this suite

| File | Purpose |
|---|---|
| `POSTGRES_MIGRATION_RUNBOOK.md` (this doc) | Permanent migration playbook |
| `workflow/pg_migration/README.md` | Quick reference for the script suite |
| `workflow/pg_migration/pre_flight_audit.py` | Phase 0 data audit on existing SQLite |
| `workflow/pg_migration/sqlite_to_pg_migrate.py` | Phase 3 bulk SQLite → Postgres load |
| `workflow/pg_migration/sqlite_pg_replicator.py` | Phase 4 delta replication daemon |
| `workflow/pg_migration/sqlite_pg_verifier.py` | Phase 4 row-count + checksum verification daemon |
| `workflow/pg_migration/render_compare.py` | Phase 5 endpoint diff harness |
| `workflow/pg_migration/provision.sh` (TBD) | Phase 1 cloud resource provisioning script (drafted after Phase 0) |

---

## Confirmations locked in 2026-05-24

1. **Cloud SQL HA** (not self-hosted Postgres on VM, not Cloud SQL non-HA). No TimescaleDB for now.
2. **New parallel VM** `mes-testing-pg` (not reusing `mes-testing`). Operators experience zero impact during entire migration.
3. **Test VM migration first.** Prod migration is a separate scheduled event using this same proven pattern, only after test cutover has been stable ≥14 days. Not bundled with this week's prod feature deploy.

---

## Maintaining this document

- Update timestamp at top after every phase landing.
- Mark phases as `[x] completed YYYY-MM-DD` in the calendar table.
- Move resolved blockers from "Phase 0 issues" to a "Resolved" appendix.
- After Phase 7 decommission, archive this doc and write a short "Post-mortem & lessons" appendix.
