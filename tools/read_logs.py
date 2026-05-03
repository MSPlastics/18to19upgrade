"""Read recent ir.logging records from prod or staging.

Useful for finding the actual error in a failed module load — Odoo
captures some warnings/errors here, but NOTE: most upgrade failures
are logged to stderr/Odoo.sh's build log, not into ir.logging.

Usage:
    python read_logs.py [--target prod|staging] [--module MODULE_NAME] [--limit 30]
"""
import argparse
from _common import connect, make_caller


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["prod", "staging"], default="staging")
    parser.add_argument("--module", help="filter messages mentioning this module")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    uid, models, db, api_key = connect(args.target)
    call = make_caller(uid, models, db, api_key)

    domain = [("level", "in", ["ERROR", "WARNING", "CRITICAL"])]
    if args.module:
        domain.append(("message", "ilike", args.module))

    logs = call("ir.logging", "search_read",
                [domain],
                {"fields": ["create_date", "level", "name", "func", "line", "message"],
                 "order": "create_date desc",
                 "limit": args.limit})
    print(f"=== {len(logs)} most recent log records ===\n")
    for log in logs:
        print(f"[{log['create_date']}] {log['level']:<8} {log['name']}")
        print(f"  func={log['func']} line={log['line']}")
        msg = (log["message"] or "")[:600]
        print(f"  {msg}")
        print()


if __name__ == "__main__":
    main()
