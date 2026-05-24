#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlite_pg_verifier.py — Phase 4 of POSTGRES_MIGRATION_RUNBOOK

Parity-verification daemon that periodically compares SQLite and Postgres
to confirm the replicator (sqlite_pg_replicator.py) is keeping them in
sync. Independent of the replicator on purpose: a bug in the replicator
should be caught here, not papered over.

For each replicated table, every cycle:

  1. Compare row counts. Equal -> green.

  2. For tables with a watermark column (created_at / updated_at),
     compare a rolling checksum of rows modified in the last
     CHECKSUM_WINDOW seconds. Equal -> green; different -> drift.

  3. Log a structured result line. Optionally serve /health on a tiny
     Flask endpoint so a dashboard / monitor can poll it.

Exit code: always 0 unless the daemon itself crashes — drift is logged,
not raised. Use --once + --strict for CI-style "fail if drift" semantics.

Usage:
  ./sqlite_pg_verifier.py \\
      --sqlite /mnt/mes-testing/data/mes_data.db \\
      --postgres 'postgresql://mes_app:PWD@127.0.0.1:5432/mes' \\
      --interval 300 \\
      --serve-health 5001
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from sqlalchemy import create_engine, MetaData, text
    from sqlalchemy.engine import Engine
except ImportError:
    print("ERROR: sqlalchemy + psycopg required.", file=sys.stderr)
    sys.exit(2)


# Mirrors REPLICATION_CONFIG in sqlite_pg_replicator.py. Kept as a
# separate copy on purpose — if the replicator's config drifts from
# reality, the verifier will surface it.
VERIFIED_TABLES = {
    "master_rolls":        "created_at",
    "pallets":             "created_at",
    "sync_queue":          "created_at",
    "qc_records":          "created_at",
    "qc_reports":          "created_at",
    "scrap_records":       "created_at",
    "compliance_events":   "created_at",
    "line_inventory":      "updated_at",
    "work_orders":         "updated_at",
    "products":            "updated_at",
    "sale_orders":         "updated_at",
    # Tables without a watermark — we count only, can't checksum recents
    "work_centers":        None,
    "employees":           None,
    "silos":               None,
    "boms":                None,
    "label_templates":     None,
    "settings":            None,
}


# Rolling checksum window — compare rows modified in the last N seconds.
# Should be larger than the replicator's interval so replication lag
# doesn't show as drift.
CHECKSUM_WINDOW_SECONDS = 600


_log = logging.getLogger("verifier")
_latest_report: dict = {"status": "starting", "tables": {}}
_latest_report_lock = threading.Lock()


def _sqlite_row_count(sqlite_path: str, table: str) -> int:
    c = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        return c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    except sqlite3.OperationalError:
        return -1
    finally:
        c.close()


def _pg_row_count(pg_engine: Engine, table: str) -> int:
    try:
        with pg_engine.connect() as c:
            return c.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
    except Exception:
        return -1


def _sqlite_recent_checksum(sqlite_path: str, table: str, watermark_col: str,
                            cutoff_iso: str) -> tuple[int, str]:
    """Return (row_count, sha256_hex) for rows where watermark > cutoff_iso.
    Hash is over a deterministic concatenation of all column values per row,
    in column-name order — matches the Postgres side."""
    c = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            f'SELECT * FROM "{table}" WHERE "{watermark_col}" > ? '
            f'ORDER BY "{watermark_col}"',
            (cutoff_iso,)
        ).fetchall()
    except sqlite3.OperationalError as e:
        c.close()
        return -1, str(e)
    h = hashlib.sha256()
    for row in rows:
        for k in sorted(row.keys()):
            h.update(f"{k}={row[k]!r}|".encode("utf-8"))
        h.update(b"\n")
    c.close()
    return len(rows), h.hexdigest()


def _pg_recent_checksum(pg_engine: Engine, table: str, watermark_col: str,
                        cutoff_iso: str) -> tuple[int, str]:
    """Mirror of _sqlite_recent_checksum on the Postgres side."""
    md = MetaData()
    md.reflect(bind=pg_engine, only=[table])
    if table not in md.tables:
        return -1, "table_missing"
    t = md.tables[table]
    if watermark_col not in t.c:
        return -1, "watermark_col_missing"
    try:
        with pg_engine.connect() as c:
            rows = c.execute(
                text(f'SELECT * FROM "{table}" WHERE "{watermark_col}" > '
                     f':cutoff ORDER BY "{watermark_col}"'),
                {"cutoff": cutoff_iso}
            ).mappings().all()
    except Exception as e:
        return -1, str(e)

    h = hashlib.sha256()
    for row in rows:
        for k in sorted(row.keys()):
            h.update(f"{k}={row[k]!r}|".encode("utf-8"))
        h.update(b"\n")
    return len(rows), h.hexdigest()


def verify_once(sqlite_path: str, pg_engine: Engine) -> dict:
    """Run one full verification pass; return structured report dict."""
    cutoff = (datetime.utcnow() - timedelta(seconds=CHECKSUM_WINDOW_SECONDS)
              ).strftime("%Y-%m-%d %H:%M:%S")
    report = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "status": "green",
        "tables": {},
    }

    for table, watermark in VERIFIED_TABLES.items():
        s_count = _sqlite_row_count(sqlite_path, table)
        p_count = _pg_row_count(pg_engine, table)
        row = {
            "sqlite_count": s_count,
            "pg_count": p_count,
            "count_match": s_count == p_count,
        }

        if watermark:
            s_n, s_hash = _sqlite_recent_checksum(sqlite_path, table, watermark, cutoff)
            p_n, p_hash = _pg_recent_checksum(pg_engine, table, watermark, cutoff)
            row["recent_window_seconds"] = CHECKSUM_WINDOW_SECONDS
            row["recent_sqlite_n"] = s_n
            row["recent_pg_n"] = p_n
            row["recent_match"] = (s_n == p_n and s_hash == p_hash)
            if not row["recent_match"]:
                row["s_hash"] = s_hash[:16]
                row["p_hash"] = p_hash[:16]

        if not row["count_match"] or (watermark and not row.get("recent_match")):
            report["status"] = "drift"

        report["tables"][table] = row

    return report


def _log_report(report: dict) -> None:
    if report["status"] == "green":
        _log.info("verify: green — all %d tables in sync",
                  len(report["tables"]))
        return
    # Drift — surface the offending tables
    bad = [t for t, r in report["tables"].items()
           if not r["count_match"] or not r.get("recent_match", True)]
    _log.warning("verify: DRIFT detected — tables: %s", bad)
    for t in bad:
        _log.warning("  %s: %s", t, json.dumps(report["tables"][t]))


def _serve_health(port: int) -> None:
    """Tiny HTTP server exposing /health with the latest report.
    Runs in a background thread."""
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer
    except ImportError:
        _log.error("http.server not available — skipping health endpoint")
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_response(404); self.end_headers(); return
            with _latest_report_lock:
                payload = json.dumps(_latest_report, indent=2).encode("utf-8")
            status = 200 if _latest_report.get("status") == "green" else 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence default request logging
            return

    server = HTTPServer(("0.0.0.0", port), Handler)
    _log.info("Health endpoint listening on :%d/health", port)
    server.serve_forever()


_shutdown = False


def _on_sigterm(signum, frame):  # noqa: ARG001
    global _shutdown
    _log.info("SIGTERM — exiting after current cycle")
    _shutdown = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--postgres", required=True)
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between verification cycles (default: 300)")
    parser.add_argument("--once", action="store_true",
                        help="Single cycle, print report, exit")
    parser.add_argument("--strict", action="store_true",
                        help="When --once: exit 1 if drift detected")
    parser.add_argument("--serve-health", type=int, default=None,
                        help="Expose /health on this port for monitors")
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

    if args.serve_health:
        t = threading.Thread(target=_serve_health, args=(args.serve_health,),
                             daemon=True)
        t.start()

    if args.once:
        report = verify_once(args.sqlite, pg_engine)
        print(json.dumps(report, indent=2, default=str))
        return 1 if (args.strict and report["status"] != "green") else 0

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    _log.info("Verifier started — interval=%ds, %d tables",
              args.interval, len(VERIFIED_TABLES))

    while not _shutdown:
        try:
            report = verify_once(args.sqlite, pg_engine)
            with _latest_report_lock:
                _latest_report.clear()
                _latest_report.update(report)
            _log_report(report)
        except Exception as e:
            _log.error("verify cycle failed: %s", e, exc_info=True)
        for _ in range(args.interval):
            if _shutdown:
                break
            time.sleep(1)

    _log.info("Verifier exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
