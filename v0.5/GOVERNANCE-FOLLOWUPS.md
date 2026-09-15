# DUSD V0.5.1 Governance Follow-ups

Companion to `FINAL-AUDIT-REPORT.md` section 6. The five residual findings
(S3, S5b, S14, S17 WEAK; S25 MIXED) are non-blocking for TESTNET but all
carrying governance instruments that must be **explicitly owned** before any
mainnet evaluation. Each item below states: the finding, the evidence bound,
the instrument (monitor/knob/policy), an owner, and an acceptance criterion.

Status: DRAFT — instruments specified, owners TBD by the DUSD working group.

---

## 1. S3 + S17 — TWAP/spot wedge (pump-then-redeem)

**Finding.** A swap moves `spot` instantly while `P_risk` (TAWAP) lags. S3
moved coverage +0.404 before redeem (wedge x~1.4); S17 measured TWAP chasing
a 91.8% spot dump with ~14-block 50% convergence window. S5 exploited the
*unbounded* version of the same wedge (7.29x). Under V0.5.1, `mint_price=twap`
+ `redeem_dero_settle=max` bound the advantage, and S5 is closed (subsidy
0.00). The residual is a *timing* window, not a free-value channel.

**Instrument — TWAP-wedge monitor (on-chain-oracle-lite, off-chain first).**
- Periodic (per-block or per-N-block, N=4) eval of `r = spot / P_risk`.
- Alert at `r >= 1.5` (S5's wedge was 7.3x; 1.5 gives ~5 min at 18.5s blocks
  headroom before the half-life window closes).
- Two action levels:
  - *Amber* (`1.5 <= r < 3.0`): log + rate-limit `DUSD->DERO` swaps; no state
    change.
  - *Red* (`r >= 3.0`): auto-trigger `pol_drawdown_ratio` tightening from
    4.0 -> 2.0 (see item 2) for the next 72 blocks. This is the only allowed
    automatic parameter move; everything else requires governance.
- TWAP half-life parameter `alpha` must be re-derived on a live chain:
  S17's "14 block" is an average from the model; re-fit
  `alpha = 1 - exp(-ln(2)/T1/2)` against observational block data before
  mainnet (also see section 8 of the report: live-chain rechecks).

**Owner:** DUSD working group / data engineering.
**Accept:** 90 days clean amber-free operation after the monitor ships
off-chain; red-level auto-tighten fires correctly in a simulated live replay.

---

## 2. S5b — redemption-priced-at-P_risk window and the drawdown knob

**Finding.** Constructed dump+redeem ladder realized net arb
`DUSD+DERO@spot = 47305.69` on a 300k deposit. The guard in V0.5.1
(`pol_drawdown_guard=True`, ratio 4.0) bounds this; the residual is the
*window before the guard binds*.

**Instrument — pol_drawdown_ratio governance knob.**
- Ratio is a SC parameter, set to 4.0 in `V051_FIXES` and stored as a
  contract constant (`pol_drawdown_ratio`). Governance must be able to change
  it 2.0–6.0 (floored at 2.0: net POL DERO outflow can never exceed 2x
  cumulative DERO fee-in; `2.0` is the model's floor below which redemption
  DERO legs starve in the live haircut tier, see S25 §97.9% outcome).
- Change rule: simple-majority SC-gate vote (5-of-7 oracle quorum per design
  doc) with the activation delay already specified in the Oracle phase;
  no direct owner write. Amber/Red auto-tighten (item 1) may *temporarily*
  lower it below governance floor for 72 blocks, then it reverts.
- Additive protection (already in FIX set): settle-at-max means a pump
  **raises** the DERO leg price, so the arb must fight its own pool exit
  price — re-verify after the knob change.

**Owner:** tokenomics + oracle committee.
**Accept:** simulate ratio {2.0, 3.0, 4.0, 6.0} across the S5b ladder;
pick the smallest ratio that keeps S5-variant arb `< 1%` of deposited value,
then publish the chosen value with the simulation output.

---

## 3. S14 — backer fee-share weighting (late-whale capture)

**Finding.** Weight W_i = LockedDERO_i x CommittedDays_i gives a late-but-large
backer outsized fee share (73.4% of a 10.09 DUSD fee event after a 400k late
mint). This is a reward-distribution **design property** — the whale locks real
collateral — not theft. No fix proposed by this audit.

**Instrument — tokenomics policy decision (pick one, record it).**
- (a) **Status quo.** Keep W = locked x lockdays. Rationale: fee share is
  proportional to secured collateral, fair game. Cost: late whales dominate.
- (b) **Maturity floor.** Fee share requires a minimum committed lock (e.g.
  30 days) to vest; reduces whale-fresh-lock capture. Cost: complexity in the
  lock table + a new vest knob.
- (c) **Fee-share decay.** Weight decays new-lock fee weight toward
  cumulative-collateral weight over lock life. Cost: more invariants to track.

Recommend (a) for testnet, revisit for mainnet only if the policy outcome
(stable fee income for small backers) measurably fails.

**Owner:** tokenomics / community.
**Accept:** a written policy decision recorded in the governance log with a
one-line rationale; no code change required for (a).

---

## 4. S25 — total-collapse haircut (MIXED)

**Finding.** 90/95% crash + full run: mint banned (correct), claimed 4309.20 /
refunded 89.43 = 97.92% haircut, residual S = 1010.80, bad-debt 0.0000. The
claim is *honestly* pro-rata haircut — no value created, no bad debt — but a
97.9% haircut is extreme social cost.

**Instrument — residual-S policy.**
- Define *residual S* (the S units that, post basket-claim, are priced at
  CF < 1 and effectively unrecoverable at floor). Current policy implicit:
  they stay outstanding, priced by CF forever, and roll into `bad_debt` only
  at liquidation.
- Explicit options (must be adopted by governance, one for mainnet):
  - (i) **Burn at claim.** After the basket, residual S is burned against the
    remaining `y_pol_dusd`/`ins_dusd` (pro-rata to the escrowed DUSD) every
    epoch until it trends to zero. Consequence: S drops with a floor; no
    "zombie claims".
  - (ii) **Insurance top-up.** Before burning, `ins_dero` + `ins_dusd`
    convert to DUSD at P_risk to raise the claim factor, then residual burns.
    Consequence: insurance is spent to soften the haircut.
  - (iii) **Freeze.** Post-crash, S stays as-is, redemption open, mints
    banned — current sim behavior. Acceptable *only* if policy (i)/(ii) is
    delegated to a post-launch governance vote with a documented timeline.
- Recommend (ii) for testnet: insurance exists precisely to smooth tail loss,
  and it converts at P_risk without minting value (conservation preserved).

**Owner:** governance / insurance-fund steward.
**Accept:** a written residual-S policy with the chosen option (+) deposit;
re-run S25 under the chosen policy and confirm bad-debt stays 0 and the
haircut < 97.92% (lower threshold) or is explicitly accepted with the
pro-rata justification on record.

---

## 5. Registry of decisions (populate on adoption)

| # | Instrument | Decision (proposed) | Owner | Adopted? |
|---|-----------|---------------------|-------|----------|
| 1 | TWAP-wedge monitor | Off-chain, alert @ r>=1.5, red auto-tighten ratio 2.0 for 72 blk | data eng | ▢ |
| 2 | pol_drawdown_ratio | knob 2.0–6.0, 5-of-7 oracle, floor 2.0 | oracle ctee | ▢ |
| 3 | S14 fee weighting | (a) status quo for testnet | tokenomics | ▢ |
| 4 | S25 residual S | (ii) insurance top-up then burn | stewards | ▢ |
| 5 | TWAP alpha re-fit | live-chain T1/2 before mainnet | data eng | ▢ |

---

*Governance gate: items 1, 2, 4 are required before mainnet valuation (report
section 6 / section 11). Items 3 and 5 are testnet-actionable.*

*All instruments are off-chain monitors + on-chain knobs that the sim and the
DVM-BASIC skeleton (`DUSD-V0.5.1-CONTRACT.bas`) already support: drawdown
ratio and amm_cap are contract constants; snapshots `v_pol_in`/`v_pol_out`
exist for the wedge monitor's read side.*