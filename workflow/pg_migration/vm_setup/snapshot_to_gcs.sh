#!/usr/bin/env bash
# snapshot_to_gcs.sh — runs on mes-testing (SQLite source side)
#
# Every cron tick: produce a WAL-consistent SQLite snapshot via sqlite3 .backup
# and upload to gs://msp-mes-backups/snapshots/mes_data_snapshot.db. The matching
# puller on mes-testing-pg fetches this object on its own cron and the
# replicator daemon reads it.
#
# sqlite3 .backup is REQUIRED — a raw cp of a WAL-mode DB opens read-only on
# the destination (Phase 3 gotcha). Confirmed-good pattern from bulk load.
#
# Atomic upload: write to .new in GCS first, then rename. Reader's puller does
# the same on its side (download to .tmp, then mv).

set -euo pipefail

SRC_DB="/opt/mes/data/mes_data.db"
SNAPSHOT="/tmp/mes_data_snapshot.db"
GCS_FINAL="gs://msp-mes-backups/snapshots/mes_data_snapshot.db"
GCS_STAGE="gs://msp-mes-backups/snapshots/mes_data_snapshot.db.new"

# 1. WAL-consistent local snapshot
sqlite3 "${SRC_DB}" ".backup ${SNAPSHOT}"

# 2. Upload to stage object then rename — readers never see a half-uploaded file
gsutil -q cp "${SNAPSHOT}" "${GCS_STAGE}"
gsutil -q mv "${GCS_STAGE}" "${GCS_FINAL}"

# 3. Cleanup local snapshot to keep /tmp small
rm -f "${SNAPSHOT}"
