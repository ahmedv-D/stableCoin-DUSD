# DUSD V0.4 — Economic Specification (Research / Testnet Only)

## 0. Design goal

DUSD V0.4 is an endogenous DERO/DUSD market + collateral system.

- `P0 = 0.01 DUSD/DERO` is the Genesis issuance price only.
- No external DERO/USD oracle is used for core price discovery.
- The DERO/DUSD POL pool discovers the internal DERO price.
- Existing vault debt does not reprice when market price changes.
- A portion of each mint funds POL using the user's real deposited collateral.
- Lock duration rises with cumulative mint pressure relative to POL depth.
- POL fee share is based on committed `LockedDERO * CommittedDays`, not decaying remaining days.
- Fee claims accrue only while the position is active; expired positions keep already-earned fees, but do not receive future fees.

## 1. Mint

Genesis price:

    P0 = 0.01 DUSD / DERO

Phase-1 issuance price:

    P_issue = P0

For collateral C:

    gross_mint = C * P_issue * LTV

POL DUSD contribution:

    pol_dusd = gross_mint * Q_POL_DUSD

with Q_POL_DUSD = 0.0025 (0.25%).

The DERO contribution is price-neutral at the current POL spot:

    pol_dero = pol_dusd / P_spot

The key accounting rule is:

    vault_collateral_after = C - pol_dero

    total DERO accounted = deposited DERO

No DERO is created by the POL contribution.

User receives:

    user_dusd = gross_mint - pol_dusd

The protocol may additionally charge a separate DUSD mint fee later; it is not part of collateral accounting.

## 2. POL

Pool reserves:

    X = DERO
    Y = DUSD

Spot:

    P_spot = Y / X

Constant product:

    K = X * Y

Swap fee:

    f = 0.003

The simulator treats fees as retained in the input asset's reserve and also tracks them by asset, preserving units.

POL is liquidity + price discovery, not the whole solvency reserve.

## 3. Lock duration

Let:

    POL_value_DUSD = X * P_spot + Y = 2Y

For a new mint batch M_batch:

    u = M_batch / POL_value_DUSD

Candidate curve:

    T(u) = 30 + 1065 * u^a / (u^a + b^a)

where:

    a = 1.05977
    b = 0.550857

Clamp:

    30 <= T <= 1095 days

Anti-split rule:

- aggregate mint pressure by signer/vault over a rolling 1095-day window;
- compute lock from the cumulative gross mint that is active in that window, not each transaction independently;
- splitting one mint must not materially reduce the resulting commitment.

The simulator enforces same-signer rolling aggregation.

## 4. POL fee weight

For position i:

    W_i = LockedDERO_i * CommittedLockDays_i

This weight stays constant during the committed lock.

At expiry:

    W_i -> 0

Accrued fees earned before expiry remain claimable.

A newly created position only receives fees generated after its creation time. This eliminates the expiry-cliff "fee sniper" that existed in the decaying-weight model.

## 5. Fee routing

Target routing:

    10% -> POL growth / reserve
    15% -> active backers
     5% -> insurance reserve
    70% -> remains in the pool as swap liquidity / market depth

These numbers are research parameters, not final governance values.

Important accounting rule:

- DERO swap fees stay denominated in DERO.
- DUSD swap fees stay denominated in DUSD.
- No unit conversion may be silently treated as the other asset.

## 6. Price use

Spot is used for swaps.

For risk actions, use an internal TWAP over time / blocks.

The Genesis issuance price P0 is used for Phase-1 minting.

An old vault's debt is never increased because spot/TWAP increased.

## 7. Solvency

For vault i:

    collateral_value = collateral_DERO * risk_price

    CR = collateral_value / debt

Recommended phase-1 risk gate:

    liquidation_threshold = 1.20

A vault can be liquidated when TWAP-derived CR <= 1.20.

Insurance is a separate reserve.

IMPORTANT LIMIT:
A collateral-only design cannot mathematically guarantee $1 redemption for DUSD through an arbitrary 90%-99% DERO crash with no external recapitalization. V0.4 therefore targets:
- bounded issuance,
- no recursive debt creation,
- orderly liquidation,
- insurance-backed absorption of bounded losses,
- protocol survival,
rather than an unconditional fiat redemption promise.

## 8. POL provenance / recursive-deposit defense

DERO acquired from the POL cannot immediately become fresh collateral.

Research rule:

    POL-origin DERO must satisfy a provenance cooldown
    before it can enter a minting vault.

Default research cooldown:

    1095 days

This is intentionally conservative for the research build.

## 9. Global ceiling

Keep an absolute ceiling:

    250,000 DUSD

But Phase-1 practical issuance may be much lower because issuance is limited by actual DERO deposits at P0.

Do not use the ceiling as evidence that 250K is currently reachable.

## 10. Implementation invariants

1. Issued DUSD never exceeds the global ceiling.
2. Every minted DUSD corresponds to a real DERO deposit.
3. POL DERO is a share of deposited DERO, never newly created DERO.
4. Existing debt never increases because market price rises.
5. Same-user mint splitting cannot materially shorten the lock.
6. Fees never exceed actual fees collected and preserve units.
7. Expired positions receive no future fees.
8. Previously accrued fees remain claimable.
9. POL-origin DERO cannot immediately recurse into new collateral.
10. Withdrawals cannot unlock more DERO than the vault owns.
11. AMM swaps preserve constant-product accounting subject to fees.
12. The protocol never promises more liquid DERO than it actually controls.
