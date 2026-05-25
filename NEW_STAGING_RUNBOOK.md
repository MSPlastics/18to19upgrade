# NEW_STAGING_RUNBOOK — adding another Postgres-backed MES environment

This walks through standing up a fresh `mes-pg-<env>` stack from zero — Cloud SQL instance, GCE VM, MES web app, optional replicator from a SQLite source. Use it when you want another staging URL to test something on without disrupting the existing test environment.

This is **not** the migration runbook ([POSTGRES_MIGRATION_RUNBOOK.md](POSTGRES_MIGRATION_RUNBOOK.md)) — that was a one-time event with phases for cutover. This one is the repeatable "spin up another env" recipe.

**Audience:** future-you, or any engineer with `Owner` on the MSP project.

**Time:** ~45 min if you don't trip over anything; ~90 min first time.

---

## 0. Pick names + values

Decide before you start:

| Variable | Example | Notes |
|---|---|---|
| `ENV_NAME` | `dev`, `qa`, `demo` | Short tag used in resource names |
| `INSTANCE_NAME` | `mes-pg-${ENV_NAME}` | Cloud SQL instance name |
| `SECRET_NAME` | `${INSTANCE_NAME}-password` | Secret Manager entry for app password |
| `VM_NAME` | `mes-${ENV_NAME}-pg` | GCE VM name |
| `TIER` | `db-custom-2-7680` (default), `db-custom-1-3840` for dev | Cloud SQL machine size |
| `READ_ONLY_MODE` | `1` for validation replica; unset for full app | See app.py `_ensure_workers` |

Set these as shell vars; the rest of the doc references them.

```bash
export ENV_NAME=dev
export INSTANCE_NAME=mes-pg-${ENV_NAME}
export SECRET_NAME=${INSTANCE_NAME}-password
export VM_NAME=mes-${ENV_NAME}-pg
export PROJECT=superb-metric-492315-r5
export REGION=us-central1
export ZONE=us-central1-a
```

---

## 1. Provision Cloud SQL

One script. Idempotent — re-run if you tweak args.

```bash
cd 18to19upgrade/workflow/pg_migration/vm_setup
./provision_cloud_sql.sh "$INSTANCE_NAME" "$SECRET_NAME"
```

Creates:
- HA Enterprise Postgres 16 instance (private IP only, no public exposure)
- `mes` database
- `mes_app` user
- Password in Secret Manager
- IAM grants on the Compute SA (`roles/cloudsql.client` + `secretAccessor`)
- 7-day PITR + 7 daily backups, daily window 07:00 UTC

Script prints the **connection name** and **private IP** when done — save those for step 2.

---

## 2. Build the VM

```bash
gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type=e2-medium \
    --network=default \
    --subnet=default \
    --tags=http-server,https-server,mes,mes-pg \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=20GB \
    --boot-disk-type=pd-balanced \
    --service-account="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
    --scopes=cloud-platform
```

Get the new VM's external IP:

```bash
gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
    --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
```

Use that as `${EXTERNAL_IP}` below.

Set the hostname (we use nip.io for free TLS via Let's Encrypt):

```bash
export HOSTNAME="${EXTERNAL_IP}.nip.io"
```

---

## 3. Bootstrap the VM

The `install.sh` script handles apt deps + Cloud SQL Auth Proxy + Python venv + repo clones + backup cron. SCP it onto the new VM and run.

```bash
gcloud compute scp install.sh anthony@${VM_NAME}:/tmp/install.sh --zone="$ZONE" --tunnel-through-iap

gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap \
    --command="sudo bash /tmp/install.sh ${INSTANCE_NAME} ${SECRET_NAME}"
```

`install.sh` does roughly:
- `apt install` for python3, build deps (libcairo2-dev, pkg-config, libpixman-1-dev), nginx, certbot, sqlite3
- Adds a `cloudsqlproxy` system user with a **real** `/home/cloudsqlproxy` (the Go binary hard-codes `$HOME` paths even with `HOME=` set — bit us during initial migration)
- Installs Cloud SQL Auth Proxy v2 binary
- Writes `/etc/systemd/system/cloud-sql-proxy.service` and enables it
- Clones `MESv1.0` and `18to19upgrade` to `/opt/mes` and `/opt/18to19upgrade`
- Builds the Python venv at `/opt/mes/venv` with full requirements
- Writes `/etc/mes-pg.env` with `DATABASE_URL` pulled from Secret Manager
- Installs the pg_dump backup cron (every 15 min → `gs://msp-mes-backups/postgres/`)

Verify the proxy is up:

```bash
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap \
    --command="sudo systemctl is-active cloud-sql-proxy.service && sudo ss -tlnp 2>/dev/null | grep :5432"
```

---

## 4. Initialize the database schema

If this is a fresh empty Postgres, create the tables. If you're cloning from an existing source (e.g., a SQLite database), use the bulk loader instead.

**Empty schema only:**
```bash
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
        /opt/mes/venv/bin/python /opt/18to19upgrade/workflow/pg_migration/vm_setup/bootstrap_pg_schema.py'"
```

**Load from a SQLite snapshot:**
First get a snapshot onto the VM (e.g., pull from GCS or scp from a source VM), then:
```bash
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'set -a; source /etc/mes-pg.env; set +a; \
        /opt/mes/venv/bin/python /opt/18to19upgrade/workflow/pg_migration/sqlite_to_pg_migrate.py \
            --sqlite /tmp/mes_data_snapshot.db --postgres \$DATABASE_URL'"
```

---

## 5. Bring up the MES web stack (nginx + gunicorn + TLS)

### 5a. Copy MES app secrets

The MES app needs two files of secrets:
- `/etc/mes.env` — `FLASK_DEBUG`, `MES_API_KEY`, `PORT`
- `/opt/mes/.env` — `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FLASK_SECRET_KEY`

Easiest is to copy them from an existing VM (`mes-testing` or `mes-testing-pg`) via private GCS transit:

```bash
# On source VM (e.g., mes-testing-pg):
gcloud compute ssh anthony@mes-testing-pg --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'gsutil -q cp /etc/mes.env gs://msp-mes-backups/.transit/mes-env-etc && \
        gsutil -q cp /opt/mes/.env gs://msp-mes-backups/.transit/mes-env-dot'"

# On new VM:
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'gsutil -q cp gs://msp-mes-backups/.transit/mes-env-etc /etc/mes.env && \
        chmod 600 /etc/mes.env && chown root:root /etc/mes.env && \
        gsutil -q cp gs://msp-mes-backups/.transit/mes-env-dot /opt/mes/.env && \
        gsutil -q rm gs://msp-mes-backups/.transit/mes-env-etc gs://msp-mes-backups/.transit/mes-env-dot'"
```

The transit copies live in a private bucket and are deleted immediately after. They never touch your local disk in plaintext.

If this VM should be a **passive read-only replica** (no Odoo sync writes), append `READ_ONLY_MODE=1` to `/etc/mes-pg.env`:

```bash
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'echo \"READ_ONLY_MODE=1\" >> /etc/mes-pg.env'"
```

### 5b. Install nginx site + provision Let's Encrypt cert

```bash
# Render template with the hostname
sed "s/__HOSTNAME__/${HOSTNAME}/g" \
    18to19upgrade/workflow/pg_migration/vm_setup/nginx-mes-site.template \
    > /tmp/mes-pg-site

gcloud compute scp /tmp/mes-pg-site anthony@${VM_NAME}:/tmp/mes-pg-site --zone="$ZONE" --tunnel-through-iap

gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'install -m 644 /tmp/mes-pg-site /etc/nginx/sites-available/mes-pg && \
        ln -sf /etc/nginx/sites-available/mes-pg /etc/nginx/sites-enabled/mes-pg && \
        rm -f /etc/nginx/sites-enabled/default && \
        nginx -t && systemctl reload nginx'"

# Provision cert (HTTP-01 challenge, needs :80 reachable)
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo certbot --nginx -d ${HOSTNAME} --non-interactive --agree-tos \
        --email anthony@mountainstatesplastics.com --redirect"
```

### 5c. Install mes.service systemd unit

```bash
gcloud compute scp 18to19upgrade/workflow/pg_migration/vm_setup/mes.service \
    anthony@${VM_NAME}:/tmp/mes.service --zone="$ZONE" --tunnel-through-iap

gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'install -m 644 /tmp/mes.service /etc/systemd/system/mes.service && \
        rm /tmp/mes.service && systemctl daemon-reload && \
        systemctl enable --now mes.service && sleep 8 && \
        systemctl is-active mes.service'"
```

---

## 6. Update Google OAuth — redirect URI

⚠️ **THIS IS A MANUAL STEP — DO NOT SKIP.**

Without it the Google sign-in flow returns `Error 400: redirect_uri_mismatch`.

1. Open https://console.cloud.google.com/apis/credentials?project=${PROJECT}
2. Sign in as `anthony@mountainstatesplastics.com` (work account)
3. Under "OAuth 2.0 Client IDs", click the MES client (the one that already has the existing `https://*.nip.io/login/callback` URLs)
4. Add to "Authorized redirect URIs":
   ```
   https://${HOSTNAME}/login/callback
   ```
5. Add to "Authorized JavaScript origins":
   ```
   https://${HOSTNAME}
   ```
6. Save. Propagation takes a few seconds.

---

## 7. (Optional) Set up replicator + verifier daemons

Only needed if this new env should be a passive replica fed from another SQLite stack (Phase 4 of the migration runbook). For a standalone env that does its own Odoo sync, skip this.

```bash
# On the SOURCE VM (the SQLite one feeding the replica), install the snapshot cron
gcloud compute scp 18to19upgrade/workflow/pg_migration/vm_setup/snapshot_to_gcs.sh \
    anthony@<SOURCE_VM>:/tmp/snapshot_to_gcs.sh --zone="$ZONE" --tunnel-through-iap
gcloud compute ssh anthony@<SOURCE_VM> --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'install -m 755 /tmp/snapshot_to_gcs.sh /usr/local/bin/ && \
        (crontab -u anthony -l 2>/dev/null | grep -v snapshot_to_gcs; \
         echo \"* * * * * /usr/local/bin/snapshot_to_gcs.sh >> /var/log/mes-snapshot.log 2>&1\") \
            | crontab -u anthony -'"

# On the NEW VM (the replica), install pull cron + replicator + verifier
gcloud compute scp 18to19upgrade/workflow/pg_migration/vm_setup/pull_snapshot_from_gcs.sh \
    18to19upgrade/workflow/pg_migration/vm_setup/mes-pg-replicator.service \
    18to19upgrade/workflow/pg_migration/vm_setup/mes-pg-verifier.service \
    anthony@${VM_NAME}:/tmp/ --zone="$ZONE" --tunnel-through-iap

gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'install -m 755 /tmp/pull_snapshot_from_gcs.sh /usr/local/bin/ && \
        install -m 644 /tmp/mes-pg-replicator.service /etc/systemd/system/ && \
        install -m 644 /tmp/mes-pg-verifier.service /etc/systemd/system/ && \
        (crontab -l 2>/dev/null | grep -v pull_snapshot; \
         echo \"* * * * * /usr/local/bin/pull_snapshot_from_gcs.sh >> /var/log/mes-snapshot-pull.log 2>&1\") | crontab - && \
        systemctl daemon-reload && systemctl enable --now mes-pg-replicator mes-pg-verifier'"
```

---

## 8. Smoke test

```bash
# Plain health
curl -s "https://${HOSTNAME}/api/health"
# Expect: {"status":"ok"}

# DB-reading endpoint (need MES_API_KEY from /etc/mes.env)
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    API_KEY=\$(sudo grep MES_API_KEY /etc/mes.env | cut -d= -f2- | tr -d '\"') && \
    curl -sk -H \"X-API-KEY: \$API_KEY\" \"https://${HOSTNAME}/api/work-centers\" | head -c 200"

# Browser: visit https://${HOSTNAME}, sign in with Google
```

If sign-in fails with `redirect_uri_mismatch`, go back to step 6.

---

## 9. Update the team

- Add the new URL to [HANDOFF.md](HANDOFF.md) (Snapshot section + Useful endpoints table)
- If this is a real shared env (not just a one-off you'll tear down), update [MEMORY.md](../../../.claude/projects/...../memory/MEMORY.md) so future Claude sessions know it exists

---

## Common gotchas (collected from past setups)

| Gotcha | Symptom | Fix |
|---|---|---|
| `--edition=ENTERPRISE` not set | `Invalid Tier ... for (ENTERPRISE_PLUS) Edition` | Add `--edition=ENTERPRISE` to instance create |
| Compute SA missing `cloudsql.client` | Proxy can't connect, "permission denied" in logs | `gcloud projects add-iam-policy-binding ... --role=roles/cloudsql.client` |
| `cloudsqlproxy` user without real home dir | Proxy fails `permission denied` on `/home/cloudsqlproxy/.config` | `useradd --create-home --home-dir /home/cloudsqlproxy` |
| pycairo build fails | `pkg-config` or libcairo2-dev missing | `apt install libcairo2-dev libpixman-1-dev pkg-config build-essential python3-dev` |
| `git clone` prompts for password | sudo'd git has no creds | Source-credential the `/root/.git-credentials` from another VM, or use a deploy key |
| OAuth `redirect_uri_mismatch` | Login fails immediately | Add `https://${HOSTNAME}/login/callback` to OAuth client (step 6) |
| `dateutil` missing in venv | gunicorn won't boot | `requirements.txt` is incomplete; do `pip install` from another VM's `pip freeze` |
| nip.io DNS resolves to 127.0.0.1 from inside VPC | TLS cert provisioning fails | Only an issue if you're on a custom DNS — Google default DNS resolves correctly |
| Windows `gcloud ssh --command` quote-melt | Bizarre bash syntax errors | Write the command as a file, `scp`, then `bash /tmp/script.sh` |
| `/etc/mes.env` chmod 600 root | Can't `grep` without sudo | Use `sudo bash -c 'set -a; source /etc/mes.env; ...'` |

---

## Teardown (when you no longer need the env)

```bash
# Stop services, remove the cert
gcloud compute ssh anthony@${VM_NAME} --zone="$ZONE" --tunnel-through-iap --command="\
    sudo bash -c 'systemctl stop mes mes-pg-replicator mes-pg-verifier cloud-sql-proxy nginx; \
        certbot delete --cert-name ${HOSTNAME} --non-interactive'"

# Delete VM
gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --quiet

# Delete Cloud SQL instance (NOTE: this also deletes all backups — irreversible)
gcloud sql instances delete "$INSTANCE_NAME" --quiet

# Delete secret
gcloud secrets delete "$SECRET_NAME" --quiet
```

Or: keep the instance, just stop the VM — costs drop to storage-only on Cloud SQL (~$10/mo for HA, $5 for non-HA).
