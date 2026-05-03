# Workflow scripts

Prod-prep scripts. These connect to the **production** Odoo instance and write to it. Treat with care.

## Setup

1. `cp ../.env.example ../.env`
2. Fill in `ODOO_PROD_*` env vars
3. Always dry-run first

## Scripts

### `prod_zero_negatives.py`

Zeros negative `stock.quant` rows on internal locations using inventory adjustments (auditable, reversible). Filters out kit (phantom-BOM) products since they don't have real stock.

```
python prod_zero_negatives.py            # dry-run (default)
python prod_zero_negatives.py --commit   # actually apply
```

### `prod_disable_kits.py`

Flips `mrp.bom.type` from `phantom` → `normal` so kit products fall back to their own quants (zero) during migration. Saves the BOM IDs to `prod_disabled_kits.json` so the restore step can find them.

```
python prod_disable_kits.py             # dry-run (default)
python prod_disable_kits.py --commit    # apply (saves marker file)
python prod_disable_kits.py --restore   # post-migration: flip back to phantom
```

⚠ **Operational impact**: while BOMs are flipped to `normal`, new MOs created against a kit product won't auto-explode into components. Run close to the migration window.

### `prod_disabled_kits.json`

Marker file recording which 164 BOMs were disabled in the most recent prep run (timestamp 2026-05-02). Required for `--restore` to know which BOMs to flip back. Not committed by default — see `.gitignore`. The version in this repo is a snapshot for reference.
