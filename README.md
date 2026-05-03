# MSPlastics Odoo 18 → 19 Upgrade

This repo is the documentation + tooling backup from the v18→v19 migration of MSPlastics' Odoo Online instance (May 2026).

## What's in here

| File / folder | Purpose |
|---|---|
| [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) | Per-module fix log: what broke on v19, what we changed, why, commit hash. **Read this first.** |
| [PLAYBOOK.md](PLAYBOOK.md) | Repeatable runbook for the upgrade procedure (prod prep + cutover). |
| [tools/](tools/) | XML-RPC diagnostic scripts: check module state, force upgrade, uninstall, read logs. |
| [workflow/](workflow/) | Prod prep scripts: zero negative quants, disable phantom BOMs, restore. |
| [.env.example](.env.example) | Template for credentials. Copy to `.env` (gitignored) and fill in. |

## Where the actual code fixes live

The v19-compatible code itself is on **`MSPlastics/odoo18`** in branch **`19_upgradetest2`** (and pushed to `msp_production` at cutover). This repo only contains documentation and operational tooling.

Last commit on `19_upgradetest2` at time of staging-green: `213191c`.

## Quick links

- Production URL: `https://msplastics-odoo18.odoo.com`
- Source repo: `https://github.com/MSPlastics/odoo18` (branches: `msp_production`, `19_upgradetest2`)
- Odoo.sh dashboard: dashboard for the project

## Status (as of last commit)

- **Staging upgrade**: GREEN. All 15 custom modules load. JS bundle compiles. UI renders.
- **Prod upgrade**: pending re-trigger. First attempt rolled back due to module loader issues that have since been fixed.
- **Known caveat**: ZPL printing on staging needs a v19 session API migration — not fixed yet, deferred.
