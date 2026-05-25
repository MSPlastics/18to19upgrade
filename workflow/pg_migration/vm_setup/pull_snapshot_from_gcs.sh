#!/usr/bin/env bash
# pull_snapshot_from_gcs.sh — runs on mes-testing-pg (Postgres / replicator side)
#
# Every cron tick: download the latest SQLite snapshot produced by
# snapshot_to_gcs.sh on mes-testing and place it at /tmp/mes_data_snapshot.db
# atomically. The replicator + verifier read from /tmp/mes_data_snapshot.db.
#
# Download to .tmp then mv so the replicator never opens a half-written file.

set -euo pipefail

GCS_SRC="gs://msp-mes-backups/snapshots/mes_data_snapshot.db"
DEST="/tmp/mes_data_snapshot.db"
STAGE="/tmp/mes_data_snapshot.db.dl"

gsutil -q cp "${GCS_SRC}" "${STAGE}"
mv -f "${STAGE}" "${DEST}"
chmod 644 "${DEST}"
