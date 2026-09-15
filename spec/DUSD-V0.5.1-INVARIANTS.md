# DUSD V0.5.1 — Invariant Catalogue (spec-authoritative)

Every invariant here corresponds 1:1 to a mechanism **already present in the
reference engine** (`core/dusd_v051_state.py`, `ProtocolState`) and to a test in
`tests/` (baseline `test_dusd_v051.py`, adversarial `test_adversarial_v051.py`,
and the crash/fuzz/property harnesses). Status is one of:

- `SPEC-AND-ENGINE` — reflected verbatim in the state-machine spec and enforced
  by the engine.
- `SPEC-ONLY` — in the spec; not yet provable in this prototype (needs DVM/testnet).
- `ENGINE-ONLY` — enforced by the reference model; not yet written into the spec text.

## Native-unit accounting

**I1 — DERO provenance (POL excluded).**
DERO that originates from POL must not immediately become fresh mint
collateral. Enforcement is provenance-based exclusion, not a cooldown.
Engine: `provenance_excluded_dero` + `eligible_collateral()` subtracts it.
Status: SPEC-AND-ENGINE.

**I2 — Fee buckets stay in native units.**
A DERO fee accumulates as DERO; a DUSD fee accumulates as DUSD. No fee is ever
converted into the other denomination and double-counted.
Engine: fee buckets `fee_pol_growth_dero/dusd`, `fee_backer_pool_dero/dusd`,
`fee_insurance_dero/dusd`; 70/10/15/5 split applied at source in native units.
Status: SPEC-AND-ENGINE.

**I3 — Conservation of DERO (no hidden creation).**
The sum `POL DERO + Insurance DERO + eligible vault collateral + provenance-
excluded DERO` never exceeds the real deposited DERO plus any genuine
external inflow. No DERO is created via accounting.
Engine: `total_deposited_dero`, `eligible_collateral()`, `pol.dero`,
`insurance.dero`, `provenance_excluded_dero`; `test_A` (40-op recursion) and
fuzz sequence conservation.
Status: SPEC-AND-ENGINE.

**I4 — Conservation of DUSD (no hidden mint).**
`OutstandingDUSD <= POL_DUSD + Insurance_DUSD + eligible collateral NAV share`
that the protocol controls. When backing is insufficient the shortfall is an
**explicit claim-factor haircut**, never a hidden token creation.
Engine: `claim_factor()`; `test_M_N`, `test_V` (13,455 reachable
claim_factor<1 states in the adversarial grid; `unpaid_value>0` provably
unreachable — distress surfaced only via the haircut).
Status: SPEC-AND-ENGINE (NOT-PROVEN as a *free of hidden creation* claim until
DVM port uses uint64 atom accounting).

**I5 — Pari-passu senior claim.**
1 DUSD is a similar senior claim on `POL + Insurance + eligible vault
collateral`. All DUSD holders share the same `claim_factor` for the same state;
no first-mover advantage.
Engine: `test_L` (5 robbers, same factor), `test_M`.
Status: SPEC-AND-ENGINE.

## Mint / ceiling

**I6 — Global ceiling.**
`OutstandingDUSD <= GLOBAL_CEILING (= 250_000.0)`. Any mint that would push past
the ceiling fails atomically.
Engine: `GLOBAL_CEILING`, `test_S`, `test_C` (uint64 + ceiling gating).
Status: SPEC-AND-ENGINE.

**I7 — Mint price = min(P0, TWAP), anti-subsidy.**
Issue price uses the smaller of the genesis reference (P0) and the internal
TWAP, so a pump cannot mint at a subsidized high price.
Engine: `test_G`, `test_G_dero`, `mint_price="min"`.
Status: SPEC-AND-ENGINE.

**I8 — Vault debt does not reprice with market movement.**
Existing vault debt stays fixed when DERO/DUSD spot changes; no silent
revaluation attack.
Engine: `test_Q`, `test_H`.
Status: SPEC-AND-ENGINE.

## Liquidation / coverage

**I9 — Collateral liquidity haircut H = 0.90.**
Eligible collateral is valued with a 10% haircut in NAV accounting.
Status: SPEC-AND-ENGINE (`H`, `eligible_collateral`).

**I10 — Minimum coverage gate.**
A mint that would drop `Coverage` below `MIN_COVERAGE = 1.00` is rejected.
Status: SPEC-AND-ENGINE (`test_M`, `test_T`).

**I11 — Explicit haircut on under-collateralization.**
When backing < outstanding, redemption returns `q * claim_factor` with
`claim_factor < 1`, and the unpaid portion is **explicit** protocol loss, not
hidden mint. Engine surfaces it via `claim_factor` + `coverage_after`.
Status: SPEC-AND-ENGINE (distress reachable; unpaid accumulator unreachable by
design — see adversarial resolution, item V).

## Atomicity / integer safety

**I12 — Atomic transactions.**
A failed operation returns zero state mutation: no partial balance, no partial
debt, no half-applied fee split.
Engine: `test_W`, `test_failed_mint_is_atomic`.
Status: SPEC-AND-ENGINE.

**I13 — uint64 (uint64 METs) safety.**
No accounting value may exceed the DERO DVM uint64 bound; multiplication that
would overflow must fail, not wrap. Reference model enforces `U64_MAX_ATOMS`
(= 2**64-1 atoms) and `MAX_DERO` (~1.8446744e14 DERO) in native units.
Engine: `_check_uint64_bound`, `MAX_DERO`, `test_R` (1e200/float("inf")/1.5*
MAX inputs rejected).
Status: SPEC-AND-ENGINE (int-bound checks are simulator-side; DVM token-bound
validation is a porting test item).

## Dynamic lock / POL

**I14 — Dynamic lock curve, no fixed tiers.**
`lock_days(u)` is continuous, monotone non-decreasing, bounded
`[MIN_LOCK_DAYS, MAX_LOCK_DAYS] = [30, 1095]` and asymptotically approaches
the cap (never exactly a fixed tier + fixed tier cap at finite u).
Status: SPEC-AND-ENGINE (`test_lock_curve`, `test_lock_curve_continuous`).

**I15 — No fixed 100 DERO/DUSD redemption.**
Redemption is dynamic (`claim_factor`), not a hard `1 DUSD = 100 DERO` promise.
Status: SPEC-AND-ENGINE; the V0.2 fixed 100 DERO/DUSD redemption is
**obsolete economics**, removed from the live model (Phase 8).

## Scope marker

No invariant in this catalogue is a claim of *mainnet security*. Each is an
economic invariant of the deterministic reference state machine, exercised by
tests and (for 1–5, 8, 12–14) additionally by 100k-sequence fuzzing and
Monte-Carlo scenarios. Anything gated on DVM-BASIC integer/storage/custody
behavior is `SPEC-ONLY` / `NEEDS-TESTNET` until the DVM port runs on a DERO
testnet.
