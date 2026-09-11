# DUSD — DERO-native soft-peg stablecoin (V0.2.1)

DUSD is a decentralized stablecoin deployed as a single DVM-BASIC smart
contract on DERO. Collateralized by DERO, minted as contract-native tokens,
with owner-gated emergency control. Target price is a soft $1 peg expressed in
DERO terms (redeem floor `FLOOR_PRICE = $0.01` → `REDEEM_RATIO = 100`, i.e.
1 DUSD likely redeemable for 100 DERO atoms at the vault).

## Status

**V0.2.1 — simulator-verified skeleton.** Compile/testnet integration is the
remaining gate (Part B). See `DUSD-V0.2-TESTPLAN.md` and
`DUSD-V0.2-TESTLOG.md` for what is proven vs. still blocked.

## Files

| File | Purpose |
|------|---------|
| `DUSD-V0.2-CONTRACT.bas` | The DVM-BASIC smart-contract source (the only artifact that runs on DERO). |
| `DUSD-V0.2-TESTPLAN.md` | Test plan: Part A code/doc-supported claims, Part B testnet-required proofs, Part C invariant matrix, Part D deploy checklist. |
| `DUSD-V0.2-TESTLOG.md` | Simulator results log for the vault-isolation fix verification. |

## Protocol in one paragraph

Anyone can Deposit DERO into the contract (a vault keyed by `SIGNER()`), Mint
new DUSD up to the soft cap `C_i × 72/10000`, Withdraw collateral while keeping
the same ratio, or Redeem DUSD back for DERO at `100 DERO/DUSD`
(`REDEEM_RATIO`) — the retire path is escrow (not burn) and is the subject of a
testnet proof. A ceiling on total issuance (`global_ceiling = 25000000000000`
atoms ≈ $250K) binds before the solvency cap on large deposits. An
owner-only, one-way `SetEmergency()` (placeholder for an Oracle 5-of-7 quorum
in a later phase) freezes Deposit/Mint while keeping Redeem/Withdraw open.

## Key invariants (DVM-enforced)

- `Debt_i ≤ C_i × 0.0072` (per-vault MINT_RATIO)
- `TotalDebt = Σ Debt_i` and `TotalCollateral = Σ C_i`
- `Issued − Retired == TotalDebt` (bookkeeping escrow identity)
- `Emergency ⇒ Mint = 0` (Redeem/Withdraw stay open)
- Failed tx ⇒ no state change (DVM atomicity)

## History

- **V0.1** — design spec + prototype (`DUSD-V1-SPEC.md`, `DUSD-V1-PROTOTYPE.bas`).
  Superseded by V0.2 and removed from the repo; content lives in git history.
- **V0.2.1** — outlined skeleton with a two-leaf elm/hard-cap structure, global
  issuance ceiling, emergency state machine.

## Not yet proven (testnet-required)

T1 retirement irreversibility, T2 exact DUSD asset-input semantics, T3 core
DERO custody. These block final implementation; see Part B of the test plan.