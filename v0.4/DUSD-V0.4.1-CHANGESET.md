# DUSD V0.4 → V0.4.1 — Changeset

Authoritative deltas between `dusd_v04_sim.py` (shipped V0.4) and `dusd_v041_sim.py`
(fix sandbox in this deliverable). Each fix is traced to a root cause from the V0.4
adversarial suite.

## F1 — Persistent positions (kills debt erasure) · S16 / A1
- **Before:** `mint()` replaced `self.positions[owner]` wholesale → a 1-DERO re-mint
  after expiry erased debt (7,182) and collateral (998,200) from the ledger → free DUSD.
- **After:** re-mint *accumulates*: `old.collateral += collateral − pol_dero`,
  `old.debt += user_dusd`, `old.committed_days = max(max(0, old_expiry − t), lock)`.
  Debt is monotone; only `repay()` reduces it.
- **Verify:** TEST-V041-REGRESSION (S1) + TEST-S16-S19 (debt erasure section).

## F2 — POL-origin quarantine + issuance cap · S3 / A2
- **Before:** swap-received POL DERO was immediately re-mintable (fresh address each
  lap) → pool.dero 101,800 → 48,025 (−53%), debt +52%, instant.
- **After:** `last_pool_outflow_at` tagged on swap receipt; `mint()` refuses while
  `t < awarded + QUARANTINE_DAYS (30)`; `LOOP_CAP_RATIO=0.0` declared as a cap on
  extra debt from POL-derived layers (**note: not yet wired into `mint()`**).
- **Honest bound:** rate-limited to 1 lap/30d; rotation still converges to ~48,021 in
  ~4.1 yr. This is a rate-limit, not a cure (Part 12.2).

## F3 — Fee split at source · S7 / A5
- **Before:** reserve kept 100% of fee AND +30% booked as claims → 130% double count.
- **After:** `_route_fee` splits freshly-collected fee 10% POL-growth / 15% backers /
  5% insurance / 70% depth; reserve re-enters only 70%.
- **Verify:** S7 in TEST-V041-REGRESSION (DUSD dir: kept=2.1, growth=0.3, ins=0.15).

## F4 — Native fee units · S7 / A6
- **Before:** shipped sim converted DERO-direction fee to a DUSD-equivalent value.
- **After:** DERO-direction fees → `pol_growth_dero`/`ins_dero`/backer-DERO;
  DUSD-direction → the DUSD analogues. No cross conversion anywhere.

## F5 — uint64-safe arithmetic · S16 / A16
- **Before:** `K = x·y` and `out = x·eff/(y+eff)` overflow uint64 (4.19e22, 4.18e22).
- **After:** reserves scaled by `RS = 100_000` atoms; staging for the numerator;
  `U64.add/mul` assert-guards for tests. Verify: TEST-V041-REGRESSION (S16) all budgets OK.

## F6 — Global anti-split pressure · S4 / A3
- **Before:** lock `u` was per-owner rolling → multi-address Sybil cut lock to 86d
  (vs 966d one-shot).
- **After:** `u_eff = max(owner_u, global rolling u)` where global u uses
  `sys_mint_hist` across ALL mints in the 1095d window.
- **Verify:** TEST-V041-REGRESSION (S4): n=10/100/1000 → 964.9/964.7/964.7 vs 966.9 one-shot
  (≤0.2% residual — flat, no exploitable gap).

## F7 — Insurance engine + mint halt · S13 / A13
- **New:** pool.ins_{dero,dusd} fed by 5% of fees (native); `insurance_ratio` =
  ins_dusd/debt; `mint()` halts when ratio < INSURANCE_TARGET (0.05) UNLESS the whole
  book is solvent at P0 (`_book_ok`: total_collateral×P0 ≥ total_debt×1.2).

## F8 — Block-anchored TWAP · S14 / A14
- **Before:** EMA alpha=0.05 updated per `advance()` call (function/instant granularity)
  → 50 × 100 DUSD/blk → TWAP +1924%; one 20K swap → +2194%.
- **After:** in `advance()`, `blocks = days·86400/18.5`; TWAP steps exactly `blocks`
  times with alpha=0.05 — anchor is per-block. Issuance remains spot-denominated
  (POL injection) — a deliberate, documented limitation.

## F9 — Solvency-safe expiry withdrawal · I11
- **New:** `withdraw(owner)` allowed only `t ≥ expiry`, amount ≤
  `max(0, min(collateral, collateral − debt/P0))`. Locks can't be broken early;
  P0-floor respected.

## F10 — Redemption, insurance backstop, bad-debt burn · S12 / A12
- **New:** `redeem_spot` (pool-spot swap, pool-depth-bound), `liquidate` at
  CR ≤ LIQ_CR (1.20) via collateral seizure + insurance absorption + bad-debt burn,
  `repay` for debt reduction.

## New/Changed constants
```
LIQ_CR = 1.20          (added)
QUARANTINE_DAYS = 30.0 (added)
INSURANCE_TARGET = 0.05 (added)
RS = 100_000           (added; integer scaling)
LOOP_CAP_RATIO = 0.0   (declared; NOT wired)
```

## Files
- `dusd_v041_sim.py` — full V0.4.1 simulator (this changeset applied).
- `test_V041.py` / `TEST-V041-REGRESSION.txt` — regression vs every V0.4 attack.
- `mc_v041.py` / `MC-V041.txt` — 50k-path Monte Carlo (zero exceptions; AMM-pump bound −97%).
- `TEST-RECURSION-V041.txt` — F2 quarantine bound experiment.
- `test_S*.py` + `TEST-S*.txt` — full V0.4 suite (unchanged spec tests).