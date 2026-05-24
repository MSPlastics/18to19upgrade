#!/usr/bin/env python3
"""null_fk_orphans.py — pre-process a SQLite snapshot for Postgres bulk load.

The MES periodic inbound sync truncates + re-inserts work_orders every
5 minutes from Odoo; the autoincrement IDs are not stable across
cycles. Child tables that reference work_orders.id by integer (via
SQLAlchemy ForeignKey) end up with orphaned rows whenever the sync
re-numbers a WO. SQLite doesn't enforce FKs by default so this is
invisible until you try to load the data into Postgres, which DOES
enforce them.

The stable join key is `wo_number` (the string like 'WH/MO/01403'),
which the app actually uses for lineage. The integer FK is best-effort
cache. Setting orphan FKs to NULL preserves all rows and all lineage
without losing audit trail.

Usage:
  ./null_fk_orphans.py /tmp/mes_data_fresh.db

Idempotent. Reports per-table NULL counts.
"""
import sqlite3
import sys


def main(db_path: str) -> int:
    c = sqlite3.connect(db_path)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = c.cursor()

    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]

    total = 0
    for t in tables:
        fks = list(cur.execute(f'PRAGMA foreign_key_list("{t}")'))
        if not fks:
            continue
        col_notnull = {row[1]: row[3] for row in cur.execute(f'PRAGMA table_info("{t}")')}
        for fk in fks:
            # (id, seq, ref_table, from_col, to_col, on_update, on_delete, match)
            ref_table, from_col, to_col = fk[2], fk[3], fk[4]
            if col_notnull.get(from_col):
                # NOT NULL column; can't NULL it. Leave for manual review.
                continue
            # Verify ref table exists
            if not cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (ref_table,),
            ).fetchone():
                continue
            cur.execute(
                f'UPDATE "{t}" SET "{from_col}" = NULL '
                f'WHERE "{from_col}" IS NOT NULL '
                f'  AND "{from_col}" NOT IN (SELECT "{to_col}" FROM "{ref_table}")'
            )
            if cur.rowcount > 0:
                print(f"  {t}.{from_col} -> {ref_table}.{to_col}: NULLed {cur.rowcount}")
                total += cur.rowcount

    c.commit()
    c.close()
    print(f"total rows NULLed: {total}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: null_fk_orphans.py <sqlite-db-path>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
