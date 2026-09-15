# DUSD V0.5.1 — DVM-BASIC Porting Skeleton

This is intentionally a porting map, not a claim that it compiles on DERO DVM-BASIC. Exact syntax and asset/custody semantics must be validated against the target DERO toolchain and testnet.

## Storage groups

### Protocol

- `outstanding_dusd`
- `global_ceiling`
- `min_coverage`
- `liq_cr`
- `now`
- `emergency_state`

### POL

- `pol_dero`
- `pol_dusd`
- fee buckets in native units
- AMM invariant / reserve tracking

### Insurance

- `insurance_dero`
- `insurance_dusd`

### Vault(owner)

- `collateral`
- `debt`
- `locked_until`
- `committed_lock_days`
- provenance flags / excluded collateral

### Price/TWAP

- bounded rolling observation ring
- block/time metadata
- internal TWAP computation

### Rolling mint pressure

- 1095-day rolling buckets
- global pressure sum

## Entry points

Recommended economic entry points:

- `DepositAndMint()`
- `SwapDUSD()`
- `SwapDERO()`
- `Redeem()`
- `ClaimFees()`
- `ExpireLock()` / state transition helper
- emergency/governance functions after quorum design is complete

## Mandatory pre-commit checks

1. exact asset-input amount
2. signer/owner isolation
3. uint64 safe multiplication/division
4. global ceiling
5. minimum coverage after proposed mint
6. provenance exclusion
7. same-state pari-passu claim factor
8. payout bounded by controlled assets
9. no hidden token creation
10. atomic failure behavior

## Integer implementation

Never implement `x*y/z` blindly with uint64-sized values. Use one or more of:

- staged reduction by gcd when possible
- quotient/remainder decomposition
- explicit maximum input checks
- scale factors with bounded intermediate products

A simulator using Python integers is not evidence that DVM uint64 arithmetic is safe.

## DERO testnet gates

The DVM port is not accepted until testnet demonstrates:

- DERO sent to the contract actually appears under the intended custody model
- exact token/asset input semantics
- DUSD creation and retirement semantics
- no signer cross-vault mutation
- no state persistence surprises
- no arithmetic overflow at identified edges
- failed calls leave all economic state unchanged
