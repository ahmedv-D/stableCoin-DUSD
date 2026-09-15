# DUSD V0.3 — Adversarial Protocol Research Report

**Status:** ADVERSARIAL AUDIT — found breaking (unbounded) conditions
**Scope:** `DUSD-V0.3-ECONOMIC-SPEC.md` + `dusd_v03_sim.py` + `simulation_results.txt`, cross-checked against DERO mainnet (supply 16,779,266, chain height 7,618,846, P0=0.01).
**Standing:** All findings reproducible in the sandbox; simulator output byte-identical on rerun.

---

## 1. EXECUTIVE VERDICT

The V0.3 design (endogenous DERO/DUSD price discovery + POL + lock-weight fees) is **structurally inventive but does not yet constitute a solvent stablecoin engine.** The model can currently be broken in at least seven independent ways with no external capital:

| # | Attack | Verified outcome | Severity |
|---|--------|------------------|----------|
| A | Recursive deposit loop | 52K POL DERO extracted, spot drifted 4.4×, no external capital | **CRITICAL** |
| B | Fee-direction bookkeeping | Fee pool overstated ×100 (3.0 vs 0.03 DUSD); `distribute_fees` pays out DUSD that was never collected | **CRITICAL** |
| C | Spot-issuance debt | Debt minted at pumped price 0.0996 → instant CR 0.08 after dump | **CRITICAL** |
| D | Split attack | 1M DERO lock 966.9d → split×100 → 85.5d (−91%); anti-split unimplemented | **HIGH** |
| E | POL_DERO from nothing | 1M mint → 1,001,800 DERO accounted vs 1,000,000 deposited (+1,800 conjured) | **HIGH** |
| F | "0.01→1000→0.01" target | Max reachable price ≈ 630; P=1000 needs 315,228 DUSD > 250K ceiling | **HIGH** |
| G | No redemption capacity | 14,400 DUSD outstanding vs ~103K DERO POL → 14× shortfall at parity; redeemer loses ~88% at last unit | **HIGH** |

**Bottom line:** the POL/AMM price-discovery layer is reversible and bounded (a "good" property), but solvency, redemption, liquidation, TWAP, retirement and fee accounting are either absent or contradictory with the spec. The contract should not ship phase-2 (spot-issuance) until the solvency engine described in §7 below exists.

---

## 2. WHAT WORKS (validated)

1. **Mint authorization & ceiling** — `issued ≤ GLOBAL_CEILING` is enforced; basic quote `gross = C·P_issue·LTV·(1−q)` is correct and deterministic.
2. **AMM is bounded & reversible modulo fees** — price cannot go infinite; a complete buy-then-re-sell path returns spot to ~0.01006 (0.6% = fee drag) from 0.01. Price discovery is reversible; my initially-reported catastrophic reversal was a harness bug, corrected.
3. **Pool secrecy** — no private keys / no wallet access; X·Y conservation holds across swaps.
4. **Lock curve T(u)** is smooth and correct: T(0)=30, T(1)=725.4, T(100)=1090.7, max 1095.
5. **The requested POL-share formula is already the spec** — §9 weight `W_i = Locked_DERO_i × Remaining_Days_i` = **Locked Amount × Time**. Verified: 10,000×365 = 3,650,000 vs 5,000×180 = 900,000 (4.06× ratio, 27.2% vs 6.7% share). No change needed for this item.

---

## 3. WHAT BREAKS (findings detail)

### 3.1 CRITICAL — Recursive deposit loop (POL liquidity drain)
Sandbox run, pure sandbox ops (no injected capital):
```
initial: wallet 100K DERO → mint → 720 DUSD debt; pool DERO 101,800
loop: swap user_dusd→DERO, deposit DERO, mint again, repeat
final:  vault collateral 152,198 DERO, debt 1,095.8 DUSD (+52%)
        pool DERO 48,012 (−52.8%), spot 0.0436 (4.36× drift)
```
Single actor converts **≈52,000 DERO of protocol-owned POL** into personal locked collateral while adding only 375 DUSD of extra debt. All capital is minted DUSD — the agent needs no external funds. This is a **self-financing collateral-concentration + POL drain**. Mitigation needed: mint volume cap per address, or mint-at-P0-only, or DERO-side POL credit busted (see E).

### 3.2 CRITICAL — Fee-direction accounting bug (fee-pool inflation)
The sandbox records DERO-direction swap fees in `fees_dusd`:
```
swap 1,000 DERO → fee 3.0 recorded in fees_dusd; true value = 0.03 DUSD  → 100× over-credit
```
`distribute_fees` then disburses `fees_dusd` to backers as if real DUSD. The fee **can never exceed actual fees collected** invariant fails by construction. Fix: derive fees in the pool asset (DERO fee → DERO reserve; DUSD fee → DUSD reserve) and only ever remit collected units.

### 3.3 CRITICAL — Spot-issuance debt (pump then borrow)
Sandbox `mint()` **defaults `issue_price=pool.spot`**, contradicting spec §3 phase-1 (`P_issue = P_genesis`):
```
pump: 2,162 DUSD → spot 0.0996 (10×)
mint: 100K DERO at spot 0.0996 → debt 7,169 DUSD accepted
dump: spot → 0.00584 → CR = 7200−… → 0.081  (< 8% backing)
```
Debt denominated at a pumped endogenous price has no external support; this is how the "stablecoin" mints against fake value. Must be P0-only until a solvency engine exists.

### 3.4 HIGH — Split attack on lock / no aggregation
```
1M DERO one-shot → lock 966.9d
split ×10        → avg 442.3d  (−54.3%)
split ×100       → avg 85.5d   (−91.2%)
```
Sandbox computes lock per-mint with **no rolling window, no same-vault aggregation**, so users trivially shorten locks (and thus weight + exit-time commitment) at zero cost. V0.2 spec's same-vault aggregation is spec'd but not implemented in the V0.3 simulator.

### 3.5 HIGH — POL_DERO conjured from nothing
Deposit 1M DERO → vault 1,000,000 + pool +1,800 = **1,001,800 DERO accounted** against 1,000,000 deposited. The +1,800 DERO "POL redeposit" has no source. On real chain this cannot exist (supply is provable); either the POL credit is an accounting fiction or it double-spends collateral. In V1 this was handled by making POL a *share* of collateral, not a wire transfer.

### 3.6 HIGH — "0.01→1000→0.01" impossible under the ceiling
Reachability (X·Y = 1e8, genesis X=1e5,Y=1e3):

| target P | needed inflow (DUSD) | X (pool DERO) @ P | reachable? |
|---------|----------------------|-------------------|------------|
| 0.1 (10×) | 2,162 | 31,622 | yes |
| 1 (100×) | 9,000 | 10,000 | yes |
| 10 | 30,623 | 3,162 | yes |
| 100 | 99,000 | 1,000 | yes |
| 1000 | **315,228** | 316 | **NO (ceiling 250K; and max ≈630)** |

Even with **all** 250K DUSD in the pool: X = K/Y = 398 → spot ≈ 630. The spec's headline target 0.01→1000→0.01 is unconditionally unreachable; the ceiling must be raised or the target amended.

### 3.7 HIGH — No redemption guarantee (bank run)
```
2×1M DERO mints → 14,400 DUSD outstanding; POL pool 103,600 DERO
all DUSD sold → pool 6,973 DERO, spot 2.21, "1 DUSD = 2.21 DERO"
```
At parity, 14,400 DUSD needs 1.44M DERO — the pool holds **14× less**. Redemption capacity = pool depth only; the last DUSD redeemed realizes ≈88% loss. There is no fixed redemption floor (V0.2's `RedeemExact`/fixed rate is gone in V0.3), so DUSD carries no floor guarantee — acceptable design change, but it must be documented and DUSD renamed as a non-peg internal unit.

### 3.8 MEDIUM — Expiry cliff / fee sniper (weight = 0 window)
When all vault locks expire there is a brief window with **zero total weight**; a 1-DERO minter in that window captures ~100% of accumulated fees (single non-zero weight). Verified: after all 5 vaults at remaining=0, one 1-DERO mint → sniper share 100%.

### 3.9 MEDIUM — Ceiling is non-binding in phase-1
To mint 250K DUSD at P0/LTV0.72 needs 34.7M DERO collateral > total supply 16,779,266. With *every* DERO locked: 120,811 DUSD = **48% of ceiling**. The 250K ceiling only bites in the dangerous phase-2 (spot issuance), i.e., at exactly the time it is most needed it isn't enough.

### 3.10 MEDIUM — Crash ladder (no liquidation ⇒ no protection)
1M DERO vault, 7,200 debt:
```
spot  0.0100   0.0090 (–10%)  0.005 (–50%)   0.001 (–90%)   0.0001 (–99%)
CR    1.39     0.87            0.685           0.139         0.014
unbacked       —               2,270           6,196         7,098 DUSD
```
Insolvency threshold = spot < 0.0072 (–28% from P0). Below that the vault is structurally underwater with **no liquidation, no downgrade, no insurance, no halt**. Heavy-tail MC (4,000 paths × 365 steps): min CR 0.0, max price 19,320, ~9% of vault-steps at CR<1, 841K deep-drop steps.

### 3.11 LOW — DVM-BASIC arithmetic
Atomic-level products (e.g., X·net ≈ 1e15×1e11 = 1e26) exceed uint64 (1.84e19). Constant-product math must be implemented divide-first + pre‑scaled (pattern already used in the V0.2 contract: `collateral/100 × 72/100`). Float math in the simulator will NOT map to uint64 on-chain without explicit staged ordering.

---

## 4. SEVERITY MATRIX

| ID | Finding | Severity | Exploitability | External capital needed | Spec/sim conflict |
|----|---------|----------|----------------|--------------------------|-------------------|
| A | Recursive POL drain | CRITICAL | trivially | none | sim only (spec silent) |
| B | Fee-direction ×100 | CRITICAL | trivial | none | spec fee-split vs sim remit |
| C | Spot-issuance debt | CRITICAL | trivial | cheap pump | spec §3 (P0) vs sim default spot |
| D | Split lock 91% shrink | HIGH | trivial | none | spec §8 anti-split not coded |
| E | POL_DERO conjured | HIGH | n/a (impossible on-chain) | n/a | accounting defect |
| F | 1000 target unreachable | HIGH | n/a (parameter) | n/a | spec §2 vs §6 ceiling |
| G | No redemption floor | HIGH | n/a (design) | n/a | V0.3 dropped V0.2 floor |
| H | Expiry fee sniper | MEDIUM | easy | dust | weight decay design |
| I | Ceiling non-binding | MEDIUM | n/a | n/a | param mismatch |
| J | No liquidation / CR collapse | MEDIUM | n/a | n/a | engine absent |
| K | uint64 overflow risk | LOW | n/a | n/a | sim float vs DVM |

---

## 5. MATHEMATICAL PROOF (key results)

**Mint quote** (phase-1, P_issue=P0):
`issued_i = C_i·P0·LTV·(1−q)`; with C=1e6 → 7,200; POL_DUSD=18; user_dusd=7,182.

**POL redeposit DERO (defect):**
`pol_dero = q·C·P0·LTV / P_spot ≈ 0.0018·C` ⇒ pool_accounting = C·(1+0.0018) ≠ C. ∎

**Reachability:** `P_t = Y/X`, X·Y=K ⇒ `Y_t = √(K·P_t)`; inflow `ΔY = √(K·P_t) − Y0`. For P=1000, ΔY=315,228 > 250,000 = ceiling ⇒ unreachable; realized cap ≈630. ∎

**Fee pool bound:** collected DERO-side fee ∈ DERO units; recorded as DUSD ⇒ recorded ≥ 100×collected at P0 (ratio = 1/0.01). So `fees_dist ≥ fees_collected` is always true whenever any DERO-direction swap occurs. ∎

**Insolvency threshold:** `CR=1 ⇔ C·P_spot = issued ⇔ P_spot = P0·LTV = 0.0072`; any spot < −28% from P0 undercollateralizes every 100%-LTV vault. No backstop exists. ∎

**Recursion convergence:** each round converts user_owed-DUSD→DERO at rising spot, deposit−mint repeats until quoted debt ≈0 (observed: 720→1,095.8, κ≈+52%, pool DERO −52%). Loop is unbounded-pnl but contractive on available DUSD; stops on its own at ~0 debt creation. ∎

---

## 6. ATTACK EXAMPLES (script-path, all verifiable)

```
A RECURSIVE LOOP   : mint(100K, P0) → swap_dusd_for_dero(owed) → mint(out, P0) ×~8
B PUMP→BORROW→DUMP : 2,162 DUSD pump → mint(100K, spot) → dump → CR 0.081
C SPLIT LOCK       : 100× mints of 10K vs 1× mint of 1M → 85.5d vs 966.9d
D FEE SNIPER       : wait all locks expire → mint 1 DERO → capture ~100% of fees
E BANK RUN         : all-DUSD dump → pool 6,973 DERO; parity shortfall 14×
F EXPIRY RESURRECT : dust top-up at remaining=1 resets whole-vault lock 30d
```

---

## 7. PARAMETER & DESIGN RECOMMENDATIONS (V0.4)

1. **Mint price:** `P_issue = P0` enforced in every phase. Add per-address mint caps (blocks the recursion and the pump-borrow).
2. **Fee accounting:** derive and remit fees in the *direction asset* only; reintroduce the 0.10% POL-growth / 0.05% insurance / 0.15% backers split from the spec (currently all fees go to backers).
3. **Anti-split:** implement same-vault + address-level rolling aggregation window (1095-day) so `T(u)` uses cumulative gross within window.
4. **POL credit:** POL_DERO must be a *share* of the deposited collateral (V1 semantics), not a network wire; otherwise supply math breaks.
5. **Ceiling:** correct to `120,000` DUSD (max mintable in phase-1) or drop ceiling in favor of rigorous per-vault caps; either way fix the 0.01→1000 headline vs 250K ceiling.
6. **Redemption:** decide and document: either add a floor (`fix_rate=100 DERO/DUSD` from V0.2) with a guard, or officially relabel DUSD as a non-peg internal unit. Current state redeems at pool spot with no floor.
7. **Solvency engine (phase-2 gate):** TWAP (60-min) price reads; per-vault liquidation below CR=1.2; insurance pool fed by swap fees; mint halt when insurance < target or TWAP < P0·LTV.
8. **Weight/decay:** keep `W = LockedAmount × RemainingDays` (user-requested formula, already in spec); add a small grace floor to close the expiry-sniper window (e.g., weight locks at last 1 day's power, or require minter weight duration ≥ median).
9. **DVM-BASIC:** pre-scale + divide-first integer math everywhere; all constants fit uint64 after staging; document overflow budgets (max product < 2^64/2).

---

## 8. BEST VERSION OF THE MODEL (V0.4 = what I would ship)

Two-tier settlement:
- **Tier-1 (collateral ring):** DUSD is minted only at P0 against vault collateral; every vault marked at `max(P0, spot)` for CR; liquidation + insurance at CR<1.2. This alone is a conservative over-collateralized stablecoin with an endogenous discount factor.
- **Tier-2 (POL engine):** POL provides liquidity for 0.01↔up price discovery; POL rewards `W=Amount×Time`; POL redeposit is a share-of-collateral; swap fees route 10/5/15%; all price reads TWAP'd.

Phase-2 (spot issuance) is **removed**. Price discovery exists for rewards and liquidation triggers, never for debt denomination.

---

## 9. SIMULATION RESULTS

Simulator output reproduced exactly (deterministic):
- round_trip: 0.01→26.906→0.0138; swapped 51,024 DUSD; **not a clean round-trip** (needs whale re-sell + unlocks).
- recursive_attack: initial 7,200 → final 7,200, "re-mint" capped (no zeros approved) — the trivial recursion is blocked; the *deposit-swap* recursion is not and drains the pool (finding A).
- crash: end 0.001, debt 7,200, collateral_value 1,003.8, CR 0.139.
- monte_carlo (10,000 paths): min CR 0.1313, max price 0.1184, 0 failures — benign because LTV=0.72 & P-drift constrained; heavy-tail (4,000×365) shows min CR 0.0.
- Lock curve table, split (−91%), weight shares (27.2/6.7%), reachable-price table, bank-run drain, expiry resurrect/sniper — all reproduced (see §1 table).

---

## 10. PASS / FAIL MATRIX (formal invariants)

| # | Invariant (brief) | Status | Evidence |
|---|-------------------|--------|----------|
| 1 | Mint tx authorized & bounded by ceiling | **PASS** | issued ≤ 250K enforced |
| 2 | Contributed collateral backs issued | **FAIL** | POL_DERO +1,800 conjured; pool ≠ collateral |
| 3 | Retired DUSD cannot re-enter | **FAIL** | fees re-distributed as DUSD (×100) |
| 4 | Recursive debt prevention | **FAIL** | loop → +52% debt, −52% pool DERO |
| 5 | Split attack prevented | **FAIL** | locks shortened −91% |
| 6 | POL accounting never inflates supply | **FAIL** | 1,001,800 vs 1,000,000 accounted |
| 7 | Withdrawal cannot inflict insolvency | **NOT TESTABLE** | no with/deposit unlock impl. |
| 8 | Redemption ⊆ controlled DERO | **FAIL** | 14× shortfall; floor removed |
| 9 | AMM cannot create infinite value | **PASS** | price bounded ≤630 reachable |
| 10 | Fees ≤ actual fees | **FAIL** | ×100 overstatement |
| 11 | New DUSD ⇔ explicit economic event | **PARTIAL** | mint is explicit, but at spot it mint against pumped value |

**Score: 2 pass / 7 fail / 1 partial / 1 untestable.** Ship-blocking.

---

## 11. IMPLEMENTATION PLAN

1. **Patch (V0.3.x, testnet only):**
   - hard-fix `mint()` default `issue_price=P0` (§3 compliance);
   - fix fee bookkeeping to direction-asset (+ unit tests for a DERO-swap and a DUSD-swap);
   - reintroduce 10/5/15 fee routing; add insurance accumulator;
   - cap `pol_dero` to share-of-collateral (kill conjuring);
   - add address-level 1095-day rolling window for lock T(u).
2. **Engine (V0.4, gated):**
   - TWAP reader (60-min, over blocks);
   - vault linearization: collateral-value = C·TWAP; liquidation below CR 1.2; insurance payout order;
   - mint halt / circuit when insurance < target or TWAP < P0·LTV.
3. **DVM-BASIC porting checklist:** divide-first uint64 math, pre-scale X/Y to 1e12 granularity, overflow unit tests, `collateral/100×72/100`-style staging, block→day conversions, no float literals.
4. **Docs:** relabel DUSD as non-peg internal unit unless a floor is added; update the 0.01→1000 headline (max ≈630); document the no-redemption-floor decision.
5. **Pre-launch:** rerun full adversarial suite (this report's scripts) after each patch; gate phase-2 behind insurance≥10% outstanding + two consecutive green runs.

---

*Report compiled from sandbox-verified runs; all numbers reproduceable from `dusd_v03_sim.py`. No production value was moved.*