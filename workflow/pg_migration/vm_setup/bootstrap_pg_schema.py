#!/usr/bin/env python3
"""bootstrap_pg_schema.py — Phase 2 schema creation on Postgres.

Runs Base.metadata.create_all() against the configured DATABASE_URL,
then reports:
  - Number of tables created
  - Per-table column count
  - Cross-check vs SQLite source (if --sqlite-source provided)
    so we know nothing was silently lost

Safe to re-run: create_all() skips tables that already exist. To
re-bootstrap from scratch, drop the schema first.

Usage on mes-testing-pg:
  sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
      /opt/mes/venv/bin/python /tmp/bootstrap_pg_schema.py \
        --sqlite-source /var/tmp/mes_data.db'

The --sqlite-source path is optional — if you have a SQLite snapshot
on the new VM (we'll scp one over), the parity check runs.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/opt/mes")
import db_models  # noqa: E402
from sqlalchemy import text, inspect  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--sqlite-source",
                   help="Path to SQLite mes_data.db for column-level cross-check")
    args = p.parse_args(argv)

    if db_models._IS_SQLITE:
        print("ERROR: DB_PATH is SQLite, not Postgres. Source /etc/mes-pg.env first.",
              file=sys.stderr)
        return 2

    print(f"=== bootstrap on {db_models.engine.dialect.name} ===")
    print(f"  DATABASE_URL prefix: {db_models.DB_PATH[:40]}...")
    print(f"  models registered: {len(db_models.Base.metadata.tables)}")
    print()

    # ---- create ----
    print("=== create_all() ===")
    before_inspector = inspect(db_models.engine)
    before = set(before_inspector.get_table_names())
    print(f"  tables present before: {len(before)}")
    db_models.Base.metadata.create_all(db_models.engine)

    after_inspector = inspect(db_models.engine)
    after = set(after_inspector.get_table_names())
    created = sorted(after - before)
    print(f"  tables present after:  {len(after)}  (+{len(created)} newly created)")
    print()

    # ---- per-table summary ----
    print("=== per-table column counts (Postgres) ===")
    pg_cols = {}
    for t in sorted(after):
        cols = after_inspector.get_columns(t)
        pg_cols[t] = len(cols)
        print(f"  {t:30} {len(cols):>3} columns")

    # ---- cross-check vs SQLite ----
    if args.sqlite_source:
        if not Path(args.sqlite_source).exists():
            print(f"\nWARN: --sqlite-source {args.sqlite_source} not found, skipping",
                  file=sys.stderr)
            return 0
        print()
        print(f"=== cross-check vs SQLite at {args.sqlite_source} ===")
        sc = sqlite3.connect(f"file:{args.sqlite_source}?mode=ro", uri=True)
        sqlite_tables = [r[0] for r in sc.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        only_in_sqlite = sorted(set(sqlite_tables) - after)
        only_in_pg = sorted(after - set(sqlite_tables))

        print(f"  SQLite has {len(sqlite_tables)} tables")
        print(f"  Postgres has {len(after)} tables")
        if only_in_sqlite:
            print(f"  IN SQLITE BUT NOT POSTGRES: {only_in_sqlite}")
        if only_in_pg:
            print(f"  IN POSTGRES BUT NOT SQLITE: {only_in_pg}")
        if not only_in_sqlite and not only_in_pg:
            print("  table sets match")

        # Per-table column count diff
        print()
        print(f"  {'table':30} sqlite  postgres  diff")
        any_diff = False
        for t in sorted(set(sqlite_tables) & after):
            sc_n = len(list(sc.execute(f'PRAGMA table_info("{t}")').fetchall()))
            pg_n = pg_cols.get(t, 0)
            flag = "" if sc_n == pg_n else "  DIFF"
            if sc_n != pg_n:
                any_diff = True
            print(f"  {t:30} {sc_n:>6}  {pg_n:>8}  {sc_n - pg_n:+d}{flag}")
        if not any_diff:
            print("\n  all shared tables have matching column counts")
        sc.close()

    print()
    print("=== bootstrap done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
