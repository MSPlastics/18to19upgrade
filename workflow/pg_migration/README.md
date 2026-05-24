# `workflow/pg_migration/` — SQLite → Postgres migration suite

Scripts that support [`../../POSTGRES_MIGRATION_RUNBOOK.md`](../../POSTGRES_MIGRATION_RUNBOOK.md). Each script maps to a phase in the runbook. Run them in order, gated by manual review of the prior phase's output.

## Layout

| File | Runbook phase | Lifetime | Run from |
|---|---|---|---|
| [`pre_flight_audit.py`](pre_flight_audit.py) | Phase 0 — pre-flight | one-shot per audit | `mes-testing` VM (read-only on SQLite) |
| [`sqlite_to_pg_migrate.py`](sqlite_to_pg_migrate.py) | Phase 3 — bulk load | one-shot | `mes-testing-pg` VM (reads SQLite, writes Postgres) |
| [`sqlite_pg_replicator.py`](sqlite_pg_replicator.py) | Phase 4 — replication daemon | systemd service, runs until cutover | `mes-testing-pg` VM |
| [`sqlite_pg_verifier.py`](sqlite_pg_verifier.py) | Phase 4 — parity verifier daemon | systemd service, runs until cutover | `mes-testing-pg` VM |
| [`render_compare.py`](render_compare.py) | Phase 5 — endpoint diff | scheduled (cron, every N hours) | anywhere with HTTP access to both VMs |

## Dependencies

All scripts target Python 3.11+. The replicator, migrate, and verifier scripts need:

```bash
pip install 'sqlalchemy>=2,<3' 'psycopg[binary]'
```

`pre_flight_audit.py` only uses the stdlib (sqlite3). `render_compare.py` only needs `requests`.

## Order of operations

```
Day 1 — Phase 0
  ./pre_flight_audit.py --db ... --report pre_flight.md
  # Review pre_flight.md, fix every blocker, re-run until zero

Day 2-3 — Phase 1 (no script here — provisioning is `gcloud sql ...` from the runbook)

Day 3-4 — Phase 2 (no script here — code change in MESv1.0 swaps the conn string)

Day 4 — Phase 3
  ./sqlite_to_pg_migrate.py --sqlite .../mes_data.db --postgres ... --verify
  # Verify exit code 0 and row counts in stdout

Day 5+ — Phase 4 (long-running daemons)
  sudo systemctl enable --now mes-pg-replicator.service
  sudo systemctl enable --now mes-pg-verifier.service
  # Watch journalctl for drift alerts

Week 2-3 — Phase 5 (scheduled diff)
  ./render_compare.py --old ... --new ... --auto-mo-sample 50 --report compare-$(date +%Y%m%d).json
  # Schedule via cron, review daily

Week 4 — Phase 6 cutover (no script — operator station URL update)
```

## What none of these scripts do

- Schema creation on Postgres. That's done once in Phase 2 by running `Base.metadata.create_all()` from the MES app with `DATABASE_URL` pointed at the empty Postgres. These scripts assume the schema already exists.
- Cloud SQL provisioning. That's `gcloud sql instances create ...` from the runbook.
- Operator cutover. That's a station-by-station `mes_base_url` update in the runbook's Phase 6.
- Anything destructive on SQLite. Every script opens SQLite as `file:...?mode=ro` for safety.

## Reverse-replication (Phase 6 rollback safety)

The runbook calls for a reverse-replication script (Postgres → SQLite) that gets pre-built and tested during Phase 4 so it's ready if cutover needs to be rolled back. **That script isn't in this suite yet** — it gets added before Phase 6 cutover by adapting `sqlite_pg_replicator.py` to flip the direction. Same logic, swapped sides.

## Per-script docstrings

Each `.py` has a long module docstring at the top explaining intent, behavior, and CLI. `./<script>.py --help` is the fastest way to see options.

## Modifying these scripts

These scripts encode assumptions about the MES schema (especially `REPLICATION_CONFIG` in `sqlite_pg_replicator.py` and `VERIFIED_TABLES` in `sqlite_pg_verifier.py`). If you add a new table to `MESv1.0/db_models.py` while this migration is in flight, add it to both lists. The Phase 0 audit script reflects the schema directly so it picks up new tables automatically — but the replicator/verifier need explicit entries.
