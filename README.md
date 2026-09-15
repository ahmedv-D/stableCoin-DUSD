# DUSD — DERO-native soft-peg stablecoin

DUSD is a decentralized stablecoin deployed as a single DVM-BASIC smart
contract on DERO. Collateralized by DERO, minted as contract-native tokens.
Each version is distributed with its economic spec, the simulation engine, the
adversarial attack suites used to audit it, and the resulting audit report.

All versions to date are **research/simulator artifacts** — none has been
deployed to mainnet. The verdict on the latest is `V0.5.1: READY FOR TESTNET`,
pending governance decisions on the residual WEAK instruments.

## Version guide

| Version | Scope | State | Latest artifact |
|---------|-------|-------|-----------------|
| V0.2.1 | Single DVM-BASIC vault skeleton, per-vault MINT_RATIO, global ceiling, emergency state machine | Simulator-verified skeleton | `DUSD-V0.2-TESTLOG.md` |
| V0.3 | Second-simulation economics (POL-origin DERO, mint gating) + adversarial pass | Audit complete | `v0.3/DUSD-V0.3-ADVERSARIAL-REPORT.md` |
| V0.4 / V0.4.1 | Unified-claim economics, liquidity/insurance splits, recursion fix | Audit complete | `v0.4/DUSD-V0.4-ADVERSARIAL-REPORT.md`, `v0.4/DUSD-V0.4.1-CHANGESET.md` |
| V0.5 / V0.5.1 | Unified-claim dynamic redemption used by this audit as the final model | Audit + 100k fuzz + 50k MC closed | `v0.5/FINAL-AUDIT-REPORT.md` |

## V0.5.1 headline results

- **S5** redemption TWAP/spot pump wedge: subsidy **2706.92 DERO → 0.00**.
- **S9/S10** POL-origin recursion drain: **−46.5% → −0.2%** (no drain).
- **S16** uint64 AMM overflow: closed by a per-swap atom cap (~1845 DERO) that
  keeps `X*eff <= U64MAX` with margin.
- **Fuzz:** 100,000 random-op runs, **0 conservation violations** (shipped and
  V0.5.1).
- **Monte Carlo (50k runs, heavy-tail shocks + full bank-run):** shipped
  coverage floor **0.019** vs V0.5.1 **1.00**; redemption real-value delivery
  **0.369** → **0.625** per claimed DUSD.

The V0.5.1 fix set: `mint_price="twap"`, `redeem_dero_settle="max"`,
`amm_cap` (atom cap), `pol_drawdown_guard` (+ ratio), plus a fuzz-found
ledger-identity repair in `liquidate` and unclaimed-backer-fee rerouting to
insurance. Details in `v0.5/FINAL-AUDIT-REPORT.md` section 7.

## V0.5 economics (dynamic redemption + unified backing claim)

Authoritative spec: `v0.5/DUSD-V0.5-ECONOMIC-DESIGN.md`. The V0.5 core decision
replaces the V0.2 fixed `100 DERO/DUSD` retire path with a **dynamic, basket
redemption**, and redefines the token:

> **1 DUSD is a pari-passu senior claim on the unified backing base** = POL +
> insurance + eligible vault collateral.

### Issuance (Genesis / Phase-1)
- `P0 = 0.01 DUSD/DERO` — Genesis reference only, **not** a permanent floor.
- `gross_mint = C * P0 * LTV`, `LTV = 0.72`.
- `POL_DUSD = gross_mint * 0.0025`; `POL_DERO = POL_DUSD / P_spot`; the POL
  DERO is a real share of the user's deposit (`vault_after = C - POL_DERO`).
  No DERO is created; existing debt never reprices.

### Endogenous price (no external oracle)
- `X = POL DERO`, `Y = POL DUSD`, `P_spot = Y / X`.
- `P_risk` = internal, block-anchored TWAP (BLOCK_S = 18.5 s, alpha 0.05).
- Risk decisions use `P_risk`, never spot.

### Unified backing NAV & claim factor
- `Backing_NAV = (Y + P_risk*X) + (I_DUSD + P_risk*I_DERO) + (H*P_risk*C)`
  with collateral liquidity haircut `H = 0.90`.
- `Coverage = Backing_NAV / S`; `ClaimFactor = min(1, Coverage)`.
- `RedeemValue = q * ClaimFactor`. If Coverage ≥ 1 the claim is fully backed;
  if Coverage < 1 **every** DUSD receives the same proportional haircut.
  No first-mover advantage (a hard invariant, verified by S6/S24).

### Dynamic redemption basket (settlement order)
1. liquid DUSD (POL + insurance)
2. liquid DERO (POL + insurance), valued at `P_risk`
3. mobilized/liquidated eligible collateral as permitted by the risk engine

Never transfer more assets than controlled; burned DUSD are reduced before
final accounting; residual under-collateralization is **explicit system loss**
(bad debt recorded), never hidden token creation.

### Solvency gate, liquidation, fees, locks
- `MIN_COVERAGE = 1.00` — new mints rejected if proposed backing would breach
  it (production candidates: 1.10–1.20).
- `LIQ_CR = 1.20`, insurance absorbs bounded losses before bad-debt
  recognition.
- Swap fee split (native units only), 70% pool depth / 10% POL growth / 15%
  active backers / 5% insurance.
- POL-origin DERO cannot directly become fresh mint collateral
  (provenance-based exclusion; cooldown alone is only rate limiting).
- Backer weight `W = LockedDERO * CommittedLockDays`, lock curve
  `T(u) = 30 + 1065*u^1.05977 / (u^1.05977 + 0.550857^1.05977)`,
  bounded 30–1095 days.

### Fundamental limitation (explicit)
Dynamic redemption guarantees pari-passu claims, no payout above controlled
assets, explicit haircut when undercollateralized, and no hidden DERO/DUSD
creation. It **cannot** guarantee an external $1 fiat redemption through an
arbitrary 90–99% DERO collapse without an independent reserve/capital source.

### V0.5 acceptance target (met)
Genesis, POL accounting, recursion, Sybil split, fee accounting, pump/dump,
25–99% crashes, 100% redemption attempt, insurance depletion, POL depletion,
TWAP manipulation, **100k fuzz**, **50k heavy-tail Monte Carlo** — all
exercised in `v0.5/attack/` and closed per `v0.5/FINAL-AUDIT-REPORT.md`.

## Repository layout

```
v0.2/ (repo root)   V0.2.1 contract + test plan/log
v0.3/               economic spec, sim engine, adversarial report
v0.4/               V0.4 + V0.4.1 spec/sims/tests/attack suite/reports/MC
v0.5/               economic design, final audit report, attack engine + suites,
                    fuzz/MC harness, saved run logs and results
```

## History

- **V0.1** — design spec + prototype (`DUSD-V1-SPEC.md`, `DUSD-V1-PROTOTYPE.bas`).
  Superseded by V0.2 and removed from the repo; content lives in git history.
- **V0.2.1** — outlined skeleton with a two-leaf elm/hard-cap structure, global
  issuance ceiling, emergency state machine.
- **V0.3** — economics iteration + first adversarial pass; mint gating on POL
  coverage introduced.
- **V0.4 / V0.4.1** — unified-claim economics, native-unit fee splits
  (70/10/15/5), insurance bucket, V0.4.1 recursion fix from a fuzz failure.
- **V0.5 / V0.5.1** — final unified-claim model, full S1–S25 + I1–I20 suite,
  100k fuzz, 50k Monte Carlo, V0.5.1 closure of all three FAIL findings.

## Not yet proven (testnet-required)

Part B of the V0.2 test plan still gates *any* deployment: T1 retirement
irreversibility, T2 exact DUSD asset-input semantics, T3 core DERO custody,
plus the DVM-level `amm_cap` implementation path for V0.5.1.