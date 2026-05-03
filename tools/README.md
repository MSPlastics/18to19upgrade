# Tools

XML-RPC diagnostic + remediation scripts used during the v18→v19 upgrade.

## Setup

1. `cp ../.env.example ../.env`
2. Fill in API keys in `.env` (see `.env.example` for required vars)
3. Run scripts from this `tools/` directory: `python diag_modules.py --target staging`

## Scripts

| Script | What it does |
|---|---|
| `diag_modules.py` | Lists state of all 15 custom modules + sanity reads on their key models. Use to confirm what's installed/working/stuck. |
| `check_module_state.py <name>` | Quick single-module state check. |
| `force_module_upgrade.py <name>` | Triggers Odoo's `button_immediate_upgrade` on a module. Destructive — use to recover stuck modules. |
| `uninstall_module.py <name>` | Triggers `button_immediate_uninstall`. Use to remove a module no longer in the repo. |
| `read_logs.py [--module X]` | Reads recent `ir.logging` ERROR/WARNING records. |

All scripts default to `--target staging`. Pass `--target prod` for prod (and respect the prod-safety memory: dry-run first; only `--commit` when you're sure).

## Adding new scripts

`_common.py` provides `connect(target)` returning `(uid, models, db, api_key)` and `make_caller(...)` for the standard `call(model, method, args, kwargs)` pattern. Reuse those.
