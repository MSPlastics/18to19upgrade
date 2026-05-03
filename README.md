# MSPlastics Odoo 18 → 19 Upgrade

Documentation + tooling for migrating MSPlastics' Odoo Online instance from v18 to v19. **For the full story, read [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md).**

## What's in here

| File / folder | Purpose |
|---|---|
| [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) | **Read this first.** Complete fix journal: every v19 break we found, the per-module fixes, the migration-level damage, and the recovery process. |
| [PLAYBOOK.md](PLAYBOOK.md) | Repeatable upgrade runbook (prod prep + cutover steps). |
| [tools/](tools/) | XML-RPC diagnostic scripts: check module state, force upgrade, uninstall, read logs. |
| [workflow/](workflow/) | Cutover scripts: prod prep, post-migration recovery, Studio view archs. |
| [workflow/post_migration_recovery.py](workflow/post_migration_recovery.py) | **The single command** to restore Studio + packaging after migration. Run on staging after every rebuild + ONCE on prod after cutover. |
| [workflow/studio_arch/](workflow/studio_arch/) | Saved prod Studio view XML — the recovery script applies these. |
| [.env.example](.env.example) | Credential template. Copy to `.env` (gitignored), fill in `ODOO_PROD_*` and `ODOO_STAGING_*`. |

## Where the actual code lives

| Repo | Branch | Purpose |
|---|---|---|
| **MSPlastics/odoo18** | `msp_production` | Production target branch. Currently at `124a8e1` — needs fast-forward to `19_upgradetest2` at cutover. |
| **MSPlastics/odoo18** | `19_upgradetest2` | All v19 module fixes + new `msp_packaging` module. Currently at `b8ea7d0`. |
| **MSPlastics/18to19upgrade** (this repo) | `main` | Recovery tooling + docs. |

This repo does **not** contain Odoo module code — only the operational scripts and documentation.

## Quick reference

- **Production URL**: `https://msplastics-odoo18.odoo.com`
- **Latest 19_upgradetest2 commit**: `b8ea7d0` (`feat(msp_packaging): recreate v18 product.packaging for v19`)

## Cutover one-liner (after Odoo.sh upgrade button)

```bash
# Set env vars first (cp .env.example .env, fill in)
cd workflow

# BEFORE clicking the upgrade button — snapshot v18 prod data:
python snapshot_v18_data.py

# AFTER migration finishes — run recovery (snapshot auto-detected):
python post_migration_recovery.py --target prod --commit \
       --copy-data --copy-packagings
python prod_disable_kits.py --restore
```

## Status

- **Staging**: ✅ GREEN. All 15 custom modules load. UI renders. Studio views recovered. Packaging behavior restored via `msp_packaging` module.
- **Prod**: ⏸ Pending cutover. First attempt rolled back; all known issues are now fixed in code.
- **Known deferred items**: ZPL printing (`label_zebra_printer`) needs v19 session API migration — UI loads but actual print broken; cosmetic warnings on `ksc_partner` and `product_customerinfo`.
