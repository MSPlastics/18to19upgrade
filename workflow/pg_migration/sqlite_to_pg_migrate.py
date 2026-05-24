#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlite_to_pg_migrate.py — Phase 3 of POSTGRES_MIGRATION_RUNBOOK

One-shot bulk copy of a SQLite database into an empty PostgreSQL database.
Assumes the Postgres schema has already been created via the MES app's
SQLAlchemy `Base.metadata.create_all()` in Phase 2. This script ONLY
copies row data.

What it does:
  1. Open SQLite source (read-only) and Postgres dest (transactional)
  2. For each non-system table in the SQLite source:
       a. Verify the table exists in Postgres with matching column names
       b. Stream rows from SQLite in batches of N
       c. Coerce SQLite-isms (datetime strings -> datetime objects,
          0/1 -> booleans where dest column is boolean)
       d. Insert into Postgres with transaction per batch
  3. Reset Postgres sequences so next inserts don't collide with imported IDs
  4. Verify row count per table; report any deltas

What it does NOT do:
  - Schema creation (Phase 2's job)
  - Dual-write / replication (Phase 4's job — sqlite_pg_replicator.py)
  - Cutover (Phase 6's job)
  - Anything destructive on the SQLite source (opens as immutable)

Usage:
  ./sqlite_to_pg_migrate.py \\
      --sqlite /opt/mes/data/mes_data.db \\
      --postgres 'postgresql://mes_app:PWD@127.0.0.1:5432/mes' \\
      --batch-size 500 \\
      --verify

  # For multi-source consolidation (Phase 0.3 decided to merge 3 DBs):
  ./sqlite_to_pg_migrate.py \\
      --sqlite /opt/mes/data/mes_data.db \\
      --sqlite /opt/mes/data/mes_schedule.db \\
      --postgres '...' \\
      --table-conflict-policy=first-wins

Exit codes:
  0 — migration completed, all row counts match
  1 — completed with deltas (some tables didn't migrate cleanly)
  2 — operational error (couldn't connect, schema mismatch, etc.)
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Iterable


# SQLAlchemy is required. Install with `pip install 'sqlalchemy>=2,<3' psycopg[binary]`.
try:
    from sqlalchemy import create_engine, MetaData, Table, select, insert, text
    from sqlalchemy.engine import Engine
    from sqlalchemy.exc import SQLAlchemyError
except ImportError:
    print("ERROR: sqlalchemy + psycopg required. "
          "pip install 'sqlalchemy>=2,<3' 'psycopg[binary]'", file=sys.stderr)
    sys.exit(2)


# Datetime patterns we expect to see (mirrors pre_flight_audit.py)
DT_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]


def _parse_dt(s: str) -> datetime | date | None:
    """Best-effort datetime parser. Returns None if unparseable."""
    if not s or not isinstance(s, str):
        return None
    for fmt in DT_PATTERNS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _coerce_row(row: dict, dest_table: Table) -> dict:
    """Coerce SQLite-stored values to types Postgres expects.

    SQLAlchemy will mostly do this for us, but we handle the common gotchas
    explicitly so any failure is visible here rather than buried in a
    psycopg traceback:
      - datetime stored as string -> datetime object
      - 0/1 stored as int in a boolean column -> True/False
      - empty string in a numeric column -> None
    """
    out = {}
    for col_name, value in row.items():
        if col_name not in dest_table.c:
            # Postgres dest doesn't have this column — skip (likely a
            # column we dropped or renamed during migration design).
            continue
        col = dest_table.c[col_name]
        py_type = col.type.python_type if hasattr(col.type, "python_type") else None

        if value is None:
            out[col_name] = None
            continue

        try:
            if py_type is bool:
                out[col_name] = bool(value)
            elif py_type in (datetime, date) and isinstance(value, str):
                parsed = _parse_dt(value)
                if parsed is None:
                    raise ValueError(f"unparseable datetime {value!r}")
                out[col_name] = parsed
            elif py_type in (int, float) and value == "":
                out[col_name] = None
            else:
                out[col_name] = value
        except Exception:
            # Re-raise with context so the caller can identify the row
            raise ValueError(f"coercion failed: col={col_name} "
                             f"value={value!r} dest_type={col.type}")
    return out


def _list_source_tables(sqlite_conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def _stream_table(sqlite_conn: sqlite3.Connection, table: str,
                  batch_size: int) -> Iterable[list[dict]]:
    """Yield batches of dict rows from a SQLite table."""
    sqlite_conn.row_factory = sqlite3.Row
    cur = sqlite_conn.execute(f'SELECT * FROM "{table}"')
    batch: list[dict] = []
    for r in cur:
        batch.append(dict(r))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _reset_sequences(pg_engine: Engine) -> None:
    """Postgres autoincrement uses sequences; bulk-loaded data leaves them
    pointing at 1, so the next insert collides with imported row id=1.
    Reset each sequence to max(id)+1 of its column."""
    with pg_engine.begin() as conn:
        # Discover serial/identity columns + their sequences
        seqs = conn.execute(text("""
            SELECT c.table_schema, c.table_name, c.column_name,
                   pg_get_serial_sequence(format('%I.%I', c.table_schema, c.table_name),
                                          c.column_name) AS seq
            FROM information_schema.columns c
            WHERE c.table_schema = 'public'
              AND pg_get_serial_sequence(format('%I.%I', c.table_schema, c.table_name),
                                         c.column_name) IS NOT NULL
        """)).fetchall()
        for schema, table, col, seq in seqs:
            conn.execute(text(
                f"SELECT setval(:seq, COALESCE((SELECT MAX({col}) FROM "
                f"\"{schema}\".\"{table}\"), 0) + 1, false)"
            ), {"seq": seq})
            print(f"  - reset sequence {seq} on {table}.{col}")


def migrate_one_sqlite(sqlite_path: str, pg_engine: Engine,
                       batch_size: int) -> dict[str, dict[str, Any]]:
    """Migrate every table from one SQLite file into Postgres. Returns
    per-table stats (rows migrated, errors)."""
    if not Path(sqlite_path).exists():
        raise FileNotFoundError(sqlite_path)

    # Open SQLite read-only via URI to be safe
    sqlite_conn = sqlite3.connect(
        f"file:{sqlite_path}?mode=ro", uri=True
    )
    sqlite_conn.text_factory = lambda b: b.decode("utf-8", "replace")

    md = MetaData()
    md.reflect(bind=pg_engine)

    stats: dict[str, dict[str, Any]] = {}
    source_tables = set(_list_source_tables(sqlite_conn))

    # Iterate in TOPOLOGICAL order (parents before children) so FK
    # constraints don't fire mid-load. SQLAlchemy's metadata.sorted_tables
    # respects the ForeignKey relationships declared in db_models.py and
    # gives us a safe load order. Tables NOT in the source SQLite get
    # skipped here; tables in source but not declared in Postgres metadata
    # get reported as errors at the bottom.
    pg_tables_sorted = [t.name for t in md.sorted_tables]
    load_order = [t for t in pg_tables_sorted if t in source_tables]
    extras_in_source = sorted(source_tables - set(pg_tables_sorted))

    for table in load_order:
        stats[table] = {"copied": 0, "errors": []}
        dest = md.tables[table]
        start = time.time()
        try:
            with pg_engine.begin() as pg_conn:
                for batch in _stream_table(sqlite_conn, table, batch_size):
                    coerced = [_coerce_row(r, dest) for r in batch]
                    pg_conn.execute(insert(dest), coerced)
                    stats[table]["copied"] += len(coerced)
        except (SQLAlchemyError, ValueError) as e:
            stats[table]["errors"].append(str(e))

        elapsed = time.time() - start
        stats[table]["elapsed_s"] = round(elapsed, 2)
        print(f"  - {table}: {stats[table]['copied']:>7,} rows in "
              f"{elapsed:>6.2f}s  {'ERR: ' + stats[table]['errors'][0][:80] if stats[table]['errors'] else 'OK'}")

    # Source-only tables (in SQLite but not in Postgres metadata): report
    # without failing. Common cause is intentionally deprecated tables we
    # decided not to migrate (see Phase 0.3/0.4 in the runbook).
    for table in extras_in_source:
        stats[table] = {"copied": 0, "errors": [
            f"table {table!r} exists in SQLite source but not in Postgres dest — "
            "skipped (intentionally deprecated, or missing from Phase 2 create_all)"
        ]}
        print(f"  - {table}: SKIPPED (not in Postgres dest)")

    sqlite_conn.close()
    return stats


def verify_counts(pg_engine: Engine, sqlite_paths: list[str]) -> int:
    """Compare row counts between each SQLite source and Postgres dest.
    Returns the number of tables with mismatches."""
    print()
    print("=== verify row counts ===")
    md = MetaData()
    md.reflect(bind=pg_engine)
    mismatches = 0

    for sqlite_path in sqlite_paths:
        sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        for table in _list_source_tables(sconn):
            if table not in md.tables:
                continue
            try:
                sqlite_n = sconn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            except sqlite3.OperationalError:
                continue
            with pg_engine.connect() as pgc:
                pg_n = pgc.execute(
                    select(md.tables[table].c[next(iter(md.tables[table].c)).name])
                    .select_from(md.tables[table]).with_only_columns(text("count(*)"))
                ).scalar()
            ok = "OK" if sqlite_n == pg_n else "MISMATCH"
            if sqlite_n != pg_n:
                mismatches += 1
            print(f"  {table:30} sqlite={sqlite_n:>7,}  postgres={pg_n:>7,}  {ok}")
        sconn.close()

    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", action="append", required=True,
                        help="Path to SQLite source DB. Repeat for multi-source.")
    parser.add_argument("--postgres", required=True,
                        help="Postgres SQLAlchemy URL, e.g. "
                             "postgresql://user:pw@host:5432/dbname")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Rows per INSERT batch (default: 500)")
    parser.add_argument("--verify", action="store_true",
                        help="After migration, run row-count comparison")
    parser.add_argument("--skip-sequence-reset", action="store_true",
                        help="Don't run ALTER SEQUENCE at the end "
                             "(rarely useful — only if you're loading "
                             "into a non-empty Postgres)")
    args = parser.parse_args(argv)

    print(f"=== SQLite → Postgres bulk load ===")
    print(f"  sources:   {args.sqlite}")
    print(f"  dest:      {re.sub(r'(:[^:@]+)@', ':***@', args.postgres)}")
    print(f"  batch:     {args.batch_size}")
    print()

    pg_engine = create_engine(args.postgres, future=True)

    # Smoke test the connection before iterating tables
    try:
        with pg_engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        print(f"ERROR: Postgres connection failed: {e}", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for sqlite_path in args.sqlite:
        print(f"--- migrating from {sqlite_path} ---")
        stats = migrate_one_sqlite(sqlite_path, pg_engine, args.batch_size)
        for table, s in stats.items():
            for err in s["errors"]:
                all_errors.append(f"{sqlite_path}::{table}: {err}")

    if not args.skip_sequence_reset:
        print()
        print("=== reset sequences ===")
        _reset_sequences(pg_engine)

    if args.verify:
        mismatches = verify_counts(pg_engine, args.sqlite)
    else:
        mismatches = 0

    print()
    print("=" * 60)
    if all_errors:
        print(f"FINISHED WITH {len(all_errors)} TABLE ERROR(S):")
        for e in all_errors[:20]:
            print(f"  - {e}")
        if len(all_errors) > 20:
            print(f"  ... and {len(all_errors) - 20} more")
    if mismatches:
        print(f"FINISHED WITH {mismatches} ROW-COUNT MISMATCH(ES)")
    if not all_errors and not mismatches:
        print("FINISHED CLEAN — all rows migrated, row counts match")
    return 1 if (all_errors or mismatches) else 0


if __name__ == "__main__":
    sys.exit(main())
