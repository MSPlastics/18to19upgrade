# `vm_setup/` — bootstrapping `mes-testing-pg`

Artifacts the new Postgres-backed MES VM needs at creation time. All scoped to **test environment** (`mes-pg-staging` Cloud SQL + `mes-testing-pg` VM). Prod cutover gets its own variants later.

## Files

| File | Purpose | Destination on VM |
|---|---|---|
| `cloud-sql-proxy.service` | systemd unit for Cloud SQL Auth Proxy (private IP, port 5432 on localhost) | `/etc/systemd/system/cloud-sql-proxy.service` |
| `backup_postgres.sh` | pg_dump → GCS every 15 min via cron, mirroring SQLite backup cadence | `/opt/cloud-sql-proxy/backup_postgres.sh` |
| `install.sh` | One-time bootstrap script (apt, proxy, repo, venv, env file, backups) | run as root: `sudo bash /tmp/install.sh` |
| `README.md` | This file | reference |

## Order of operations on the VM

Per the runbook Phase 1 (1H onward):

```bash
# After `gcloud compute instances create mes-testing-pg ...` completes:

# 1. Upload all three artifacts to the VM
gcloud compute scp \
    18to19upgrade/workflow/pg_migration/vm_setup/cloud-sql-proxy.service \
    18to19upgrade/workflow/pg_migration/vm_setup/backup_postgres.sh \
    18to19upgrade/workflow/pg_migration/vm_setup/install.sh \
    anthony@mes-testing-pg:/tmp/ --zone=us-central1-a

# 2. Run the bootstrap (idempotent; safe to re-run)
gcloud compute ssh anthony@mes-testing-pg --zone=us-central1-a \
    --command="sudo bash /tmp/install.sh"
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
