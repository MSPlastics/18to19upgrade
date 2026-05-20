# 18to19upgrade — Handoff & State

> **Living document.** Umbrella tracker for the Odoo 18 → 19 cutover effort. Other repos have their own [HANDOFF.md](../MESv1.0/HANDOFF.md) files — this one captures cross-repo state + the audit pipeline + upgrade-specific runbooks.

**Last updated:** 2026-05-19 — Claude (Anthony's session) — rebased lanes branches onto current `master`/`main`; test VM reset to new HEAD.

---

## Snapshot — where everything stands

### Odoo
- **Production**: Live on v19 (cut over ~2026-04). `msppartialMO` is NOT installed on prod yet.
- **Staging** (`19_upgradetest2` branch on Odoo.sh): `https://msplastics-odoo18-19-upgradetest2-32113137.dev.odoo.com/`. Has vendored `msppartialMO` v19.0.1.2.0. This is what the cloud test MES talks to.

### MES
- **Production** (https://mes.mountainstatesplastics.com or similar — confirm before touching): `master` branch @ `81c7779`. **Untouched by all in-flight branch work.**
- **Cloud test** (https://34.67.173.228.nip.io, `mes-testing` GCP VM): `lanes-per-master-fix` @ `c4543b7` (rebased onto current `master` 2026-05-19). See [../MESv1.0/HANDOFF.md](../MESv1.0/HANDOFF.md).

### operatorUI
- **Each operator station** runs its own local Flask via .bat installer. Currently on whatever the most-recent installer build picked up from `main` @ `e6612e4`.
- **Local dev** (Anthony's box): `lanes-per-master-fix` @ `b1d8da5`, points at cloud test MES. See [../operatorUI/HANDOFF.md](../operatorUI/HANDOFF.md).

### msppartialMO
- `19_upgrade` branch @ `b101030` — v19.0.1.2.0. Source of truth for the addon.
- Vendored into `odoo18` repo's `19_upgradetest2` branch for Odoo.sh staging install.
- **NOT on production yet.** Production cut over to v19 without this addon. Will need install + module upgrade as part of staging→prod rollout.

---

## Cross-repo HEAD reference

| Repo | Branch | HEAD | What's on it |
|---|---|---|---|
| `MESv1.0` | `lanes-per-master-fix` | `c4543b7` | Lane split, Odoo auth precedence fix — rebased onto current `master` 2026-05-19 |
| `MESv1.0` | `master` | `81c7779` | Heather's cleanup + v19 staging Odoo repoint + 2026-05-19 recursion fix |
| `operatorUI` | `lanes-per-master-fix` | `b1d8da5` | Stitch tracker uses `lanes_per_master_roll` — rebased onto current `main` 2026-05-19 |
| `operatorUI` | `main` | `e6612e4` | Heather's ft-conversion + 2026-05-19 progress/station/timeout fixes |
| `msppartialMO` | `19_upgrade` | `b101030` | v19.0.1.2.0 — button_mark_done override |
| `odoo18` | `19_upgradetest2` | `a1a4759` | Vendored msppartialMO for staging |
| `18to19upgrade` | `main` | `a69f158` | Audit pipeline + per-run reports + umbrella HANDOFF |

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
The 2026-05-10 + 2026-05-14 fixes are all staging-verified or in-flight:
- Silo lot validation, pallet rewire, FG zero-out + cancel handling, UoM-aware pack qty (2026-05-10)
- `msppartialMO` v19.0.1.2.0 `button_mark_done` override (2026-05-10)
- `lanes_per_master_roll` + `masters_per_doff` schema split, slitter cap, Odoo auth precedence (2026-05-14)

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
