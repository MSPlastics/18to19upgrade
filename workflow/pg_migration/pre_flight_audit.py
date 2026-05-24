#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_flight_audit.py — Phase 0 of POSTGRES_MIGRATION_RUNBOOK

Audits one or more SQLite databases for issues that would block or
silently corrupt a migration to PostgreSQL.

Surface area:
  - row counts per table (sanity baseline)
  - foreign-key orphans (rows whose FK target doesn't exist) — Postgres
    enforces FKs strictly; SQLite usually doesn't
  - column type mismatches (SQLite stores any type in any column;
    Postgres rejects)
  - datetime format inconsistency within the same column (Z vs no-Z,
    seconds vs sub-seconds, etc.) — SQLAlchemy can handle a single
    format per column but mixed formats cause silent parse failures
  - duplicate primary keys (rare in practice, would fail bulk load)
  - tables that exist in multiple input DBs with same name but different
    row counts — surfaces the consolidation decision

Usage:
  ./pre_flight_audit.py \\
      --db /opt/mes/data/mes_data.db \\
      --db /opt/mes/data/local_db.sqlite \\
      --db /opt/mes/data/mes_schedule.db \\
      --report /tmp/pre_flight_report.md

Exit codes:
  0 — no blockers found, migration can proceed
  1 — blockers found (counts > 0 on any FK orphan / type mismatch /
      duplicate-pk check); review report before proceeding
  2 — operational error (DB unreadable, etc.)

Read-only against the source SQLite files. Never writes to them.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


# Datetime patterns we expect to see in MES data. Mixed formats within
# one column are a problem; consistent format is fine.
DATETIME_PATTERNS = {
    "iso_z":           re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"),
    "iso_no_z":        re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$"),
    "sql_space":       re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$"),
    "date_only":       re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "epoch_seconds":   re.compile(r"^\d{10}(\.\d+)?$"),
    "epoch_ms":        re.compile(r"^\d{13}$"),
}


class AuditReport:
    """Accumulates findings across all DBs. Renders to markdown at the end."""

    def __init__(self) -> None:
        self.dbs: list[dict[str, Any]] = []
        self.blocker_count = 0

    def add_db(self, db_path: str, payload: dict[str, Any]) -> None:
        payload["path"] = db_path
        self.dbs.append(payload)
        # Count anything that would actually fail a migration as a blocker
        for table in payload.get("tables", {}).values():
            self.blocker_count += len(table.get("fk_orphans", []))
            self.blocker_count += len(table.get("type_mismatches", []))
            self.blocker_count += len(table.get("duplicate_pks", []))

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# SQLite → Postgres Pre-Flight Audit Report")
        lines.append("")
        lines.append(f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z")
        lines.append(f"Databases audited: {len(self.dbs)}")
        lines.append(f"Total blockers found: **{self.blocker_count}**")
        lines.append("")
        if self.blocker_count > 0:
            lines.append("> :warning: blockers exist. Each must be remediated before"
                         " bulk migration. See per-DB sections below for specifics.")
        else:
            lines.append("> :white_check_mark: no blockers. Migration can proceed to Phase 1.")
        lines.append("")

        # Cross-DB consolidation check
        cross = _cross_db_table_overlap(self.dbs)
        if cross:
            lines.append("## Tables that exist in multiple DBs (consolidation decision)")
            lines.append("")
            lines.append("| Table | DB | rows |")
            lines.append("|---|---|---|")
            for name, occurrences in sorted(cross.items()):
                for db, rows in occurrences:
                    lines.append(f"| {name} | {db} | {rows} |")
            lines.append("")
            lines.append("Decide for each overlapping table: which DB is the source"
                         " of truth? See Phase 0.3/0.4 in the runbook.")
            lines.append("")

        for db in self.dbs:
            lines.append(f"## DB: `{db['path']}`")
            lines.append("")
            lines.append(f"- Tables: {len(db['tables'])}")
            lines.append(f"- Total rows: {sum(t['row_count'] for t in db['tables'].values()):,}")
            lines.append(f"- Pragmas: {db['pragmas']}")
            lines.append("")
            if not db["tables"]:
                lines.append("_(empty — no tables found, classify as deprecated)_")
                lines.append("")
                continue

            lines.append("### Per-table summary")
            lines.append("")
            lines.append("| Table | rows | FK orphans | type mismatches | datetime format issues | duplicate PKs |")
            lines.append("|---|---|---|---|---|---|")
            for name in sorted(db["tables"]):
                t = db["tables"][name]
                lines.append(
                    f"| {name} | {t['row_count']:,} | "
                    f"{len(t['fk_orphans'])} | "
                    f"{len(t['type_mismatches'])} | "
                    f"{len(t['datetime_format_issues'])} | "
                    f"{len(t['duplicate_pks'])} |"
                )
            lines.append("")

            # Detail sections for any table with issues
            for name in sorted(db["tables"]):
                t = db["tables"][name]
                if not (t["fk_orphans"] or t["type_mismatches"]
                        or t["datetime_format_issues"] or t["duplicate_pks"]):
                    continue
                lines.append(f"#### `{name}` — issues")
                lines.append("")
                if t["fk_orphans"]:
                    lines.append("**FK orphans** (would fail Postgres FK constraint):")
                    for o in t["fk_orphans"]:
                        lines.append(f"- column `{o['column']}` → `{o['referenced_table']}.{o['referenced_column']}`:"
                                     f" {o['orphan_count']} orphan rows")
                    lines.append("")
                if t["type_mismatches"]:
                    lines.append("**Type mismatches** (declared type vs actual stored types):")
                    for m in t["type_mismatches"]:
                        lines.append(f"- column `{m['column']}` declared `{m['declared']}`,"
                                     f" found types: {m['actual_types']} (sample: `{m['sample']}`)")
                    lines.append("")
                if t["datetime_format_issues"]:
                    lines.append("**Datetime format inconsistencies** (mixed formats in one column):")
                    for d in t["datetime_format_issues"]:
                        lines.append(f"- column `{d['column']}`: formats seen = {d['formats']}")
                    lines.append("")
                if t["duplicate_pks"]:
                    lines.append("**Duplicate primary keys** (would fail Postgres PK):")
                    for d in t["duplicate_pks"]:
                        lines.append(f"- pk={d['pk']}: {d['count']} occurrences")
                    lines.append("")
        return "\n".join(lines)


def _cross_db_table_overlap(dbs: list[dict[str, Any]]) -> dict[str, list[tuple[str, int]]]:
    """Tables whose name appears in more than one of the audited DBs."""
    by_table: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for db in dbs:
        for name, t in db["tables"].items():
            by_table[name].append((db["path"], t["row_count"]))
    return {k: v for k, v in by_table.items() if len(v) > 1}


def audit_db(db_path: str) -> dict[str, Any]:
    """Run all audit checks against one SQLite file."""
    if not Path(db_path).exists():
        return {"tables": {}, "pragmas": {}, "error": "file not found"}

    conn = sqlite3.connect(db_path)
    # text_factory keeps us safe against any cp1252/etc bytes that snuck in
    conn.text_factory = lambda b: b.decode("utf-8", "replace")

    pragmas = {}
    for p in ("journal_mode", "busy_timeout", "synchronous", "foreign_keys"):
        try:
            pragmas[p] = conn.execute(f"PRAGMA {p}").fetchone()[0]
        except Exception as e:
            pragmas[p] = f"ERR: {e}"

    tables: dict[str, dict[str, Any]] = {}
    table_names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]

    for name in table_names:
        tables[name] = _audit_table(conn, name)

    conn.close()
    return {"tables": tables, "pragmas": pragmas}


def _audit_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Audit one table. All checks are read-only."""
    info = {
        "row_count": 0,
        "columns": [],
        "fk_orphans": [],
        "type_mismatches": [],
        "datetime_format_issues": [],
        "duplicate_pks": [],
    }

    # Row count
    try:
        info["row_count"] = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    except sqlite3.OperationalError as e:
        info["error"] = str(e)
        return info

    # Schema
    cols = list(conn.execute(f'PRAGMA table_info("{table}")'))
    # Columns: (cid, name, type, notnull, dflt_value, pk)
    info["columns"] = [
        {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
        for c in cols
    ]
    pk_cols = [c[1] for c in cols if c[5]]

    # Skip expensive checks on empty tables
    if info["row_count"] == 0:
        return info

    # FK orphans
    fks = list(conn.execute(f'PRAGMA foreign_key_list("{table}")'))
    # (id, seq, ref_table, from_col, to_col, on_update, on_delete, match)
    for fk in fks:
        ref_table, from_col, to_col = fk[2], fk[3], fk[4]
        # Skip if ref table doesn't exist (broken schema, but not our problem)
        ref_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (ref_table,)
        ).fetchone()
        if not ref_exists:
            continue
        try:
            orphan_count = conn.execute(
                f'SELECT count(*) FROM "{table}" '
                f'WHERE "{from_col}" IS NOT NULL '
                f'  AND "{from_col}" NOT IN (SELECT "{to_col}" FROM "{ref_table}")'
            ).fetchone()[0]
            if orphan_count > 0:
                info["fk_orphans"].append({
                    "column": from_col,
                    "referenced_table": ref_table,
                    "referenced_column": to_col,
                    "orphan_count": orphan_count,
                })
        except sqlite3.OperationalError:
            # Schema quirk — column missing or similar. Log skipped.
            continue

    # Type mismatch: for each column with a declared type, sample values
    # and check actual SQLite storage class
    for col in info["columns"]:
        if not col["type"]:
            continue
        mismatch = _check_column_types(conn, table, col)
        if mismatch:
            info["type_mismatches"].append(mismatch)

    # Datetime format issues: any column whose name hints at being a
    # timestamp (or declared TIMESTAMP/DATETIME), check for mixed formats
    for col in info["columns"]:
        if not _looks_like_datetime_column(col):
            continue
        dt_issue = _check_datetime_consistency(conn, table, col["name"])
        if dt_issue:
            info["datetime_format_issues"].append(dt_issue)

    # Duplicate PKs — Postgres won't accept these
    if pk_cols:
        pk_expr = ", ".join(f'"{c}"' for c in pk_cols)
        try:
            dupes = conn.execute(
                f'SELECT {pk_expr}, count(*) FROM "{table}" '
                f'GROUP BY {pk_expr} HAVING count(*) > 1 LIMIT 10'
            ).fetchall()
            for d in dupes:
                pk_val = d[:-1]
                cnt = d[-1]
                info["duplicate_pks"].append({"pk": pk_val, "count": cnt})
        except sqlite3.OperationalError:
            pass

    return info


def _check_column_types(conn, table: str, col: dict) -> dict | None:
    """Sample column values, return mismatch dict if storage class doesn't match declared type."""
    declared = col["type"].upper()
    # Map SQLite declared type to expected storage class
    expected = {
        "INTEGER": {"integer"},
        "INT": {"integer"},
        "BIGINT": {"integer"},
        "BOOLEAN": {"integer"},
        "REAL": {"real", "integer"},
        "FLOAT": {"real", "integer"},
        "NUMERIC": {"real", "integer"},
        "TEXT": {"text"},
        "VARCHAR": {"text"},
        "CHAR": {"text"},
        "JSON": {"text"},
        "DATETIME": {"text"},
        "TIMESTAMP": {"text"},
        "DATE": {"text"},
        "BLOB": {"blob"},
    }
    # Strip column size like VARCHAR(255)
    base = declared.split("(")[0].strip()
    allowed = expected.get(base)
    if not allowed:
        return None  # exotic type — leave alone

    # Sample up to 1000 non-null rows
    try:
        rows = conn.execute(
            f'SELECT "{col["name"]}", typeof("{col["name"]}") FROM "{table}" '
            f'WHERE "{col["name"]}" IS NOT NULL LIMIT 1000'
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    actual_types = set()
    bad_sample = None
    for val, t in rows:
        actual_types.add(t)
        if t not in allowed and bad_sample is None:
            bad_sample = str(val)[:60]
    bad_types = actual_types - allowed
    if bad_types:
        return {
            "column": col["name"],
            "declared": declared,
            "actual_types": sorted(actual_types),
            "sample": bad_sample,
        }
    return None


def _looks_like_datetime_column(col: dict) -> bool:
    t = (col["type"] or "").upper()
    if "DATE" in t or "TIME" in t or "TIMESTAMP" in t:
        return True
    name = col["name"].lower()
    return any(hint in name for hint in ("created_at", "updated_at",
                                          "_at", "_time", "date_"))


def _check_datetime_consistency(conn, table: str, column: str) -> dict | None:
    """Return issue dict if column has values matching multiple format patterns."""
    try:
        rows = conn.execute(
            f'SELECT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT 500'
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    formats_seen = set()
    for (val,) in rows:
        if val is None:
            continue
        s = str(val)
        matched = False
        for fmt_name, pat in DATETIME_PATTERNS.items():
            if pat.match(s):
                formats_seen.add(fmt_name)
                matched = True
                break
        if not matched:
            formats_seen.add("unknown")

    if len(formats_seen) > 1:
        return {"column": column, "formats": sorted(formats_seen)}
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", action="append", required=True,
                        help="Path to SQLite DB. Pass multiple times for multiple DBs.")
    parser.add_argument("--report", required=True,
                        help="Output path for the markdown report.")
    parser.add_argument("--json", action="store_true",
                        help="Also write a .json sibling of the report for tooling.")
    args = parser.parse_args(argv)

    report = AuditReport()
    for db_path in args.db:
        try:
            payload = audit_db(db_path)
        except Exception as e:
            print(f"ERROR auditing {db_path}: {e}", file=sys.stderr)
            return 2
        report.add_db(db_path, payload)

    Path(args.report).write_text(report.to_markdown(), encoding="utf-8")
    print(f"Report written: {args.report}")

    if args.json:
        json_path = Path(args.report).with_suffix(".json")
        json_path.write_text(json.dumps(report.dbs, indent=2, default=str),
                             encoding="utf-8")
        print(f"JSON written: {json_path}")

    print(f"Total blockers: {report.blocker_count}")
    return 1 if report.blocker_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
