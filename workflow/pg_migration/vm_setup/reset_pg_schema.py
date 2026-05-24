#!/usr/bin/env python3
"""reset_pg_schema.py — drop + recreate Postgres schema for clean re-load.

Used during Phase 3 iteration when we need to retry the bulk load with
a different source snapshot. NEVER use this once production data is
flowing — it nukes everything.

Refuses to run unless --confirm is passed AND DATABASE_URL points at a
hostname containing 'staging' (defense against accidentally pointing
at prod).

Usage:
  sudo bash -c 'set -a; source /etc/mes-pg.env; set +a;
      /opt/mes/venv/bin/python /tmp/reset_pg_schema.py --confirm'
"""
import argparse
import os
import sys

sys.path.insert(0, "/opt/mes")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true", required=False,
                   help="Required acknowledgment to actually drop tables.")
    args = p.parse_args(argv)

    if not args.confirm:
        print("ERROR: pass --confirm to actually drop tables", file=sys.stderr)
        return 2

    url = os.environ.get("DATABASE_URL", "")
    if "staging" not in url and "127.0.0.1" not in url:
        print(f"ERROR: DATABASE_URL ({url[:40]}...) doesn't look like staging. "
              "Refusing to drop. Override the safety check by editing this script "
              "if you really mean it.", file=sys.stderr)
        return 2

    import db_models
    print(f"=== resetting schema on {db_models.engine.dialect.name} ===")
    print(f"  models registered: {len(db_models.Base.metadata.tables)}")

    db_models.Base.metadata.drop_all(db_models.engine)
    print("  drop_all done")
    db_models.Base.metadata.create_all(db_models.engine)
    print("  create_all done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
