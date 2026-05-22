# 18to19upgrade — Handoff & State

> **Living document.** Umbrella tracker for the Odoo 18 → 19 cutover effort. Other repos have their own [HANDOFF.md](../MESv1.0/HANDOFF.md) files — this one captures cross-repo state + the audit pipeline + upgrade-specific runbooks.

**Last updated:** 2026-05-22 — Claude (Anthony's session) — pallet-qty UoM fix landed on staging (MESv1.0 `125869b` + `msp_pallet 19.0.1.0.4` in odoo18 `b8b454c`). Verified live on 01483 PAL-5: quant went from 35 Thousands (= 35,000 bags) to 1.05 Thousands (= 1,050 bags). New `msp_unit_count` Integer on stock.package now exposes the operator-facing roll count alongside the sales-UoM quant.

---

## Snapshot — where everything stands

### Odoo
- **Production**: Live on v19 (cut over ~2026-04). `msppartialMO` is NOT installed on prod yet.
- **Staging** (`19_upgradetest2` branch on Odoo.sh): `https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/`. Has vendored `msppartialMO` v19.0.1.2.0. This is what the cloud test MES talks to.

### MES
- **Production** (https://mes.mountainstatesplastics.com or similar — confirm before touching): `master` branch @ `81c7779`. **Untouched by all in-flight branch work.**
- **Cloud test** (https://34.67.173.228.nip.io, `mes-testing` GCP VM): `lanes-per-master-fix` @ `125869b` (latest: pallet-qty UoM fix 2026-05-22). See [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md).

### operatorUI
- **Each operator station** runs its own local Flask via .bat installer. Currently on whatever the most-recent installer build picked up from `main` @ `e6612e4`.
- **Local dev** (Anthony's box): `lanes-per-master-fix` @ `b1d8da5`, points at cloud test MES. See [../operatorUI/HANDOFF.md](../operatorUI/HANDOFF.md).

### msppartialMO
- `19_upgrade` branch @ `d0583c8` — v19.0.1.3.0 (BOM auto-fill cleanup in `action_increment_qty_producing` on top of `button_mark_done` override). Source of truth for the addon.
- Vendored into `odoo18` repo's `19_upgradetest2` branch for Odoo.sh staging install.
- **NOT on production yet.** Production cut over to v19 without this addon. Will need install + module upgrade as part of staging→prod rollout.

### msp_pallet (in odoo18)
- Lives in `odoo18/msp_pallet/`, currently at `19.0.1.0.4` (b8b454c, 2026-05-22 — added `msp_unit_count` Integer field on `stock.package`).
- **NOT on production yet.** Whole module ships with the v19-staging cutover.

---

## Cross-repo HEAD reference

| Repo | Branch | HEAD | What's on it |
|---|---|---|---|
| `MESv1.0` | `lanes-per-master-fix` | `125869b` | Lane split, auth precedence, dashboard perf, nav partial, UoM=Thousands consumption + **pallet-qty UoM (2026-05-22)** |
| `MESv1.0` | `master` | `81c7779` | Heather's cleanup + v19 staging Odoo repoint + 2026-05-19 recursion fix |
| `operatorUI` | `lanes-per-master-fix` | `a51eec6` | Stitch tracker uses `lanes_per_master_roll`. Heather's `8d5da85` (Expected Wt UI) is on `main` — pick up via rebase when convenient. |
| `operatorUI` | `main` | `8d5da85` | Heather 2026-05-21: Expected master roll weight on stitch tracker for two-step orders |
| `msppartialMO` | `19_upgrade` | `d0583c8` | v19.0.1.3.0 — button_mark_done + BOM auto-fill cleanup |
| `odoo18` | `19_upgradetest2` | `b8b454c` | Vendored msppartialMO 19.0.1.3.0 + msp_pallet 19.0.1.0.4 (msp_unit_count) |
| `18to19upgrade` | `main` | `fa39c35` | Audit pipeline + per-run reports + umbrella HANDOFF |

Cmd to refresh all five at once:
```bash
cd "c:\Users\Anthony\Desktop\mes and operator ui"
for r in MESv1.0 operatorUI msppartialMO odoo18 18to19upgrade; do
  echo "=== $r ===" && (cd $r && git fetch --all --quiet && \
  echo "  branch: $(git rev-parse --abbrev-ref HEAD), HEAD: $(git rev-parse --short HEAD)" && \
  echo "  ahead/behind origin: $(git rev-list --left-right --count HEAD...@{u} 2>/dev/null | awk '{print "ahead="$1", behind="$2}')")
done
```

---

## Audit pipeline (workflow/audit/)

State-driven Odoo SO → MO → roll → pallet → invoice test pipeline. Product-agnostic since 2026-05-10.

**Most recent audit reports** (in this repo):
- `AUDIT_2026-05-09_11158.md` — Roll-sold product, baseline reference.
- `AUDIT_2026-05-10_11158_fixverify.md` — Verified silo lot + pallet rewire fixes.
- `AUDIT_2026-05-10_10083.md` — Lb-sold product, multi-step. Surfaced FG double-write + pallet UoM bugs.

**To start a fresh audit** see `PLAYBOOK.md` in this repo. The pipeline currently handles weight-tracked and unit-tracked products correctly. Hardcoded check-side cleanup is the only open TODO (see below).

---

## Pending — cross-repo

### Validation needed
- [ ] **Lanes fix end-to-end on operator station.** Cloud test MES + local operatorUI both have the fix. Need to record real master rolls on the 4-master SWS test order and confirm progress now matches reality (1× not 4×).

### Audit pipeline cleanup (cosmetic — checks fail on lb-stocked products even though functionality is correct)
- [ ] `03_observe_production.py --finalize`: hardcoded `MO/` lot prefix check. Generalize to use whatever pattern state has.
- [ ] `04_verify_pallets.py`: `pallet contains {PER_PALLET} units` check — for lb-stocked, multiply by `FG_PER_ROLL`.
- [ ] `05_verify_pick_sheet.py`: similar issue likely.
- [ ] `08_trace_lot.py`: hardcodes 11158's resin set + seed lot. Read from `state['blend_recipe']`.

### Production rollout (pending staging-validation finish)
The 2026-05-10 → 2026-05-22 fixes are all staging-verified or in-flight:
- Silo lot validation, pallet rewire, FG zero-out + cancel handling, UoM-aware pack qty (2026-05-10)
- `msppartialMO` v19.0.1.2.0 `button_mark_done` override (2026-05-10)
- `lanes_per_master_roll` + `masters_per_doff` schema split, slitter cap, Odoo auth precedence (2026-05-14)
- Dashboard perf (cached_property + batch pre-fetch + pre-warm), nav partial, DR docs, VM-IP recovery (2026-05-19)
- UoM=Thousands consumption fix + msppartialMO `19.0.1.3.0` BOM auto-fill cleanup (2026-05-21)
- **Pallet-qty UoM fix + msp_pallet `19.0.1.0.4` (msp_unit_count field) (2026-05-22)**

When ready: follow [STAGING_TO_PROD_RUNBOOK.md](STAGING_TO_PROD_RUNBOOK.md) Phase 0 dry-run first.

### Future work mentioned in conversation
- Operator-set lane override on extrusion setup screen.
- Ft-based progress in LBS + Unit trackers (today they're lbs-based; the data is there but not displayed).
- 3rd-product audit (Thousands-sold inline single-step) — was on the original audit plan.

---

## Useful endpoints

| Service | URL |
|---|---|
| Cloud test MES | https://34.67.173.228.nip.io |
| Cloud test MES health | https://34.67.173.228.nip.io/api/health |
| Staging Odoo | https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/ |
| Production Odoo | (live v19 instance — confirm URL before any contact) |
| Local operatorUI dev | http://127.0.0.1:5010 (when `python app.py` is running) |

---

## Maintaining this document

- Update the snapshot section after every cross-repo deploy or branch change.
- Add new gotchas / runbook updates to the per-repo HANDOFF.md files, then summarize here if it affects cross-repo state.
- Keep the HEAD reference table accurate — it's the fastest way to answer "where are we?" from a new computer.
- Remove resolved pending items.
- Aim for under 200 lines; link to longer runbooks for the heavy detail.
