#!/usr/bin/env bash
# provision_cloud_sql.sh — provision a new Postgres-backed staging instance.
#
# Creates: Cloud SQL HA Postgres instance + database + app user + Secret
# Manager entry for the app password + grants the Compute SA the IAM roles
# it needs to use the proxy and read the secret. Idempotent: re-running with
# the same args is a no-op for resources that already exist.
#
# Run from your workstation (not a VM) — needs your owner-level credentials
# for the gcloud sql + IAM commands.
#
# Cost note: HA Enterprise instance is ~$135/mo. db-custom-2-7680 = 2 vCPU,
# 7.5GB RAM. Adjust --tier if you want smaller for dev-only.
#
# Usage:
#   ./provision_cloud_sql.sh INSTANCE_NAME SECRET_NAME [TIER]
# Example:
#   ./provision_cloud_sql.sh mes-pg-dev mes-pg-dev-password
#
# After this finishes, follow NEW_STAGING_RUNBOOK.md to build the VM.

set -euo pipefail

INSTANCE_NAME="${1:?usage: $0 INSTANCE_NAME SECRET_NAME [TIER]}"
SECRET_NAME="${2:?usage: $0 INSTANCE_NAME SECRET_NAME [TIER]}"
TIER="${3:-db-custom-2-7680}"

PROJECT="superb-metric-492315-r5"
REGION="us-central1"
DB_NAME="mes"
APP_USER="mes_app"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "=== Provisioning Cloud SQL instance: ${INSTANCE_NAME} ==="
echo "  Project:  ${PROJECT}"
echo "  Tier:     ${TIER} (HA, Enterprise edition)"
echo "  Secret:   ${SECRET_NAME}"

# 1. Generate a random app password and stage it in Secret Manager
#    (do this BEFORE creating the instance so we can pipe directly).
APP_PASSWORD="$(openssl rand -base64 33 | tr -d '/+=' | head -c 32)"

if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  Secret ${SECRET_NAME} already exists — adding a new version"
    echo -n "$APP_PASSWORD" | gcloud secrets versions add "$SECRET_NAME" \
        --project="$PROJECT" --data-file=-
else
    echo -n "$APP_PASSWORD" | gcloud secrets create "$SECRET_NAME" \
        --project="$PROJECT" --replication-policy=automatic --data-file=-
fi

# 2. Create the Cloud SQL instance.
#    --edition=ENTERPRISE is REQUIRED. Default in 2026 is Enterprise Plus
#    (db-perf-optimized tiers, ~2x cost). Without this flag, db-custom-*
#    tiers are rejected with "Invalid Tier ... for (ENTERPRISE_PLUS) Edition".
if gcloud sql instances describe "$INSTANCE_NAME" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  Instance ${INSTANCE_NAME} already exists — skipping create"
else
    gcloud sql instances create "$INSTANCE_NAME" \
        --project="$PROJECT" \
        --region="$REGION" \
        --database-version=POSTGRES_16 \
        --edition=ENTERPRISE \
        --tier="$TIER" \
        --availability-type=REGIONAL \
        --storage-type=SSD \
        --storage-size=50 \
        --storage-auto-increase \
        --network=projects/${PROJECT}/global/networks/default \
        --no-assign-ip \
        --enable-bin-log=false \
        --backup-start-time=07:00 \
        --retained-backups-count=7 \
        --retained-transaction-log-days=7 \
        --enable-point-in-time-recovery \
        --maintenance-window-day=SUN \
        --maintenance-window-hour=8 \
        --maintenance-release-channel=production
fi

# 3. Wait for instance to be RUNNABLE
echo "  Waiting for instance to be RUNNABLE..."
until [ "$(gcloud sql instances describe "$INSTANCE_NAME" \
            --project="$PROJECT" --format='value(state)')" = "RUNNABLE" ]; do
    sleep 10
done
echo "  Instance is RUNNABLE"

# 4. Set the postgres-user password (only matters if you ever want to
#    connect as the superuser — typically via Cloud SQL Studio).
gcloud sql users set-password postgres \
    --instance="$INSTANCE_NAME" --project="$PROJECT" \
    --password="$APP_PASSWORD" >/dev/null

# 5. Create the application database
if gcloud sql databases describe "$DB_NAME" --instance="$INSTANCE_NAME" \
    --project="$PROJECT" >/dev/null 2>&1; then
    echo "  Database ${DB_NAME} exists — skipping"
else
    gcloud sql databases create "$DB_NAME" \
        --instance="$INSTANCE_NAME" --project="$PROJECT"
fi

# 6. Create the application user (separate from postgres superuser)
if gcloud sql users list --instance="$INSTANCE_NAME" --project="$PROJECT" \
    --format='value(name)' | grep -q "^${APP_USER}$"; then
    echo "  User ${APP_USER} exists — updating password"
    gcloud sql users set-password "$APP_USER" \
        --instance="$INSTANCE_NAME" --project="$PROJECT" \
        --password="$APP_PASSWORD"
else
    gcloud sql users create "$APP_USER" \
        --instance="$INSTANCE_NAME" --project="$PROJECT" \
        --password="$APP_PASSWORD"
fi

# 7. IAM: Compute SA needs cloudsql.client (proxy) + secretAccessor on the secret
echo "=== IAM grants ==="
gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/cloudsql.client" \
    --condition=None >/dev/null

gcloud secrets add-iam-policy-binding "$SECRET_NAME" --project="$PROJECT" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None >/dev/null

# 8. Final summary
INSTANCE_CONN="$(gcloud sql instances describe "$INSTANCE_NAME" \
    --project="$PROJECT" --format='value(connectionName)')"
PRIVATE_IP="$(gcloud sql instances describe "$INSTANCE_NAME" \
    --project="$PROJECT" --format='value(ipAddresses[?type=PRIVATE].ipAddress)')"

cat <<EOF

============================================================
PROVISIONED — ${INSTANCE_NAME}
  Connection name: ${INSTANCE_CONN}
  Private IP:      ${PRIVATE_IP}
  Database:        ${DB_NAME}
  App user:        ${APP_USER}
  Password secret: gs://${PROJECT}/secrets/${SECRET_NAME} (Secret Manager)

Next: build the VM. See NEW_STAGING_RUNBOOK.md, section "VM bootstrap".
  Pass: INSTANCE_CONN=${INSTANCE_CONN} SECRET_NAME=${SECRET_NAME}
============================================================
EOF
