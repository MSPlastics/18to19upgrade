#!/usr/bin/env python3
"""smoke_test_pg.py — verify MES app code sees Postgres via DATABASE_URL.

Run on mes-testing-pg as:
  sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
      /opt/mes/venv/bin/python /tmp/smoke_test_pg.py'
"""
import os
import sys

print(f"DATABASE_URL prefix: {os.environ.get('DATABASE_URL', '')[:35]}...")

sys.path.insert(0, "/opt/mes")
import db_models  # noqa: E402

print(f"  engine dialect: {db_models.engine.dialect.name}")
print(f"  IS_SQLITE flag: {db_models._IS_SQLITE}")
print(f"  models registered on Base: {len(db_models.Base.metadata.tables)}")

from sqlalchemy import text  # noqa: E402

with db_models.engine.connect() as c:
    v = c.execute(text("SELECT version()")).scalar()
    print(f"  SELECT version() -> {v[:60]}")
    schemas = c.execute(text("SELECT current_database(), current_user")).first()
    print(f"  current db / user: {schemas[0]} / {schemas[1]}")
