# DUSD V0.5.1 — State Machine Specification

**Status:** Testnet implementation baseline. Not mainnet approval.

## Purpose

Replace the current V0.2.1 vault-only implementation with a V0.5.1 economic state machine while preserving DERO-native accounting and explicit invariants.

The current GitHub repository identifies itself as V0.2.1 and still defines fixed redemption at 100 DERO/DUSD, per-vault collateral ratios, and an owner-gated emergency placeholder. V0.5.1 replaces those economic primitives; the existing V0.2.1 contract should be treated as migration history, not as the final economic engine.

## Core invariant

**1 DUSD = a pari-passu senior claim on the unified backing base:**

`POL + Insurance + Eligible Vault Collateral`

Redemption is dynamic. There is no permanent `100 DERO = 1 DUSD` redemption promise.

## State

The protocol state contains:

- `Vault[owner]`: eligible DERO collateral, debt, lock expiry, committed lock days.
- `POL`: DERO reserve `X` and DUSD reserve `Y`.
- `Insurance`: DERO + DUSD balances.
- `OutstandingDUSD`: externally claimable DUSD.
- `PriceHistory`: block/time anchored spot samples used for internal TWAP.
- `RollingMintPressure`: 1095-day rolling mint history.
- `ProvenanceExcludedDERO`: DERO that originated from POL and is not eligible to become fresh collateral.
- Fee accounting buckets by native unit.

## Pricing

`P_spot = POL_DUSD / POL_DERO`

`P_risk = internal block-anchored TWAP`

No external DERO/USD oracle is required for the core price discovery engine.

Genesis reference:

`P0 = 0.01 DUSD / DERO`

`P0` is an issuance reference, not a lifetime floor.

## Mint

For a DERO deposit `C`:

`gross = C * P0 * 0.72`

`POL_DUSD = gross * 0.0025`

`POL_DERO = POL_DUSD / P_spot`

`user_DUSD = gross - POL_DUSD`

The POL DERO contribution must be sourced from the real deposit. No DERO may be created by accounting.

Existing vault debt does **not** reprice because spot/TWAP increases.

Before state mutation is committed, the proposed state must satisfy:

`Coverage >= MIN_COVERAGE`

with default research value `MIN_COVERAGE = 1.00`.

## Unified backing NAV

Let `C` be eligible vault collateral and `H = 0.90` the research collateral liquidity haircut:

`POL_NAV = Y + P_risk * X`

`Insurance_NAV = I_DUSD + P_risk * I_DERO`

`Collateral_NAV = H * P_risk * C`

`Backing_NAV = POL_NAV + Insurance_NAV + Collateral_NAV`

`Coverage = Backing_NAV / OutstandingDUSD`

`ClaimFactor = min(1, Coverage)`

## Redemption

For redemption amount `q`:

`ClaimValue = q * ClaimFactor`

All DUSD is pari-passu. No redemption may receive priority merely by timing.

Settlement order:

1. POL DUSD
2. Insurance DUSD
3. POL DERO at `P_risk`
4. Insurance DERO at `P_risk`
5. Eligible vault collateral through the liquidation/settlement engine, applying the configured haircut.

The protocol must never transfer an asset it does not control.

Outstanding DUSD is reduced by the redeemed amount.

Any shortfall is explicit system loss/haircut. It is not repaired by hidden minting.

## POL / AMM

Constant-product baseline:

`K = X * Y`

Swap fee: `0.30%`.

Fee split target:

- 70% pool depth
- 10% POL growth
- 15% active backers
- 5% insurance

Fee buckets must remain in native units: DERO fees stay DERO; DUSD fees stay DUSD.

The production implementation must use overflow-safe arithmetic suitable for DERO DVM integer limits.

## Lock function

Let:

`u = cumulative mint pressure / POL value`

Then:

`T(u) = 30 + 1065*u^1.05977 / (u^1.05977 + 0.550857^1.05977)`

bounded to `[30, 1095]` days.

The anti-split rule is global/rolling, not limited to a single transaction or address.
The engine additionally books a **per-owner 1095-day rolling split ledger**
(`owner_rolling_splits`, booked at every mint) so rotating across addresses
cannot reset the rolling window; the lock stays global-pressure-driven.

## POL fee weight

`Weight_i = LockedDERO_i * CommittedLockDays_i`

The committed lock length, not a later remaining-time snapshot, determines the weight. The weight becomes inactive at expiry. Previously accrued fees remain claimable.

## Provenance rule

DERO originating from POL must not immediately become fresh mint collateral. Preferred enforcement is provenance-based exclusion, not merely a cooldown. A cooldown alone only slows recursive extraction and is insufficient as the fundamental invariant.

## Emergency / governance

The current V0.2.1 owner-only one-way emergency control is not the final governance model. Production governance requires a reviewed quorum mechanism and explicitly defined authority boundaries.

## Atomicity

Every state-changing operation must either:

- complete all invariant checks and commit atomically, or
- fail with zero state mutation.

## Required invariants for tests

1. `OutstandingDUSD <= GLOBAL_CEILING`.
2. No negative asset inventory.
3. No debt repricing from external price movement.
4. Deposited DERO = vault DERO + POL-origin contribution + explicitly accounted released/settled flows.
5. Redemption payout value never exceeds the computed claim value.
6. Redemption payout never exceeds controlled assets.
7. Under-collateralization produces an explicit haircut, never silent token creation.
8. Fee splits sum to the fee amount and remain in native units.
9. POL-origin DERO is excluded from fresh collateral eligibility according to provenance state.
10. Failed operations are atomic.
11. TWAP is internally derived from recorded block/time price observations.
12. Every DUSD holder receives the same claim factor for the same state.

## Scope boundaries

This document is an economic/state-machine baseline. It is **not** evidence that DVM-BASIC storage, asset-send semantics, integer bounds, or testnet custody behavior have already been proven. Those require implementation-specific DERO testnet validation before deployment.
