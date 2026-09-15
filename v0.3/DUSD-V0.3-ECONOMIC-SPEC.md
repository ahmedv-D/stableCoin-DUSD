# DUSD V0.3 — Endogenous Price Discovery Economic Specification

**Status:** research / simulator-ready; not production code.

## 1. Goal

DUSD is designed as an internal stable unit for the DERO ecosystem. DERO price is discovered endogenously through a DERO/DUSD protocol-owned liquidity pool (POL). No external DERO/USD oracle is used by the core price-discovery mechanism.

Genesis price is **0.01 DUSD/DERO**. This is an initial issuance/reference price, **not a permanent floor**.

The system must permit reversible market movement (for example 0.01 → 1,000 → 0.01) without resetting the protocol or changing historical debt.

## 2. Core separation of concerns

1. **Vault / collateral layer**: DERO is time-locked and supports DUSD issuance.
2. **POL layer**: a mandatory contribution of DERO and DUSD builds protocol-owned liquidity.
3. **Swap layer**: DERO↔DUSD swaps provide price discovery.
4. **Lock-incentive layer**: fee share is proportional to capital committed × remaining lock time.
5. **Solvency layer**: prevents DUSD liabilities from exceeding recoverable backing during severe DERO price declines. This layer is NOT solved by the AMM alone.

## 3. Genesis issuance

Let:

- `C` = newly locked DERO
- `P_issue` = issuance price for this tranche
- `P_genesis = 0.01`
- `LTV = 0.72`

V0.3 Phase-1 issuance uses the conservative rule:

`P_issue = P_genesis`

Therefore:

`GrossMint = C × 0.01 × 0.72`

Historical debt is fixed to its issuance tranche. A later market-price increase does **not** create additional debt capacity on the same collateral.

## 4. Mandatory POL contribution

Every successful mint contributes a fixed initial share of the new DUSD to POL:

`POL_DUSD = GrossMint × q`

with the initial research parameter:

`q = 0.0025` (0.25%).

To make the contribution price-neutral at entry, the protocol contributes matching DERO:

`POL_DERO = POL_DUSD / P_spot`

where `P_spot = POL_DUSD_reserve / POL_DERO_reserve` immediately before the contribution.

The POL contribution belongs to the protocol and is not user-withdrawable before its protocol lock expires.

## 5. Endogenous DERO price

The POL uses a constant-product curve for the research model:

`X × Y = K`

where:

- `X` = DERO reserve
- `Y` = DUSD reserve

Spot price:

`P_spot = Y / X` DUSD per DERO.

A swap changes reserves according to the AMM curve and charges the configured fee (initial simulator parameter: 0.30%).

**Important:** `P_spot` is for trading/price discovery. It must not be allowed to retroactively reprice historical vault debt.

## 6. Price manipulation protection

Spot price must not be used directly for risk decisions.

The risk engine uses an internal time-weighted price derived from protocol swaps:

`P_TWAP = time-weighted average of observed POL spot prices over the configured window.`

Initial research window: 60 minutes.

This is an internal market statistic, not an external oracle feed.

## 7. Lock-duration function

Mint pressure is measured against current POL value.

At pool balance:

`V_POL = X × P_spot + Y`

Since `P_spot = Y/X`, the idealized balanced-pool identity is:

`V_POL = 2Y`.

Define:

`u = BatchMint / V_POL`

where `BatchMint` includes a rolling/cumulative window sufficient to prevent a split-transaction bypass.

Research lock curve:

`T(u) = 30 + 1065 × u^1.05977 / (u^1.05977 + 0.550857^1.05977)`

with:

`30 ≤ T ≤ 1095 days`.

This curve is a calibration candidate, not a final governance constant.

## 8. Anti-split rule

Lock duration cannot be calculated independently per transaction.

For a vault, mint-pressure accounting uses a batch/rolling window:

`BatchMint = current_mint + eligible_mints_by_same_position_within_window`

The simulator must support both:

- same-vault aggregation
- global pressure tests with multiple wallets

The implementation must prevent a user from splitting a large mint into many small transactions solely to reduce lock duration.

## 9. POL fee-share weight

For every active locked position:

`W_i = Locked_DERO_i × Remaining_Lock_Days_i`

The user's share of the fee distribution reserved for long-term POL backers is:

`Share_i = W_i / ΣW_j`

As time passes, `Remaining_Lock_Days` decreases, so fee entitlement decays naturally toward zero at expiry.

## 10. Fee routing — research default

Initial research split for a 0.30% swap fee:

- `0.10%` equivalent → POL growth / reserve strengthening
- `0.15%` → time-weighted POL backers
- `0.05%` → insurance / solvency reserve

This split is a tunable parameter and must be stress-tested before implementation.

## 11. Crucial solvency invariant

Price discovery and solvency are separate.

A DERO price crash can make a fixed historical debt larger than the market value of the vault collateral. The simulator intentionally exposes this state rather than hiding it.

Therefore the production design must enforce at least:

`Outstanding_DUSD ≤ Recoverable_Backings_Value`

under the selected internal solvency valuation and liquidation/redemption rules.

The current V0.3 research model does **not** yet claim that this invariant is solved. It is the next module to design.

## 12. No retroactive repricing

For every issuance tranche:

`Debt_tranche = Locked_DERO × P_issue × LTV`

and:

`P_issue` is immutable after issuance.

A higher market price can increase trading price and improve future issuance terms only for **newly locked collateral**, subject to the eventual solvency design.

## 13. Required failure modes

Before production implementation, simulations must include:

- Genesis bootstrap
- gradual adoption
- large mint vs shallow POL
- mint splitting attack
- whale buy
- whale sell
- price pump to 10 / 100 / 1,000 DUSD per DERO
- reversal to Genesis region
- 50%, 75%, 90%, 99% DERO drawdown
- bank-run redemption
- POL exhaustion
- fee starvation
- many long-duration lockers
- expiry cliff
- coordinated multi-wallet pressure
- integer/overflow boundary tests

## 14. Current simulator verdict

The sandbox tests currently show:

- Historical debt stays fixed when market price changes.
- A simple recursive re-mint attack fails under the fixed-issuance rule.
- AMM price is reversible in principle.
- Severe price crashes expose insolvency if no separate solvency mechanism exists.

That last result is a feature of the test suite, not a failure to report: it tells us exactly what V0.3's next economic module must solve.

## 15. Implementation order

1. Freeze this economic state machine.
2. Build and validate solvency/redemption module.
3. Add multi-agent Monte Carlo stress tests.
4. Specify POL accounting and expiry semantics.
5. Only then map the proven state machine into DVM-BASIC.

DERO DVM supports contracts that can hold/send DERO and assets and exposes primitives such as `DEROVALUE()`, `ASSETVALUE()`, `SEND_DERO_TO_ADDRESS()`, and `SEND_ASSET_TO_ADDRESS()`. Exact custody and retirement behavior still requires testnet proof before production claims. citehttps://docs.dero.io/Developers/dvm/
