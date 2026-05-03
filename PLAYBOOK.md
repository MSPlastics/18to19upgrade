# Odoo v18 → v19 Upgrade Playbook (MSP)

> **Note (2026-05-03)**: scripts in this repo read credentials from env vars instead of having them hardcoded. Copy `.env.example` → `.env` and fill in `ODOO_PROD_*` values before running any `prod_*.py` script. See [workflow/README.md](workflow/README.md).
>
> **For v19-specific module compat fixes** (those discovered during the upgrade), see [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md).

**This is the "do this when you need to repeat the upgrade" runbook.** The session that produced it (2026-05-01 → 2026-05-02) discovered two pre-existing data conditions in MSP's prod database that make Odoo's migration tool fail. Fix those, and the migration passes.

If you're upgrading to a *newer* major version someday (v19 → v20, etc.), the same overall structure applies but the specific test that fails may be different. Read the upgrade error first; come back here for the *shape* of the fix.

---

## TL;DR — what to actually do

```
PROD PREP (Saturday morning, low-impact):
  python prod_zero_negatives.py            # dry-run, eyeball
  python prod_zero_negatives.py --commit
  python prod_disable_kits.py              # dry-run, eyeball
  python prod_disable_kits.py --commit

WAIT for tonight's auto-backup (or trigger one if your plan allows).

DEV BRANCH TEST (Sunday morning):
  Dashboard → New dev branch from prod
  Dashboard → Upgrade → 19.0 → Test
  Wait for email (~30 min – 4 hours)
  If pass: browser-test the dev branch URL

PROD CUTOVER (Sunday afternoon):
  Dashboard → Upgrade → 19.0 → Production
  Wait. Verify when complete.

POST-MIGRATION:
  python prod_disable_kits.py --restore    # flips 164 BOMs back to phantom

ROLLBACK IF NEEDED:
  Dashboard → restore from pre-upgrade backup (Odoo keeps 7 days)
```

---

## Root cause: why the upgrade was failing

Odoo's migration runs an invariant test called `TestOnHandQuantityUnchanged` that compares each product's `qty_available` BEFORE vs AFTER the migration. Any product whose computed value changes during migration → test fails → migration aborts.

For MSP's data, two distinct issues caused this:

### Issue 1 — Negative quants on internal locations (47 of them)

**What it is:** Real products with on-hand `< 0` in the warehouse stock locations. Things like `M/C LDPE Film 10619` at -42,490 lbs, packaging supplies at -200 each, etc.

**Why it fails the migration:** Some products (the BLEND-* kits) have phantom BOMs. Kit `qty_available` is computed as a *rollup* of component availability. When a component has a negative quant, the rollup goes deeply negative. The v18 and v19 rollup formulas differ slightly in how they handle negative inputs → different result → test fails.

**Fix:** zero each negative quant via an inventory adjustment (`stock.quant.action_apply_inventory` after setting `inventory_quantity = 0`). Auditable, reversible.

**Script:** [prod_zero_negatives.py](prod_zero_negatives.py)

### Issue 2 — Phantom BOMs (164 of them, all on consumable products)

**What it is:** Kit recipes — when you say "make a BLEND-2001," the BOM auto-explodes into the resin components. They're configured as `mrp.bom.type = 'phantom'` on `consu` (consumable) product templates.

**Why it fails the migration:** Even with non-negative components, v18 and v19 compute kit `qty_available` differently — the formula was tweaked between versions. With kits in the mix, the migration test never matches exactly.

**Fix:** temporarily flip the BOM type from `phantom` to `normal`. With phantom disabled, kit products fall back to their *own* quants (which are 0 since they're consumables), so v18 and v19 both compute 0 for these products.

After the migration completes, flip them back to `phantom` so kit behavior in v19 matches what users expect.

**Script:** [prod_disable_kits.py](prod_disable_kits.py) (commit + restore modes)

**Operational impact during the disabled window:** any new MO created against a kit product won't auto-explode into components. If MSP's team works while kits are disabled, they could create unexpected MO behavior. **MSP doesn't work weekends, so we apply the fix Saturday morning, leave it overnight, do the migration Sunday, restore before Monday.**

---

## Pre-flight checklist

Before kicking off any of this:

- [ ] **Maintenance window booked.** Saturday morning through Sunday evening for MSP.
- [ ] **Backup exists** that pre-dates any of our changes. Odoo Online auto-backups suffice; manually trigger one if you want extra safety.
- [ ] **Communications sent.** Anyone who might log in over the weekend should know prod is in flux.
- [ ] **Rollback decision-maker identified.** If something breaks, who calls "rollback" vs "fix forward"?
- [ ] **Production credentials handy.** URL, DB name, admin login, API key. Edit the SERVER CONFIGURATION block in `prod_zero_negatives.py` and `prod_disable_kits.py` if any of these change between runs.
- [ ] **No active MOs on kit products in flight** that would be disrupted by disabling phantom BOMs. (Quick check: if MOs are processed during the day, do them BEFORE running disable_kits.)

---

## The full playbook (step-by-step, with gates)

### Step 1 — Apply prod prep fixes

Order matters. Zero negatives first (low impact, gives the inventory state a clean baseline). Then disable kits (higher impact, time-bounded).

```bash
cd "c:/Users/Anthony/Desktop/odoo bot/upgrade_workflow"

# Zero negative quants (47 in our last run, ~30-90s)
python prod_zero_negatives.py            # eyeball preview
python prod_zero_negatives.py --commit   # apply
# Confirm output says "0 negative quants remaining"

# Disable phantom BOMs (164 in our last run, ~5s)
python prod_disable_kits.py              # eyeball preview
python prod_disable_kits.py --commit     # apply
# Marker file saved to upgrade_workflow/prod_disabled_kits.json
```

**Gate before continuing:** confirm both scripts succeeded. Look at the `logs/` directory and the `prod_disabled_kits.json` marker file.

### Step 2 — Get a backup that includes the fixes

Odoo Online dev/staging branches are always created from the **latest automatic daily backup**. You can't make them use a manual backup directly.

So either:

**(a) Wait for tonight's nightly auto-backup.** Tomorrow morning, fresh dev branch will include the fixes. Cleanest path.

**(b) If your plan supports it: dashboard → upload manual backup → restore as new database.** Less common, plan-dependent.

### Step 3 — Test upgrade on a fresh dev branch

```
https://www.odoo.com/my/databases
→ Create new dev branch from production (don't reuse old branches — they may have stale state)
→ Wait for the new branch to provision
→ Gear icon on the branch → Upgrade → Target: 19.0 → Click "Test" (NOT Production)
```

Wait for the email. Look for `TestOnHandQuantityUnchanged: PASSED` somewhere in the report. Estimated time: 30 min to a few hours.

**If it fails on a different test:** capture the error and come back. Common follow-on issues:
- Custom-module v19-incompatibility — module's `__manifest__.py` says `'version': '18.0.x'` and Odoo refuses to upgrade. Need a v19 port.
- Studio view xpath breakage — Odoo 19 changed view structures, custom Studio xpaths can break. Less common.

**If it passes:** continue.

### Step 4 — Browser-test the v19 dev branch

The dev branch URL works in a browser. Log in with your normal admin credentials.

Walk through the critical paths:
- Login → home dashboard renders
- Sales → recent SO → opens cleanly, customer info present
- Manufacturing → recent MO → operations tab shows FPM field
- Manufacturing → BOMs → open a BLEND-* (kit) product's BOM. **Note: at this point on prod the BOM is still type='normal' from our fix.** That's fine for the test — the kit will auto-explode after we flip back.
- Inventory → recent delivery → lot number visible on line
- Try printing a delivery PDF
- Reports → Stock at hand → loads
- Click into a Studio-customized model — does the form render? Are the `x_studio_*` fields visible?

If anything throws or renders broken, capture the URL and traceback.

### Step 5 — Trigger Production upgrade

Only after Step 4 passes for the major flows:

```
Dashboard → Production database → Upgrade → 19.0 → "Production"
```

This is the irreversible step. Odoo will run the migration against prod's data. The DB is unavailable during this — could be 30 min to several hours.

When complete, you'll get an email confirming.

### Step 6 — Post-migration: restore phantom BOMs

```bash
cd "c:/Users/Anthony/Desktop/odoo bot/upgrade_workflow"

# IMPORTANT: edit SERVER CONFIGURATION in prod_disable_kits.py if anything
# about prod's URL/DB/key changed during the upgrade (it usually doesn't).

python prod_disable_kits.py --restore
# Reads prod_disabled_kits.json, flips the recorded BOMs back to type='phantom'
```

**Gate:** confirm the script restored the expected number of BOMs (should be 164, or close — some may have been deleted since the disable step).

### Step 7 — Smoke test prod-v19

Same checklist as Step 4 but against the live prod URL. Real users start using it Monday morning, so any breakage you find now saves them headache.

### If something is critically broken

Odoo Online keeps a v18 backup for 7 days post-upgrade. Dashboard → restore from backup. **You will lose any data created on v19** between cutover and restore.

---

## Reference — when to repeat this playbook

This playbook is specific to MSP's v18→v19 upgrade. **If you ever upgrade again** (v19→v20 in the future, or a new dev branch from prod gets the same problems), expect:

1. The two prep fixes here may **still apply** — negative quants and kit BOMs are operational artifacts of MSP's manufacturing process; both can re-accumulate over time.
2. **New issues may surface** — newer Odoo versions add new invariant tests. Read the failing test name from the upgrade log first; the *shape* of the fix is "find the data condition the test cares about, normalize it on prod via XML-RPC, retry."
3. The Windows compat patches in `run_upgrade.py` are independent of Odoo version and should keep working.

---

## Windows compat — already automated

`run_upgrade.py` auto-patches the freshly-downloaded upgrade.odoo.com CLI on every run. **You shouldn't need to touch this.** For reference, the four patches:

1. **psql arg order:** Linux psql permits `psql DBNAME --flag`; Windows EDB psql parses `--flag` as the username. Patch injects `-d` before the dbname.
2. **rsync upload trailing slash:** `os.sep` is `\\` on Windows, MSYS2 rsync needs `/` to mean "transfer contents not the directory itself."
3. **pg_restore arg order:** same parsing issue as psql. Patch moves the positional dump-name to the end.
4. **rsync download path:** `os.path.join(server, name)` on Windows produces `:/data\\file` and `--ignore-missing-args` silently drops it. Patch builds POSIX paths explicitly.

If `run_upgrade.py` ever stops applying these — Odoo updated the upstream CLI in a way that breaks the regex matchers — you'll see one of these symptoms:
- `psql: warning: extra command-line argument` → patch 1 broke
- `pg_restore: failed to read TOC` and the upload DID succeed → patch 2 broke
- `pg_restore: too many command-line arguments` → patch 3 broke
- Download succeeds (rsync says 100%) but no file appears locally → patch 4 broke

The fix in each case: read the new upstream `upgrade.py`, update the regex / replace string in `patch_cli_for_windows()` to match the new code shape.

---

## Local iteration option

If you ever need to debug something *without* touching prod (different error, want to try a fix, etc.), the local pipeline is fully working. Steps:

```bash
# Get a fresh prod backup (manual backup from dashboard, save to C:\msp_backups\)

cd "c:/Users/Anthony/Desktop/odoo bot/upgrade_workflow"

# 1. Restore the backup locally
python restore_local.py C:\msp_backups\<backup>.zip --target msp_v18_input --force

# 2. Inject the enterprise contract code (Odoo Online doesn't put it in dump)
PGPASSWORD=odoo_dev psql -h 127.0.0.1 -U odoo_dev -d msp_v18_input -c \
  "INSERT INTO ir_config_parameter (key, value, create_date, write_date, create_uid, write_uid) \
   VALUES ('database.enterprise_code', 'M250113200809819', now(), now(), 1, 1) \
   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;"

# 3. Apply the prep fixes locally
python zero_negatives.py --db msp_v18_input --commit
python disable_kits.py --db msp_v18_input --commit

# 4. Run the upgrade (~6 min on Odoo's servers)
python run_upgrade.py msp_v18_input

# 5. Migrated DB ends up at msp_v19_output (locally)
python disable_kits.py --db msp_v19_output --restore
```

**Limitation:** local Odoo 19 source doesn't have Enterprise modules, so booting `msp_v19_output` in a browser is unreliable. The local pipeline is good for verifying the *migration mechanism*, not for browser-testing the resulting v19. For UI verification, use the cloud dev branch.

---

## Files inventory

Scripts in [upgrade_workflow/](.):

| File | Purpose | Targets |
|---|---|---|
| `config.py` | Paths, helpers, libpq env vars | (utility) |
| `restore_local.py` | Backup zip → local Postgres + filestore (with pgvector patch) | local |
| `zero_negatives.py` | Zero negative quants (direct SQL) | local |
| `disable_kits.py` | Phantom → normal BOM flip (direct SQL) | local |
| `run_upgrade.py` | Wrap upgrade.odoo.com CLI + auto-apply Windows patches | local |
| `boot_check.py` | Boot Odoo with `--stop-after-init`, surface errors | local |
| `prod_zero_negatives.py` | XML-RPC version against prod | **PROD** |
| `prod_disable_kits.py` | XML-RPC version against prod, with --restore | **PROD** |
| `RESULTS.md` | What happened in the 2026-05-01 session | (history) |
| `PLAYBOOK.md` | This file | (reference) |
| `prod_disabled_kits.json` | Marker file — IDs of BOMs disabled, used by --restore | (state) |
| `logs/` | Per-run timestamped logs | (history) |

Credentials live inside the prod scripts' SERVER CONFIGURATION block. Update them if the API key rotates.
