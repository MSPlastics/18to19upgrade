#!/usr/bin/env bash
# backup_postgres.sh — pg_dump -> GCS, every 15 min via cron
#
# Mirror of the existing /opt/mes/scripts/backup_db.sh which does the same
# thing for SQLite. Runs as the cloudsqlproxy user so it inherits the GCS
# service-account scope from the VM.

set -euo pipefail

GCP_PROJECT="superb-metric-492315-r5"
PG_SECRET_NAME="mes-pg-app-password"
PG_HOST="127.0.0.1"
PG_PORT="5432"
PG_DB="mes"
PG_USER="mes_app"
BUCKET="gs://msp-mes-backups/postgres"
RETAIN_LOCAL_LATEST_N=3
LOCAL_DIR="/var/tmp/mes-pg-backup"

mkdir -p "$LOCAL_DIR"

TS=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_FILE="$LOCAL_DIR/mes-pg-$TS.dump"

# pg_dump in custom format (-Fc): compressed, parallel-restore-capable,
# self-contained schema+data. Smaller than plain SQL on disk.
PGPASSWORD=$(gcloud secrets versions access latest --secret="$PG_SECRET_NAME" --project="$GCP_PROJECT") \
    pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -Fc -Z 6 -f "$DUMP_FILE"

# Upload to GCS — gsutil inherits VM service account
gsutil -q cp "$DUMP_FILE" "$BUCKET/$(basename "$DUMP_FILE")"

# Keep last N local copies in case GCS is briefly unreachable; the GCS
# copy is the source of truth (it has bucket-level lifecycle policy).
ls -1t "$LOCAL_DIR"/*.dump | tail -n +$((RETAIN_LOCAL_LATEST_N + 1)) | xargs -r rm -f

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backup OK: $(basename "$DUMP_FILE") ($(du -h "$DUMP_FILE" | cut -f1))"
