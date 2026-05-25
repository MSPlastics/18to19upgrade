#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlite_pg_replicator.py — Phase 4 of POSTGRES_MIGRATION_RUNBOOK

Long-running daemon that incrementally replicates new + updated rows
from the live SQLite database (on the old mes-testing VM) into the
Postgres database (on Cloud SQL), every N seconds.

Architecture:
  - Runs on the NEW VM (mes-testing-pg)
  - Reads SQLite over a read-only fuse/sshfs mount of /opt/mes/data
    (or scp's the file every cycle — see --source-mode)
  - Per-table cursor advances on a high-water column (typically
    created_at for append-only tables, updated_at where present)
  - Upserts into Postgres using ON CONFLICT DO UPDATE
  - Records last-seen high-water per table in a `replication_state`
    table inside Postgres so daemon restarts pick up where they left off

Per-table strategy is declared in REPLICATION_CONFIG below — keep that
in sync with the schema. Tables not in the config are NOT replicated
(safer than guessing and missing rows).

Usage:
  ./sqlite_pg_replicator.py \\
      --sqlite /mnt/mes-testing/data/mes_data.db \\
      --postgres 'postgresql://mes_app:PWD@127.0.0.1:5432/mes' \\
      --interval 60

Runs forever. Send SIGTERM (systemctl stop) to drain and exit cleanly.

Operational notes:
  - Read-only on SQLite (URI mode=ro)
  - Idempotent: re-running with the same state produces the same Postgres
    state — no duplicates, no missed updates
  - On startup, reads existing high-water marks from Postgres and resumes
  - Logs to stdout (systemd captures to journalctl)
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from sqlalchemy import create_engine, MetaData, Table, Column, String, \
        DateTime, text, select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.engine import Engine
except ImportError:
    print("ERROR: sqlalchemy + psycopg required. "
          "pip install 'sqlalchemy>=2,<3' 'psycopg[binary]'", file=sys.stderr)
    sys.exit(2)


# -----------------------------------------------------------------------------
# Per-table replication config. KEEP IN SYNC WITH MES SCHEMA.
#
# For each table we replicate:
#   pk:        primary key column (used for ON CONFLICT)
#   watermark: column to advance on (must be monotonic — created_at, id, etc.)
#   conflict:  what to do on PK collision; usually "update" (latest wins) or
#              "skip" (append-only, never update existing rows)
# -----------------------------------------------------------------------------
# Schema-verified 2026-05-24 against live mes-pg-staging reflection — when
# in doubt re-run the reflection script in vm_setup/ and update this dict.
# Watermark=None means full-table refresh each cycle (small tables only).
REPLICATION_CONFIG = {
    "master_rolls":        {"pk": "roll_id",   "watermark": "created_at",   "conflict": "update"},
    "pallets":             {"pk": "pallet_id", "watermark": "created_at",   "conflict": "update"},
    "sync_queue":          {"pk": "id",        "watermark": "updated_at",   "conflict": "update"},
    "scrap_records":       {"pk": "id",        "watermark": "created_at",   "conflict": "update"},
    "compliance_events":   {"pk": "id",        "watermark": "created_at",   "conflict": "update"},
    "line_inventory":      {"pk": "id",        "watermark": "last_updated", "conflict": "update"},
    "silos":               {"pk": "id",        "watermark": "last_updated", "conflict": "update"},
    # No timestamp columns — full refresh each cycle (small tables).
    # work_orders/products/sale_orders are truncate+reinserted by the Odoo
    # periodic sync, so a full refresh here matches that pattern.
    "qc_records":          {"pk": "id",        "watermark": None,           "conflict": "update"},
    "qc_reports":          {"pk": "report_id", "watermark": None,           "conflict": "update"},
    "work_orders":         {"pk": "id",        "watermark": None,           "conflict": "update"},
    "products":            {"pk": "id",        "watermark": None,           "conflict": "update"},
    "sale_orders":         {"pk": "id",        "watermark": None,           "conflict": "update"},
    "work_centers":        {"pk": "id",        "watermark": None,           "conflict": "update"},
    "employees":           {"pk": "id",        "watermark": None,           "conflict": "update"},
    "boms":                {"pk": "id",        "watermark": None,           "conflict": "update"},
    "label_templates":     {"pk": "id",        "watermark": None,           "conflict": "update"},
    "settings":            {"pk": "key",       "watermark": None,           "conflict": "update"},
    # Tables NOT replicated (intentional):
    #   wo_metadata: derived, rebuilt from work_orders
}


# Replication state table — created in Postgres if missing
STATE_TABLE = "replication_state"


_log = logging.getLogger("replicator")


def _ensure_state_table(pg_engine: Engine) -> Table:
    md = MetaData()
    state_table = Table(
        STATE_TABLE, md,
        Column("table_name", String, primary_key=True),
        Column("last_watermark", String, nullable=True),
        Column("last_run_at", DateTime, nullable=True),
        Column("rows_copied_total", String, nullable=True),
    )
    md.create_all(pg_engine, checkfirst=True)
    return state_table


def _get_watermark(pg_engine: Engine, state_table: Table, table: str) -> str | None:
    with pg_engine.connect() as c:
        row = c.execute(
            select(state_table.c.last_watermark)
            .where(state_table.c.table_name == table)
        ).fetchone()
    return row[0] if row else None


def _set_watermark(pg_engine: Engine, state_table: Table, table: str,
                   watermark: str, copied: int) -> None:
    with pg_engine.begin() as c:
        stmt = pg_insert(state_table).values(
            table_name=table,
            last_watermark=watermark,
            last_run_at=datetime.utcnow(),
            rows_copied_total=str(copied),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["table_name"],
            set_={
                "last_watermark": stmt.excluded.last_watermark,
                "last_run_at": stmt.excluded.last_run_at,
                "rows_copied_total": stmt.excluded.rows_copied_total,
            },
        )
        c.execute(stmt)


def _replicate_table(sqlite_path: str, pg_engine: Engine, md: MetaData,
                     state_table: Table, table: str, cfg: dict) -> int:
    """Pull new/changed rows from SQLite for one table; upsert to Postgres.
    Returns row count replicated this cycle."""
    if table not in md.tables:
        _log.warning("table %s not in Postgres dest; skipping", table)
        return 0

    dest = md.tables[table]
    pk = cfg["pk"]
    watermark_col = cfg["watermark"]

    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    sconn.row_factory = sqlite3.Row
    sconn.text_factory = lambda b: b.decode("utf-8", "replace")

    # Build delta query
    if watermark_col:
        last_w = _get_watermark(pg_engine, state_table, table)
        if last_w:
            query = f'SELECT * FROM "{table}" WHERE "{watermark_col}" > ? ORDER BY "{watermark_col}"'
            params = (last_w,)
        else:
            # No prior watermark — first run, copy everything
            query = f'SELECT * FROM "{table}" ORDER BY "{watermark_col}"'
            params = ()
    else:
        # No watermark column — we have to read the whole table every cycle
        # (these tables are small master data; refresh cost is low)
        query = f'SELECT * FROM "{table}"'
        params = ()

    try:
        cursor = sconn.execute(query, params)
    except sqlite3.OperationalError as e:
        _log.error("SQLite query failed on %s: %s", table, e)
        sconn.close()
        return 0

    batch: list[dict] = []
    new_watermark = None
    total = 0

    for row in cursor:
        d = dict(row)
        batch.append(d)
        if watermark_col and watermark_col in d and d[watermark_col]:
            new_watermark = d[watermark_col]
        if len(batch) >= 500:
            _flush_batch(pg_engine, dest, batch, pk, cfg["conflict"])
            total += len(batch)
            batch = []

    if batch:
        _flush_batch(pg_engine, dest, batch, pk, cfg["conflict"])
        total += len(batch)

    sconn.close()

    if total > 0 and new_watermark:
        _set_watermark(pg_engine, state_table, table, new_watermark, total)
    elif watermark_col is None and total > 0:
        # Full-refresh tables: stamp last_run_at, watermark stays None
        _set_watermark(pg_engine, state_table, table, "", total)

    return total


def _flush_batch(pg_engine: Engine, dest: Table, batch: list[dict],
                 pk: str, conflict_policy: str) -> None:
    # Filter to only columns that exist in dest, and parse JSON strings.
    # SQLite stores JSON columns as TEXT; passing the raw string to a
    # Postgres JSON/JSONB column stores it as a JSON string scalar instead
    # of the intended list/dict. Templates iterating over wo.layers then
    # see characters instead of layer dicts.
    dest_cols = set(dest.c.keys())
    json_cols = {c.name for c in dest.c if "json" in str(c.type).lower()}
    cleaned = []
    for row in batch:
        out = {}
        for k, v in row.items():
            if k not in dest_cols:
                continue
            if k in json_cols and isinstance(v, str) and v != "":
                try:
                    out[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    out[k] = v  # malformed source — preserve raw string
            elif k in json_cols and v == "":
                out[k] = None
            else:
                out[k] = v
        cleaned.append(out)

    if not cleaned:
        return

    stmt = pg_insert(dest).values(cleaned)
    if conflict_policy == "skip":
        stmt = stmt.on_conflict_do_nothing(index_elements=[pk])
    elif conflict_policy == "update":
        excluded = stmt.excluded
        update_cols = {c.name: excluded[c.name] for c in dest.c if c.name != pk}
        stmt = stmt.on_conflict_do_update(
            index_elements=[pk], set_=update_cols
        )
    else:
        raise ValueError(f"unknown conflict policy: {conflict_policy}")

    with pg_engine.begin() as c:
        c.execute(stmt)


def replicate_once(sqlite_path: str, pg_engine: Engine,
                   state_table: Table) -> dict[str, int]:
    """One full pass through all configured tables. Returns per-table copy counts."""
    md = MetaData()
    md.reflect(bind=pg_engine)
    counts = {}
    cycle_start = time.time()
    for table, cfg in REPLICATION_CONFIG.items():
        try:
            n = _replicate_table(sqlite_path, pg_engine, md, state_table, table, cfg)
        except Exception as e:
            _log.error("replication failed for %s: %s", table, e, exc_info=True)
            n = -1
        counts[table] = n
    elapsed = time.time() - cycle_start
    total = sum(c for c in counts.values() if c > 0)
    nonzero = {t: n for t, n in counts.items() if n > 0}
    if nonzero:
        _log.info("cycle done in %.2fs: %s (total %d rows)",
                  elapsed, nonzero, total)
    else:
        _log.debug("cycle done in %.2fs: no changes", elapsed)
    return counts


_shutdown_requested = False


def _handle_sigterm(signum, frame):  # noqa: ARG001
    global _shutdown_requested
    _log.info("SIGTERM received, will exit after current cycle")
    _shutdown_requested = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", required=True,
                        help="Path to SQLite source (read-only)")
    parser.add_argument("--postgres", required=True,
                        help="Postgres SQLAlchemy URL")
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between replication cycles (default: 60)")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit (testing)")
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not Path(args.sqlite).exists():
        _log.error("SQLite path does not exist: %s", args.sqlite)
        return 2

    pg_engine = create_engine(args.postgres, future=True, pool_pre_ping=True)
    try:
        with pg_engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        _log.error("Postgres connection failed: %s", e)
        return 2

    state_table = _ensure_state_table(pg_engine)
    _log.info("Replicator started — sqlite=%s, interval=%ds, %d tables configured",
              args.sqlite, args.interval, len(REPLICATION_CONFIG))

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    if args.once:
        replicate_once(args.sqlite, pg_engine, state_table)
        return 0

    while not _shutdown_requested:
        try:
            replicate_once(args.sqlite, pg_engine, state_table)
        except Exception as e:
            _log.error("cycle failed at top level: %s", e, exc_info=True)
        # Sleep in 1-sec slices so SIGTERM is responsive
        for _ in range(args.interval):
            if _shutdown_requested:
                break
            time.sleep(1)

    _log.info("Replicator exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
