# DUSD V0.5.1 — Parameter Catalogue

Single source of truth for the constants used by the reference engine
(`core/dusd_v051_state.py`). Values are copied verbatim from the engine; do not
"tune" them here.

| Parameter | Value | Meaning | Source (engine) |
|-----------|-------|---------|------------------|
| `P0` | `0.01` | Genesis issuance reference DUSD/DERO price | `P0 = 0.01` |
| `LTV` | `0.72` | Mint leverage (gross DUSD per DERO × P0) | `LTV = 0.72` |
| `POL_MINT_SHARE` | `0.0025` | POL share of gross DUSD mint | `POL_MINT_SHARE = 0.0025` |
| `POL_MINT_GROWTH` | `0.10` | POL growth attribution (10% of fee) | fee-split 70/10/15/5 |
| `H` | `0.90` | Eligible-collateral liquidity haircut | `H = 0.90` |
| `MIN_COVERAGE` | `1.00` | Minimum coverage gate for mint/redemption | `MIN_COVERAGE = 1.00` |
| `LIQ_CR` | `1.20` | Liquidation collateral-ratio threshold | `LIQ_CR = 1.20` |
| `GLOBAL_CEILING` | `250_000.0` | Outstanding-DUSD hard ceiling | `GLOBAL_CEILING = 250000.0` |
| `SWAP_FEE` | `0.003` | AMM swap fee (0.30%) | `SWAP_FEE = 0.003` |
| `TWAP_WINDOW` | `30` | Internal block-anchored TWAP horizon (days) | `TWAP_WINDOW = 30` |
| `MIN_LOCK_DAYS` | `30` | Lock curve floor (days) | `MIN_LOCK_DAYS = 30` |
| `MAX_LOCK_DAYS` | `1095` | Lock curve cap (days) | `MAX_LOCK_DAYS = 1095` |
| `U64_MAX_ATOMS` | `2**64 - 1` | DVM uint64 MET bound (atoms) | `U64_MAX_ATOMS = float(2**64-1)` |
| `ATOMS_PER_DERO` | `100_000.0` | atomic unit conversion (DERO MET = 1e5 atoms) | `ATOMS_PER_DERO = 100_000.0` |
| `MAX_DERO` | `~1.8446744e14` | DERO ceiling implied by uint64-atom bound | `MAX_DERO = U64_MAX_ATOMS / ATOMS_PER_DERO` |

## Lock curve parameters

`lock_days(u)` = `30 + 1065 * u^a / (u^a + b^a)` with:

`a = 1.05977`, `b = 0.550857` (vault pressure `u` = cumulative mint / POL value);

bounded `[30, 1095]`. Values mirror the engine's `lock_days` and the frozen
V0.5 research curve.

## Fee split (native units)

Per AMM swap fee `F`:

- 70% → pool depth (POL DERO/DUSD)
- 10% → POL growth
- 15% → active backers
-  5% → insurance

Each bucket stays in its native denomination; the split is applied at source.
Engine: `fee_pol_growth_*`, `fee_backer_pool_*`, `fee_insurance_*`.

**Final booking semantics (matches the committed engine, lines 319–323 /
343–347):** the fee is booked physically as **POL `+ fee * 0.95`** (embedding
70% depth + 10% growth + 15% backers) **and insurance `+ fee * 0.05`**; the
three tallies (`0.10` growth / `0.15` backers / `0.05` insurance) are
attribution-only and add **zero extra atoms**. Conservation gain = exactly the
gross fee. (Fix `0.80 → 0.95` committed in dade2274; see
`docs/V0.5.1-FEE-ACCOUNTING-FIX.md`.)

## Scope boundary

These are research parameters of the deterministic reference model. They are
**not** governance-approved mainnet parameters; production values (esp. LTV,
H, ceiling, lock curve) remain a governance decision and are only testable on
a DERO testnet.
