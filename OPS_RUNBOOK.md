# OPS_RUNBOOK — operating the Postgres-backed MES stacks

Operational reference for the running Postgres infrastructure: backups, restores, health monitoring, failure modes. Treat as a flight manual — most days you won't need it, but when you do it has to be correct.

For *building* a new environment, see [NEW_STAGING_RUNBOOK.md](NEW_STAGING_RUNBOOK.md).
For the *original migration* history + rationale, see [POSTGRES_MIGRATION_RUNBOOK.md](POSTGRES_MIGRATION_RUNBOOK.md).

---

## What you have running (snapshot)

| Layer | Resource | Where |
|---|---|---|
| Database | Cloud SQL `mes-pg-staging` (Postgres 16, HA, PITR) | GCP project `superb-metric-492315-r5`, region `us-central1` |
| Auth Proxy | `cloud-sql-proxy.service` on each VM | `mes-testing-pg`, future staging VMs |
| MES web | `mes.service` (gunicorn) + `nginx` | per-VM |
| Replication | `mes-pg-replicator.service` (60s), `mes-pg-verifier.service` (300s, /health on :5001) | replicator-side VMs |
| Snapshot transport | `snapshot_to_gcs.sh` cron (source VM), `pull_snapshot_from_gcs.sh` cron (replica VM), GCS bucket `gs://msp-mes-backups/snapshots/` | per-VM crons + GCS |
| Backups | Cloud SQL PITR + daily snapshots; pg_dump cron → `gs://msp-mes-backups/postgres/` | GCP-managed + per-VM cron |

---

## Daily checks (~30 seconds)

```bash
# 1. Verifier reports green?
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    curl -s http://127.0.0.1:5001/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"verifier:\", d[\"status\"])'"

# 2. Both MES URLs return 200?
curl -sk -o /dev/null -w "sqlite_stack=%{http_code}\n" https://34.67.173.228.nip.io/api/health
curl -sk -o /dev/null -w "postgres_stack=%{http_code}\n" https://34.57.35.195.nip.io/api/health

# 3. Backups still running?
gsutil ls -l gs://msp-mes-backups/postgres/ | tail -3
gsutil ls -l gs://msp-mes-backups/snapshots/
```

If verifier ≠ green for 2+ consecutive checks, see [Failure: replicator drift](#failure-replicator-drift).

---

## Weekly checks (~5 minutes)

```bash
# Cloud SQL backup completeness
gcloud sql backups list --instance=mes-pg-staging --limit=10

# Disk pressure on the VM
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    df -h / /tmp /var/log; \
    du -sh /opt/mes /opt/18to19upgrade /var/log/journal"

# Cloud SQL disk + CPU pressure
gcloud sql instances describe mes-pg-staging --format="value(currentDiskSize,maxDiskSize,settings.tier)"

# Cost YTD on the instance (lazy check)
# Cloud SQL: ~$135/mo for current tier. Anything over ~$150 means something changed.
```

---

## Backups & restores

### What's backing up what

| Mechanism | Cadence | Retention | Where it lives |
|---|---|---|---|
| Cloud SQL automated backup | 1×/day @ 07:00 UTC | 7 snapshots | Inside Cloud SQL |
| Cloud SQL PITR (WAL) | continuous | 7 days | Inside Cloud SQL |
| `pg_dump` cron on VM | every 15 min | until you delete | `gs://msp-mes-backups/postgres/` |
| SQLite snapshot cron | every 60s | last-only (overwritten) | `gs://msp-mes-backups/snapshots/mes_data_snapshot.db` |

The Cloud SQL backups are the **primary** recovery mechanism. The `pg_dump` cron is independent insurance — if the whole project got nuked you'd still have hourly-ish dumps in GCS.

### PITR restore (point-in-time)

Use when something bad happened at a known time and you want to clone the DB to its state just before that moment.

```bash
# Restore mes-pg-staging to its state at 22:30 UTC on 2026-05-24,
# cloning into a NEW instance (Google won't overwrite live data)
gcloud sql instances clone mes-pg-staging mes-pg-restored \
    --point-in-time='2026-05-24T22:30:00.000Z' \
    --project=superb-metric-492315-r5

# Watch it provision (takes ~5-10 min)
gcloud sql operations list --instance=mes-pg-restored --limit=5
```

Once `mes-pg-restored` is RUNNABLE:

1. Connect to it (Cloud SQL Studio works) and verify the data looks right at that moment
2. Either:
   - **Cut over to it**: update `/etc/mes-pg.env` on the VM to point `DATABASE_URL` at the new instance (and the `cloud-sql-proxy.service` to use the new connection name); restart `cloud-sql-proxy` + `mes`
   - **Cherry-pick rows**: dump specific tables from `mes-pg-restored`, restore into `mes-pg-staging`
3. When done, delete the restored instance: `gcloud sql instances delete mes-pg-restored --quiet`

### Daily snapshot restore

If PITR isn't fine-grained enough (or you want to roll back further):

```bash
# List snapshots
gcloud sql backups list --instance=mes-pg-staging --limit=10

# Restore a specific one
gcloud sql backups restore <BACKUP_ID> --restore-instance=mes-pg-staging \
    --backup-instance=mes-pg-staging
```

⚠️ This *overwrites* `mes-pg-staging` in place. Disruptive. Usually you want PITR-clone instead.

### Manual on-demand backup (before risky work)

```bash
gcloud sql backups create --instance=mes-pg-staging \
    --description="pre-<whatever-you're-about-to-do>"
```

Takes ~2-5 min. Counts against your retention (so old ones drop earlier).

### Restoring from `gs://msp-mes-backups/postgres/` pg_dump

Disaster scenario: the whole project / Cloud SQL is gone.

```bash
# 1. Provision a fresh instance (use NEW_STAGING_RUNBOOK.md or provision_cloud_sql.sh)
./provision_cloud_sql.sh mes-pg-recovery mes-pg-recovery-password

# 2. Pull the most recent dump
gsutil cp $(gsutil ls gs://msp-mes-backups/postgres/ | tail -1) /tmp/recovery.sql.gz
gunzip /tmp/recovery.sql.gz

# 3. Restore (run from a VM that can reach the new instance via proxy)
psql "$DATABASE_URL" < /tmp/recovery.sql
```

---

## Health monitoring

### Verifier `/health` endpoint

`mes-pg-verifier.service` exposes `:5001/health` on `mes-testing-pg`. Returns:
- 200 + `{"status": "green", ...}` — all 17 tables in sync
- 503 + `{"status": "drift", ...}` — drift detected on at least one table

Endpoint is local-only (binds 0.0.0.0 on the VM but firewall doesn't expose :5001). Hit it via SSH or expose if you want a real dashboard.

### journald logs

Per-service tail:
```bash
sudo journalctl -u mes.service -f                    # MES web app
sudo journalctl -u mes-pg-replicator.service -f      # Replicator
sudo journalctl -u mes-pg-verifier.service -f        # Verifier
sudo journalctl -u cloud-sql-proxy.service -f        # Proxy
sudo journalctl -u nginx.service -n 50               # nginx errors
```

Recent app errors only:
```bash
sudo journalctl -u mes.service --since '1 hour ago' --no-pager | grep -E 'Error|Traceback'
```

### Cloud SQL metrics

```bash
# CPU, memory, connections from the last hour (rough)
gcloud monitoring time-series list \
    --filter="metric.type=cloudsql.googleapis.com/database/cpu/utilization \
              AND resource.labels.database_id=mes-pg-staging" \
    --interval-end-time=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --interval-start-time=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
    --format='value(points[0].value.doubleValue)' | tail -5
```

Easier: open Cloud Console → SQL → mes-pg-staging → Monitoring tab.

---

## Failure modes

### Failure: replicator drift

**Symptom:** `/health` returns 503; logs show `verify: DRIFT detected — tables: [...]`.

**Diagnose:**
```bash
# Look at exactly what's drifting
curl -s http://127.0.0.1:5001/health | python3 -m json.tool

# Common causes (in order of likelihood):
# 1. Snapshot pull stalled
ls -la /tmp/mes_data_snapshot.db   # mtime should be < 2 min old
tail /var/log/mes-snapshot-pull.log

# 2. Source-side snapshot push stalled
gcloud compute ssh anthony@mes-testing --zone=us-central1-a --tunnel-through-iap --command="tail /var/log/mes-snapshot.log"
gsutil ls -l gs://msp-mes-backups/snapshots/   # mtime should be < 2 min old

# 3. Replicator hit an error
sudo journalctl -u mes-pg-replicator -n 50 --no-pager | grep -i error

# 4. Schema drift (a Column(JSON) field added to the model but no replicator config update)
sudo journalctl -u mes-pg-replicator -n 50 --no-pager | grep -i 'column.*not in'
```

**Fix:**
- Snapshot stalled → restart the cron: `sudo systemctl restart cron` on whichever side
- Replicator errored → check the error, often a JSON parse failure on a new column. Fix in `sqlite_pg_replicator.py`, redeploy
- New Column(JSON) field → add to `REPLICATION_CONFIG` in replicator + `VERIFIED_TABLES` in verifier

### Failure: MES web app won't start

**Symptom:** `systemctl is-active mes.service` returns `failed` or `activating`.

**Diagnose:**
```bash
sudo journalctl -u mes.service --since '5 minutes ago' --no-pager | tail -50
```

Common causes:
- `ModuleNotFoundError` — venv missing a dep. Reinstall: `sudo /opt/mes/venv/bin/pip install -r /opt/mes/requirements.txt`; if still missing, copy pip freeze from another VM (see NEW_STAGING_RUNBOOK.md gotchas)
- `ProgrammingError` / `OperationalError` — schema drift between Postgres and SQLAlchemy model. Re-run `bootstrap_pg_schema.py` if you added columns
- `connection refused 127.0.0.1:5432` — `cloud-sql-proxy.service` is down. `sudo systemctl status cloud-sql-proxy` then restart
- `permission denied /etc/mes-pg.env` — file got `chmod`-ed wrong. `sudo chmod 600 /etc/mes-pg.env && sudo chown root:root /etc/mes-pg.env`

### Failure: Cloud SQL Auth Proxy down

**Symptom:** Nothing on the VM can hit Postgres; `mes.service` keeps restarting.

**Diagnose:**
```bash
sudo systemctl status cloud-sql-proxy.service
sudo journalctl -u cloud-sql-proxy.service -n 100 --no-pager
```

Common causes:
- `cloudsqlproxy` user lost its home dir → `sudo usermod --create-home --home /home/cloudsqlproxy cloudsqlproxy`
- IAM revoked → re-grant: `gcloud projects add-iam-policy-binding superb-metric-492315-r5 --member=serviceAccount:570594245263-compute@developer.gserviceaccount.com --role=roles/cloudsql.client`
- Cloud SQL instance stopped → `gcloud sql instances describe mes-pg-staging --format='value(state)'`. If `STOPPED`, start it: `gcloud sql instances patch mes-pg-staging --activation-policy=ALWAYS`

### Failure: Cloud SQL out of disk

**Symptom:** Writes start failing with `ERROR: could not extend file ...: No space left on device`.

**Fix:**
```bash
gcloud sql instances patch mes-pg-staging --storage-size=100
# Or enable auto-grow if not already:
gcloud sql instances patch mes-pg-staging --storage-auto-increase
```

Auto-grow is on by default for instances created via `provision_cloud_sql.sh`. If it's not, turn it on now.

### Failure: TLS cert expired

**Symptom:** Browser shows `NET::ERR_CERT_DATE_INVALID`; certbot's renewal cron missed.

**Fix:**
```bash
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    sudo certbot renew --force-renewal && sudo systemctl reload nginx"
```

certbot's auto-renew is in a systemd timer; verify it's running:
```bash
sudo systemctl list-timers | grep certbot
```

### Failure: OAuth login broken after URL change

**Symptom:** `redirect_uri_mismatch` 400 from Google.

**Fix:** Add the new URL's `/login/callback` to the OAuth client in https://console.cloud.google.com/apis/credentials?project=superb-metric-492315-r5 (see NEW_STAGING_RUNBOOK.md step 6).

### Failure: HA failover happened

**Symptom:** Brief outage (~30-90 sec); after recovery, primary is now in us-central1-b (was us-central1-a).

**Action:** Usually none — HA failover is automatic and the proxy reconnects. Verify:
```bash
gcloud sql instances describe mes-pg-staging --format="value(gceZone,settings.availabilityType)"
# gceZone tells you where the primary currently lives
```

Cloud SQL will heal back to a healthy two-zone state on its own. If you want to force the primary back to us-central1-a:
```bash
gcloud sql instances failover mes-pg-staging
```

(This swaps primary ↔ standby. Causes another brief outage. Only do it if you have a specific reason.)

---

## Common operational commands cheatsheet

```bash
# Restart everything on the new VM in the right order
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    sudo bash -c 'systemctl restart cloud-sql-proxy && sleep 3 && \
        systemctl restart mes-pg-replicator mes-pg-verifier mes'"

# Pull the latest MES code on a VM
gcloud compute ssh anthony@<VM> --zone=us-central1-a --tunnel-through-iap --command="\
    sudo bash -c 'cd /opt/mes && git fetch origin && git reset --hard origin/lanes-per-master-fix && \
        systemctl restart mes'"

# Connect to Postgres via psql from inside a VM
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
        PGPASSWORD=\$(echo \$DATABASE_URL | sed -E \"s|.*//mes_app:([^@]+)@.*|\\1|\") \
        psql -h 127.0.0.1 -U mes_app -d mes'"

# Run a one-off Python script against Postgres
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
        /opt/mes/venv/bin/python -c \"<your code>\"'"

# Force-pull a fresh SQLite snapshot
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --tunnel-through-iap --command="\
    sudo /usr/local/bin/pull_snapshot_from_gcs.sh"

# Show all MES URL endpoints
curl -sk https://34.67.173.228.nip.io/api/health     # SQLite stack (operators)
curl -sk https://34.57.35.195.nip.io/api/health      # Postgres stack (validation)
```

---

## When to escalate vs fix-in-place

| Situation | Fix locally | Escalate / pull help |
|---|---|---|
| Verifier red but counts match (timestamp drift) | yes | — |
| Verifier red AND counts drift | yes (replicator restart) | if persists >15 min |
| MES web app down on Postgres stack | yes | — |
| MES web app down on SQLite stack (operators!) | careful — check no operator impact first | yes if operators affected |
| Cloud SQL primary failed over | usually no action needed | if 2nd failover happens within 24 hr |
| Cloud SQL instance unreachable for >5 min | restart proxy first | yes — Google SLA event |
| Data corruption / wrong values in UI | yes (PITR restore to before) | yes — needs RCA |
| Lost access (IAM revoked) | from another owner account | yes if no other owners |

---

## Costs (as of 2026-05)

Monthly recurring (rough):

| Resource | Cost |
|---|---|
| Cloud SQL `mes-pg-staging` (HA Enterprise, db-custom-2-7680) | ~$135 |
| GCE `mes-testing-pg` (e2-medium, us-central1-a) | ~$25 |
| GCE `mes-testing` (e2-small, us-central1-a) | ~$13 |
| GCS storage (~3GB) | <$1 |
| Egress (within region) | <$1 |
| Let's Encrypt | $0 |
| **Total Postgres-related** | **~$160/mo** |

The SQLite stack alone was ~$13/mo (one VM, GCS). The migration adds ~$150/mo for HA + PITR + the validation VM. Worth it for the durability guarantees.

To cut cost in half: drop HA → ~$135 becomes ~$70. Drop validation VM after cutover → save $25. Long-term steady state should be ~$95/mo.
