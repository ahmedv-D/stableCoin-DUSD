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

## Protocol in one paragraph

Anyone can Deposit DERO into the contract (a vault keyed by `SIGNER()`), Mint
new DUSD up to the per-vault and global caps, Withdraw collateral while keeping
the ratio, or Redeem DUSD back against the unified claim — POL DUSD → insurance
DUSD → POL DERO → insurance DERO → eligible collateral, valued at internal TWAP
(`P0 = $0.01` in DERO). Risk decisions use internal TWAP, not spot; insurance
absorbs bounded losses before explicit bad-debt recognition.

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