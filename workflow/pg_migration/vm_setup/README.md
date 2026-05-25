# `vm_setup/` — bootstrapping a Postgres-backed MES VM

Artifacts the new Postgres-backed MES VM needs at creation time. Originally written for `mes-testing-pg`; now reusable for any new staging environment via [NEW_STAGING_RUNBOOK.md](../../../NEW_STAGING_RUNBOOK.md).

For operating these VMs day-to-day (backups, PITR, failure modes), see [OPS_RUNBOOK.md](../../../OPS_RUNBOOK.md).

## Files

| File | Purpose | Destination on VM |
|---|---|---|
| `provision_cloud_sql.sh` | **Runs from your workstation.** Creates Cloud SQL HA instance + DB + user + secret + IAM. Idempotent. | n/a (workstation script) |
| `install.sh` | One-time bootstrap on the VM (apt, proxy, repo, venv, env file, backup cron) | run as root: `sudo bash /tmp/install.sh` |
| `cloud-sql-proxy.service` | systemd unit for Cloud SQL Auth Proxy (private IP, port 5432 on localhost) | `/etc/systemd/system/cloud-sql-proxy.service` |
| `backup_postgres.sh` | pg_dump → GCS every 15 min via cron, mirroring SQLite backup cadence | `/opt/cloud-sql-proxy/backup_postgres.sh` |
| `bootstrap_pg_schema.py` | One-shot `Base.metadata.create_all()` for empty Cloud SQL DB | run via venv |
| `reset_pg_schema.py` | `drop_all` + `create_all` (refuses unless hostname is staging/127.0.0.1) | run via venv |
| `null_fk_orphans.py` | Idempotent FK orphan NULL on a SQLite source file (Phase 0 cleanup) | run via venv |
| `smoke_test_pg.py` | Confirms app code sees Postgres dialect via DATABASE_URL | run via venv |
| `snapshot_to_gcs.sh` | Source-side cron: WAL-consistent `sqlite3 .backup` → GCS every 60s | `/usr/local/bin/snapshot_to_gcs.sh` |
| `pull_snapshot_from_gcs.sh` | Replica-side cron: download GCS snapshot atomically every 60s | `/usr/local/bin/pull_snapshot_from_gcs.sh` |
| `mes-pg-replicator.service` | systemd unit for the SQLite→Postgres replicator daemon (60s) | `/etc/systemd/system/mes-pg-replicator.service` |
| `mes-pg-verifier.service` | systemd unit for the parity verifier daemon (300s, `/health` on :5001) | `/etc/systemd/system/mes-pg-verifier.service` |
| `mes.service` | systemd unit for the MES gunicorn web app (two EnvironmentFiles) | `/etc/systemd/system/mes.service` |
| `nginx-mes-site.template` | Pre-certbot nginx site config; replace `__HOSTNAME__` before installing | `/etc/nginx/sites-available/mes-pg` |
| `README.md` | This file | reference |

## Order of operations on the VM

The canonical end-to-end recipe lives in [NEW_STAGING_RUNBOOK.md](../../../NEW_STAGING_RUNBOOK.md). Quick version:

```bash
# 1. (workstation) Provision Cloud SQL
./provision_cloud_sql.sh mes-pg-<env> mes-pg-<env>-password

# 2. (workstation) Create the VM
gcloud compute instances create mes-<env>-pg --zone=us-central1-a ...

# 3. (workstation) Run install.sh on the VM (idempotent)
gcloud compute scp install.sh anthony@mes-<env>-pg:/tmp/install.sh --zone=us-central1-a
gcloud compute ssh anthony@mes-<env>-pg --zone=us-central1-a \
    --command="sudo bash /tmp/install.sh mes-pg-<env> mes-pg-<env>-password"

# 4. Set up the MES web stack (nginx + cert + mes.service) — see NEW_STAGING_RUNBOOK.md step 5
# 5. Update OAuth redirect URIs in console (manual)
# 6. (Optional) Wire replicator + verifier if this is a passive replica
```

The script self-verifies (smoke-tests the proxy connection, prints git HEAD, confirms `/etc/mes-pg.env` permissions). It is **read-only on Cloud SQL** — it never runs DDL or schema bootstrap. That happens manually next:

```bash
# 3. Initial schema bootstrap (Phase 2)
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a --command="
    cd /opt/mes && set -a && source /etc/mes-pg.env && set +a && \
    ./venv/bin/python -c 'from db_models import init_db; init_db()' && \
    psql -h 127.0.0.1 -U mes_app -d mes -c '\\dt'
"
```

After that, the VM is ready for Phase 3 bulk load via `sqlite_to_pg_migrate.py`.

## Why systemd + Auth Proxy and not direct private-IP psql?

We *could* point `DATABASE_URL` at the Cloud SQL private IP directly (e.g. `postgresql://...@10.82.240.3:5432/mes`). The Auth Proxy adds value:

- **IAM auth** is supported through the proxy (we use password today for simplicity; can switch to IAM later without changing app code)
- **Automatic certificate management** — TLS to Cloud SQL is enforced; the proxy handles cert rotation invisibly
- **Localhost connection ergonomics** — `psql -h 127.0.0.1` works from any tool, no IP juggling if Cloud SQL's private IP ever changes
- **Connection observability** — proxy logs every connection attempt to journalctl
- **Future-proof for Cloud SQL changes** — if Google ever changes the connection contract, the proxy abstracts it

Cost: zero. The proxy is a few-MB Go binary, holds a couple of MB of RAM.
