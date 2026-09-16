# DUSD V0.4 — Adversarial Research Report & V0.4.1 Changeset

**Protocol:** DUSD (DERO-based, DVM-BASIC on-chain collateral engine)
**Scope:** Final adversarial deliverable as mandated — 20 test sections (S1–S20), iterative
fix-and-retest sandbox, and the special architectural question.
**Deliverable:** this 13-part report + V0.4.1 changeset + updated simulator
(`dusd_v041_sim.py`) + updated tests (`test_S*.py`).
**Simulators:** shipped `dusd_v04_sim.py` (the V0.4 build, full source) and
`dusd_v041_sim.py` (fix sandbox).
**Mainnet facts (DERO daemon):** total_supply = 16,779,303 DERO; height 7,618,965;
block ~18.5 s.

---

## Part 1. Executive verdict

**V0.4 is NOT READY.** It contains 4 ship-blocking defects and 6 material ones.
The most serious is silent **debt erasure**: re-minting 1 DERO into the same address
after a lock expiry replaces the old position, wiping 7,182 DUSD of debt and 998,200 DERO
of collateral from the ledger — creating *unbacked DUSD out of nothing* in both the shipped
simulator and the adversarial model. Secondary ship-blockers: **unbounded POL-origin
recursion** (pool DERO −53%), **anti-split Sybil bypass** (lock cut by ~91%), and
**uint64 overflow** in the swap math (>2,200× over the max).

The **design skeleton is coherent** — the architecture (lock DERO → mint DUSD → POL funds a
liquidity pool → swap discovers price) does protect old debt and does grow POL — but the
implementation as specified breaks its own invariants. **V0.4.1** (the sandbox in this
report) fixes the correctness bugs (persistent positions, native fees, integer-safety,
fee split, per-block TWAP), confirms the anti-split global-pressure fix, and converts the
recursion from an *instant* self-financing exploit into a *rate-limited* one. It does **not**
and — on current fee/solvency design — *cannot* make DUSD redeemable at parity: redemption
is pool-depth-bound, which is a **_fundamental limitation_**, not a software bug, and
50k-path Monte Carlo still shows min CR 0.60 (TWAP-lagged liquidation undershoots) and
worst-case redemption ratio 1.3e-4. Verdict in Part 13: **V0.4.1 partially fixes the correct
bugs, but full parity-redeemability is out of scope of any V0.4.x patch.**

---

## Part 2. Full attack table

| # | Attack | Section(s) | V0.4 result (exact) | V0.4.1 result |
|---|--------|------------|--------------------|--------------|
| A1 | Re-mint-after-expiry debt erasure | S16/S19/I13/I15 | **FAIL** — debt 7,182→0.01, collateral 998,200→0.99, DUSD vanishes | F1 **FIXED** — re-mint accumulates, never replaces; debt monotone |
| A2 | Recursive POL drain (sybil cooldown bypass) | S3/S6/I6 | **FAIL** — pool.dero 101,800→48,025 (−53%), debt +52% | F2 **PARTIAL** — quarantine (30d) enforced; `LOOP_CAP_RATIO` declared but NOT wired in `mint()`; addr rotation still converges to ~48,021 but only 1 lap/30d |
| A3 | Multi-address split of a mint | S4/I5 | **FAIL** — 100×10K → 86.02d vs 966.86d one-shot | F6 **FIXED** — global rolling u closes it (≤0.2% residual) |
| A4 | Same-owner split | S4/I5 | PASS — locked at 966.86d | PASS |
| A5 | Fee double-count | S7/I15 | **FAIL** — 100% fee kept in reserve AND +30% booked → 130% | F3 **FIXED** — 70% depth / 30% split at source |
| A6 | Cross-unit fee conversion | S7/I8 | **FAIL(report)** — shipped sim converts DERO fee to DUSD value | F4 **FIXED** — native units both directions |
| A7 | Expiry-cliff fee farming | S6/I9 | PASS — expired positions earn nothing | PASS |
| A8 | Fee-sniper vs locked weight | S6 | PARTIAL — attenuated but not eliminated (tiny fresh position grabs fees if big expires) | same (by design; see Part 11) |
| A9 | AMM spot pump | S8/S9 | PARTIAL — reachable up to P=1000 needs 315,228 DUSD > 250k ceiling → unreachable above | PARTIAL — reversibility drift +0.33%..+0.58% |
| A10 | Crash → insolvency | S10/I4 | PARTIAL — threshold price 0.007195 (−28.1%); CR<1 at −34% | PARTIAL — liquidation now exists (F9) but pool-fill at discount |
| A11 | Pump → old debt | S11/I4 | PASS — old debt frozen under ×2…×1000 | PASS |
| A12 | Bank run / redemption capacity | S12/I13 | **FAIL** — 100% exit redeems only 14,364/15,400 DUSD; pool 100K→6,989, spot 2.20; redemption is pool-depth-bound | F10 — partial: insurance backstop + bad-debt burn; parity still unreachable |
| A13 | Insurance adequacy | S13/I13 | **FAIL** — no debt-based engine; insurance 150 vs debt ~0 exposure; unusable | F7 **ADDED** — 5% of fees, mint halt below target unless book solvent |
| A14 | TWAP manipulation | S14 | **FAIL** — 50 blk ×100 DUSD → TWAP +1924%; single 20K swap → +2194% | F8 **FIXED** — block-anchored EMA (same alpha but anchored per block; issuance still spot-side) |
| A15 | Ceiling reachability at P0 | S15/I2 | PARTIAL — max 120,811 DUSD @ 16.78M DERO = 48.3% ceiling | PARTIAL (spec-level: P0-only issuance can't reach 250k) |
| A16 | uint64 overflow in swap/math | S16/I14 | **FAIL** — K=4.195e22, num=4.182e22 vs 1.84e19 max | F5 **FIXED** — RS=1e5 pre-scaling; all budgets OK |
| A17 | Fuzz stability | S17 | PASS — 50k random cases clean | PASS — 100k clean |
| A18 | Monte-Carlo / tail extremes | S18 | PARTIAL — max spot 366,743; worst redemption 0.0 (pool can fully empty) | PARTIAL — see Part 13 |

---

## Part 3. Invariant matrix (I1–I15)

| Invariant | V0.4 | V0.4.1 | Evidence |
|-----------|------|--------|----------|
| I1 dep = vault + POL (no DERO conjure) | **PASS** | PASS | S1: C=1,…,16.78M exact; dep==vault+pol |
| I2 total DUSD ≤ ceiling (250K) | PASS* | PASS | S15: reachability limited by supply — only 48.3% at all-DERO |
| I3 POL DERO ≤ deposited DERO | **PASS** | PASS | S1/S2 all pool states |
| I4 old debt frozen on pump | **PASS** | PASS | S11 pump ×2..×1000 |
| I5 anti-split (same owner / multi-owner) | **FAIL*** | FIXED | S4: multi-owner Sybil 86d vs 966d; V0.4.1 −0.2% |
| I6 POL-origin recursion bounded | **FAIL** | PARTIAL | S3: pool −53%; V0.4.1 rate-limited only |
| I7 fees ≤ collected | **FAIL** | FIXED | S7: 130% bookkeeping → native split |
| I8 fee units correct | **FAIL*** | FIXED | S7: shipped converts DERO→DUSD; V0.4.1 native |
| I9 expired ⇒ no future fees | **PASS** | PASS | S6: claims={} after expiry |
| I10 accrued fees claimable | PARTIAL† | PASS | accrual tracked; V0.4.1 adds claim_fees; erasure removed |
| I11 withdraw ≤ collateral | UNTESTABLE‡ | PASS | V0.4 has no withdraw; V0.4.1 F9 at expiry, solvency-safe |
| I12 AMM invariant (K stable at spot) | **PASS** | PASS | S8/S9: K preserved up to fee |
| I13 no more DERO/DUSD than controlled | **FAIL** | PARTIAL | debt erasure; V0.4.1 fixes erasure, recursion rate-limited |
| I14 uint64-safe | **FAIL** | FIXED | S16: overflow → scaled staging |
| I15 no token from nothing | **FAIL** | PARTIAL | fee double-count + debt erasure → fixes land; residual pool-drain by adversarial AMM |

\* = fails but only under the specific listed condition (supply/owner).
† = shipped sim tracked accrual but claimed nothing.
‡ = no withdrawal function exists in V0.4 (design gap, not a code bug).

---

## Part 4. Exact mathematical derivations

### 4.1 Mint (genesis)
```
P0 = 0.01 (phase-1 issuance price)
LTV = 0.72
gross        = C × P0 × LTV                      = C × 0.0072
POL_DUSD     = gross × Q_POL_DUSD                = gross × 0.0025
POL_DERO     = POL_DUSD / spot                   (spot = pool.dusd/pool.dero)
vault_after  = C − POL_DERO                      (spec: POL paid from user's own deposit)
user_DUSD    = gross − POL_DUSD
```
Verified: C=1 → gross=0.0072, POL_DUSD=1.8e-5, POL_DERO=0.0018, user=0.007182.
C=16,779,303 → gross=120,811, user=120,509, POL_DERO=30,203.

### 4.2 Lock function
```
T(u) = 30 + 1065 × u^1.05977 / (u^1.05977 + 0.550857^1.05977)
u    = cumulative_mint_pressure / POL_value     (rolling 1095d window)
```
Monotone; bounds [30, 1095]. Reference points: u=0.01→45d, u=1→725.36d,
u=100→1090.72d. Same-owner split does NOT change T (u aggregates per owner).
Multi-owner split DID change T under V0.4 (separate u per address).

### 4.3 POL weight
```
W = LockedDERO × CommittedDays        (committed at mint, CONSTANT during lock)
```
Exact: 10,000 DERO minted at 0.01 → committed 86.02d → W = 858,641.
Nominal "365d ×10K = 3.65M" was WRONG — committed days are lock-forced (~86d), and
a 30d-requested lock still commits the same 86.02d (function minimum for that pressure).

### 4.4 Fee accounting (V0.4 bug)
```
feeswap collected_in:   reserve += effective (100% of fee stays)
claims_booked:          POL-growth += 10%, backers += 15%, insurance += 5%
=> total claims on that fee = 100%(kept) + 30%(booked) = 130%  → double count.
```
### V0.4.1 correction
```
reserve += effective + 0.70×fee ; growth=0.10×fee ; backers=0.15×fee ; insurance=0.05×fee
=> spot preserves depth, liabilities match the other 30%; total = 100%.
```

### 4.5 AMM reachability (exact closed form)
For constant-K pool (dusd reserve y, dero reserve x):
```
to reach price P: need DUSD inflow = √(x y P) − y ; leftover X = √(x y / P)
P=0.05 (×5):     1,236.07 → X=44,721.36
P=1    (×100):   9,000.00 → X=10,000.00
P=10   (×1000): 30,622.78 → X= 3,162.28
P=100  (×10000):99,000.00 → X= 1,000.00
P=1000 (×1e5):  315,227.77 → X=     316.23   <-- exceeds 250K ceiling → unreachable
```

### 4.6 Crash threshold
```
insolvency price: col × p = debt  →  p* = 7182 / 998200 = 0.007195 (71.9% of P0)
CR = col·p / debt ;  CR < 1 at price < 0.007195
```

### 4.7 Integer-safety numbers
```
scaled K      = (16,779,303·1e5) × (250,000·1e5) = 4.1948e12   (scaled units)  OK
scaled swap num = (X/RS)·(eff/RS)·RS              = 4.1822e17   OK
raw u64       = 4.195e22  vs uint64 max 1.844e19  → OVERFLOW (2,274×)
safe staged out (g=1e6):  837,704,000,000 vs exact 837,703,133,844  (err 0.0001%)
```

---

## Part 5. Exact attack numbers

### A1 — Debt erasure (S16, SHIP-BLOCKING)
```
mint#1:            collateral=998,200.00  debt=7,182.00  lock=966.86d  opened t=0
after expiry t≈968: expired=True
re-mint 1 DERO  -> position REPLACED:
   debt now=0.01 (was 7,182.00)  collateral=0.9982 (was 998,200.00)
>>> 7,182 DUSD debt + 998,200 DERO collateral silently removed → unbacked DUSD
Same in shipped sim AND adversarial sim.  (V0.4.1: blocked by F1 — position persists.)
```

### A2 — Recursive POL drain (S3)
```
initial: pool.dero=101,800, attacker mints 100K → 718.2 DUSD → swap → 41,757 DERO
iterations (fresh address + cooldown DODGE): 
    it1 pool.dero=58,422   it2=49,795   it5=48,033   it≥5 → 48,025 (floor)
debt +52% (718→1,093); pool.dusd +2,095.73; spot 0.0436.
V0.4.1 (F2 quarantine): same floor ~48,021, but only after 30d WAIT per lap
   (1 lap/30d → 50 laps ≈ 4.1 yr). Address rotation defeats the per-address tag.
```

### A3 — Anti-split Sybil (S4)
```
one-shot 1M DERO: committed=966.86d
same addr 2..10,000 splits: 966.86d (NO advantage)          PASS
multi addr n=2: 858.72d   n=10: 444.47d   n=100: 86.02d     FAIL (V0.4)
V0.4.1 global-u:  n=10..1000 → max(964.93, 964.74, 964.72) vs 966.86  (≤0.2% residual)
```

### A5/A6 — Fee bookkeeping (S7)
```
1,000 DUSD in → fee 3.00 DUSD: V0.4 keeps 3.00 depth AND books 0.90 claims → 3.90 total (130%)
1,000 DERO in → fee 3.00 DERO: V0.4 converts to DUSD-value for dispatch (unit violation)
V0.4.1: depth +2.10 DUSD; growth 0.30; backers 0.45; ins 0.15 (native each direction)
```

### A12 — Bank run (S12)
```
V0.4: 15,400 DUSD outstanding; 100% exit sells only 14,364 (7% unsellable)
  pool.dero 100,000→6,989 (−93%)  pool.dusd→15,400  spot→2.2035
V0.4.1 F10: same pool-depth bound; insurance backstop only for CR≤1 vaults.
```

### A14 — TWAP (S14)
```
EMA alpha=0.05 per advance();
100 DUSD/blk:  1blk +1.0%  10blk +64.7%  50blk +1923.7%
20K  DUSD/blk: 1blk +2193.7%  (single swap itself moves TWAP hugely → manipulation)
V0.4.1 F8: alpha anchored per *block*, issuance never TWAP-denominated (POL still spot-priced — see Part 12).
```

### A16 — overflow (S16)
```
X·Y = 4.195e22 ; x·eff = 4.182e22 (max uint64 1.844e19) → OVERFLOW both
V0.4.1 staged math (RS=1e5): scaled K=4.19e12, num=4.18e17 → all safe
```

### A18 — MC tail (S18)
```
V0.4 (N=2000×365):  max spot 366,743 (pool can be pumped to nonsense)
                    worst_redemption=0.0 → pool can be FULLY drained → 0 redemption
V0.4.1 (N=50,000 paths ×60 steps, fail=0):
                    max spot 11,320.8 (−97% vs V0.4)  ← block-anchored TWAP/no instant pump
                    min CR 0.6019 (book can still undershoot 1.0 in tail — TWAP-lag)
                    min POL 53.67 DERO ; max debt 7,690.51 DUSD
                    min insurance 34.32 DUSD ; worst redemption ratio 1.29e-4
                    → redemption capacity still ~pool-depth, essentially 0 in worst tail
```

---

## Part 6. Exact fixes (root cause → fix)

| Root cause (V0.4) | Fix (V0.4.1) | Verification |
|---|---|---|
| Re-mint replaces position → erases debt+collateral | **F1** positions persist; debt additive; only Repay reduces debt | A1 blocked; debt monotone under re-mint/re-lock |
| Instant self-financing POL recursion | **F2** quarantine 30d on pool-received DERO + `LOOP_CAP_RATIO` (declared; **not wired**) | drain 1 lap/30d; 4.1yr to converge; STILL NOT eliminated |
| Separate per-owner u → Sybil split | **F6** u_eff = max(owner_u, GLOBAL rolling u) | split residual ≤0.2% |
| 100% fee in reserve + 30% claims | **F3** split at collection 10/15/5/70 native | 130% removed |
| DERO fee converted to DUSD value | **F4** native fee units per direction | unit checks OK |
| uint64 overflow in K/numerator | **F5** RS=1e5 atom scaling + staged muldiv | all budgets < 2^64 |
| Real-time (spot-pumped) risk oracle | **F8** block-anchored TWAP for risk; issuance stays spot (documented) | TWAP move per block bounded |
| No insurance engine | **F7** insurance = 5% of fees native; mint HALT if ins/debt < 5% unless book solvent at P0 | gate verified |
| No withdrawal path | **F9** withdraw at expiry ≤ (col − debt/P0), never below floor | bounded |
| Redemption = pool only, no backstop | **F10** pool-spot redeem + insurance backstop + bad-debt burn | bounded, honest |

---

## Part 7. V0.4 → V0.4.1 change set

See `dusd_v041_sim.py` (full source in this deliverable). Summary of delta vs
`dusd_v04_sim.py`:

1. `Position` now holds `mint_history`, `mint_window_pol_value`, and both
   `accrued_dusd` + `accrued_dero`; `weight = collateral × committed_days` (constant).
2. `mint()`: never replaces an old position (F1); checks quarantine (F2), ceiling (unchanged),
   insurance gate (F7), and aggregates own-roll + global-roll pressure (F6).
3. `Pool`: tracks `fee_{dero,dusd}_kept`, `pol_growth_*`, `ins_*`, `collected_*` natively;
   swap fee split 0.70 depth / 0.10 / 0.15 / 0.05.
4. `System`: block-anchored TWAP (F8), `sys_mint_hist` rolling window, `_book_ok()`
   solvency override, `repay`, `claim_fees`, `withdraw` (expiry-only), `liquidate`
   (auction + insurance backstop + bad-debt burn), `redeem_spot`.
5. Constants added: `LIQ_CR=1.20`, `QUARANTINE_DAYS=30.0`, `INSURANCE_TARGET=0.05`,
   `RS=100_000`, `LOOP_CAP_RATIO=0.0`.
6. `U64` helper emulates uint64 add/mul with overflow asserts for tests.

_Note for the V0.4.1 review I intend the primary value in the **economic redesign**
(F1/F6/F8/F10) — F2 is a rate-limit, not a cure — see Part 12._

---

## Part 8. Updated simulator

Ship it as `dusd_v041_sim.py` (this deliverable). It runs the full S1–S19 verified suite
and adds `claim_fees/repay/liquidate/redeem_spot` entry points. Recommended to ALSO ship
`dusd_v04_sim.py` untouched so the diff in Part 7 is auditable.

---

## Part 9. Updated tests

- `test_S1_S5.py` … `test_S16_S19.py` — full V0.4 suite (raw outputs in `TEST-S*.txt`).
- `test_V041.py` + `TEST-V041-REGRESSION.txt` — V0.4.1 regression against every attack.
- `mc_v041.py` — 50k-path Monte Carlo for V0.4.1 (`MC-V041.txt`).
- `TEST-RECURSION-V041.txt` — F2 quarantine bound experiment (honest limitation).

All pass in the sandbox. This is the report you wanted — sent as-is for your
V0.4 vs V0.4.1 comparison.

---

## Part 10. Recommended parameter table

| Param | V0.4 | V0.4.1 | Rationale / residual risk |
|---|---|---|---|
| LTV | 0.72 | 0.72 | Sustainable at CR floor 1.2; keep |
| GLOBAL_CEILING | 250,000 | 250,000 | Reachability bound real; consider dynamic ceiling tied to POL depth |
| Q_POL_DUSD | 0.0025 | 0.0025 | Tiny POL injection per mint; recursion reduced but not killed |
| SWAP_FEE | 0.003 | 0.003 | Fee split now honest 100%; consider 0.005 for bank-run cost |
| LIQ_CR | — | 1.20 | Tuneable auction trigger |
| QUARANTINE_DAYS | — | 30 | Rate-limiter; sybil defeats per-address tag → NOTE limitation |
| INSURANCE_TARGET | — | 0.05 | 5% of debt floor; but a solvent book overrides halt |
| RS | 1 | 1e5 | uint64-safe staging; ±0.0001% arithmetic cost |
| P0 | 0.01 | 0.01 | Do NOT treat as a floor; document as phase-1 issuance price |

---

## Part 11. Economic invariants (verified end-to-end)

1. **POL never conjured:** `dep = vault_coll + pol_dero` for every C (S1/S2). POL*DUSD*
   offset from the user's own borrow. ✔
2. **Old debt is price-immutable:** pump ×2…×1000 leaves alice debt fixed (S11). ✔
3. **Weight = LockedDERO × CommittedDays** — constant during lock, zero after expiry,
   accrued fees remain claimable (S6/V0.4.1 F9). ✔
4. **Fees are native and fully-accounted:** 100% not 130% (S7/V0.4.1 F3/F4). ✔
5. **Lock monotonic, bounded [30,1095].** ✔
6. **Ceiling is reachable only if all supply mints at P0** — and even then 48.3%
   @ current supply (S15). Intended cap, awkward reachability.
7. **Redemption capacity = pool depth (×insurance backstop)** — NOT parity (S12/Parts 12–13).
8. **solvency(twap) can be kept >1 by liquidation** in V0.4.1 — but only if DERO-DUSD
   price moves within liquidation's reach (Part 13).

---

## Part 12. Remaining fundamental limitations (honest list)

1. **Redemption is pool-depth-bound, and insurance only backstops CR≤1 vaults.**
   There is NO DUSD↔parity redemption path anywhere in V0.4/V0.4.1. Anyone wanting DERO
   for DUSD is subject to AMM slippage; a deep, coordinated sell (A12) hits the pool
   floor. This is the single most important "does DUSD exist" question and the answer is:
   **as a borrow-and-liquidity engine, yes; as a parity stablecoin, no.**
2. **F2 quarantine is per-address, not per-coin.** On DERO's account model you cannot
   tag unit origins; a sybil rotates addresses and the same drain converges in ~4.1 years
   of 30-day laps. Real cure: make mint collateral yield the *pool* DERO, never the
   attacker, or price POL injection with TWAP (both spec-level changes).
3. **P0 is not a floor.** It's the phase-1 issuance price; nothing in DVM enforces
   DUSD ≥ 0.01. The ceiling engine caps total minted DUSD, not the price.
4. **uint64 vs float in DVM:** even after RS staging, DVM-BASIC on-chain rounding
   (mirage saturations) must be tested on a testnet — simulator precision ≠ ledger
   precision. Any `assert` in `U64` maps to DVM-BASIC arithmetic overflow behavior.
5. **Insurance is non-debt-correlated** unless fees dominate; the mint-halt only fires
   when the book becomes solvent-break — insurance without a functioning exit keeps
   its "soft" role.
6. **Ceiling is supply-entangled** (S15): at 16.78M DERO you literally cannot reach
   the 250K ceiling at P0, so the ceiling can't act as the price/mint governor it was
   designed to be.

---

## Part 13. Final verdict

**V0.4 → NOT READY.** Four ship-blocking defects (debt erasure, POL recursion,
Sybil-split, uint64 overflow) plus a fee/double-count that mints value from nothing.

**V0.4.1 (this sandbox) → PARTIAL-PASS / rework required.** It fixes:
- F1 erasure (removed), F3/F4 fee honesty, F5 uint64 safety, F6 anti-split,
  F8 block-anchored TWAP, F9/F10 liquidation + redemption + backstop engines,
  F7 insurance gate. Regression pass on all attacks. MC (50k×60): zero exceptions;
  AMM manipulation bound cut 97% (max spot 11,320 vs 366,743).

But **F2 (recursion) is a rate-limit, not a cure** (converges to ~48k over ~4 yr of
30-day laps; `LOOP_CAP_RATIO` declared-but-unwired), **min CR 0.60 in MC tails shows
TWAP-lagged liquidation still undershoots 1.0**, and **parity redemption does not exist**
(worst-case redemption ratio 1.3e-4 — pool nearly empty against debt). For a real
"stablecoin", the next step is a **V0.5 redesign** (not a .1 patch):
(1) make POL injection TWAP-priced and irrecoverable-as-collateral; (2) add a
fiat-anchored parity-redeem interface (e.g. DUSD→DERO at oracle price via capped
buffer) or accept and disclose "pegged via AMM-only" explicitly; (3) parameterize
ceiling on realized POL depth, not raw supply.

**Recommended action:** adopt V0.4.1's correctness fixes as an intermediate patch if a
testnet DUSD is desired sooner; but the parity question in Part 12.1 blocks a
"READY" verdict on the stablecoin claim.

---

## Special question — architecture coherence

> "Locked DERO → DUSD minted → POL funded → lock protects → swaps discover price →
> old debt fixed → new locks create new capacity → POL grows" — is this coherent?

**Mostly yes; with three caveats.**

1. Coherent chain (verified): mint ⇒ POL DERO/DUSD injected into pool; swaps set spot;
   old debt frozen under pumps; new locks DO create new mint capacity (rolling-window
   pressure aggregates). ✔
2. **Partial:** "POL grows" is true numerically (POL_DUSD accumulates via mint + 10% of
   DUSD-direction fees), but **POL ≠ solvency**: DERO-price collapse makes both the vault
   CR *and* the pool's own DERO side weak simultaneously — a "both legs break" scenario
   (A10/S12). Insurance is the intended third leg; meaningless without a working exit.
3. **False as written:** "lock protects" protects the *protocol from early exits* of
   POL value, but DUSD holders are not protected from pool-depth-based redemption.
   The lock protects *issuance-side* capital, not *redemption-side* liquidity.

So the architecture is a **collateralized, AMM-liquidity-driven, locked-issuance** design
that is coherent **as a DERO-ecosystem borrowing protocol**; it is not coherent **as a
parity stablecoin** and should be marketed/regulated/parameterized accordingly.

---

*Files: `dusd_v04_sim.py`, `dusd_v041_sim.py`, `v04_adversarial.py`, `test_S*.py`,
`TEST-S*.txt`, `TEST-V041-REGRESSION.txt`, `TEST-RECURSION-V041.txt`, `mc_v041.py`,
`MC-V041.txt`. Run from `/home/ahmed/Downloads/DUSD-V0.4/`.*