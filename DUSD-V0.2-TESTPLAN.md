# DUSD V0.2.1 — Testnet Compile / Simulator Test Plan

Target contract: `DUSD-V0.2-CONTRACT.bas`
Big-model constants (atoms): `MINT_RATIO = 72/10000` → `x72/10000`; `FLOOR_PRICE=$0.01` → `REDEEM_RATIO=100` (1 DUSD=100 DERO); ceiling 25000000000000.

---

## PART A — CODE / DOCUMENTATION SUPPORTED (assert in code + docs; no testnet dependency — NOT "proven" until testnet integration run)

| # | Claim | Evidence |
|---|-------|----------|
| A1 | `ASSETVALUE(SCID())` reads self-token sent to Core | official `token.bas` `ConvertTOKENX` (docs-supported, not testnet-verified for DUSD deployment) |
| A2 | `SEND_DERO_TO_ADDRESS` on successful redeem | same pattern |
| A3 | `SEND_ASSET_TO_ADDRESS(SIGNER(), amt, SCID())` issues DUSD = delta debt | token.bas InitializePrivate; issuance-in-tee semantics still testnet-tied (T2) |
| A4 | LOAD of missing key guarded by EXISTS/STORE-0 (fresh vault starts at col=0, debt=0) — verified fix | exists-guard blocks in Deposit/Mint/Withdraw/Redeem/SnapshotVault |
| A5 | Single-transaction atomicity (failed tx → no state change) | DVM consensus; I8 falls out |

## PART B — TESTNET REQUIRED (block final implementation)

- **T1 DUSD retirement irreversibility**
  - Claim: DUSD entering Redeem() is moved into the contract's own encrypted balance (topic escrow); a `retired_dusd` counter records it. No release path exists — proof = absence of a retract function + absence of `UPDATE_SC_CODE` + testnet loop that attempts every SCID transfer/permission path to pull escrowed DUSD back out → expect all to fail.
  - **Precise semantics (v0.2.1):** retirement ≠ burn. Bookkeeping identity `Issued − Retired == Outstanding(TotalDebt)` is DVM-enforced; *physical destruction* of the DUSD is NOT — that is what T1 proves on testnet.
- **T2 Exact DUSD asset-input semantics**
  - Claim: `ASSETVALUE(SCID())` inside Redeem unambiguously equals the DUSD the redeemer sent.
  - Test: same testnet Send With `scid=DUSD` → Core → call Redeem. Confirm amount=0 when a different asset is attached, confirm amount>0 only for DUSD.
  - Alternative design (future): DUSD as a *separate* SCID; this changes `ASSETVALUE()` lookup — prove which SCID the explorer + wallet bind before building non-owner redemption.
- **T3 Core DERO custody**
  - Claim: `TotalCollateral <= ActualRecoverableDERO` always, and no entrypoint other than Withdraw/Redeem can extract vault-collateral DERO.
  - Test: after scripted deposits, compare daemon `get_balance` (Core) vs `total_collateral` restored var; must be `>=`. Then try hostile SCID-transfers / reentrancy-ish own-contract calls → all rejected.

## PART C — INVARIANTS → CODE → SIMULATOR SCRIPT

| # | Invariant | Enforced by | Simulator scenario |
|---|-----------|-------------|--------------------|
| I1 | `Debt_i ≤ C_i × 0.0072` (MINT_RATIO) | Mint L167-172 · Withdraw L220-223 · Redeem L273-276 | Vault A deposit 100 DEROS → Mint 0.73 DUSD → **REJECT**; Mint 0.72 → OK, then Withdraw beyond required collateral → **REJECT** |
| I2 | `TotalDebt = Σ Debt_i` | atomic aggregate+per-vault write pair | two vaults mint; `CheckGlobal` → `v_total_debt` == Σ `SnapshotVault(address).v_debt` (off-chain sum) |
| I3 | `TotalCollateral = Σ C_i` | atomic aggregate+per-vault write pair | same via `SnapshotState().v_total_collateral` |
| I4 | `TotalDebt ≤ TotalCollateral×0.0072` | Mint L179-184 | Vault A 100 DEROS, Vault B 100 DEROS; A mints 0.72, B mints 0.72 → **OK**; A mints 0.73 → REJECT (global binds at 1.44) |
| I5 | `TotalCollateral ≤ ActualRecoverableDERO` — **EXTERNAL custody invariant, NOT DVM-enforced** | Testnet custody proof (T3); contract cannot read its own external DERO balance | Part B scenario |
| I6 | `OutstandingDUSD = TotalDebt = IssuedDUSD − RetiredDUSD` | issuing only at Mint (+`issued_dusd`), retiring only at Redeem | mint → transfer DUSD A→B (state unchanged) → A redeems at 100 DERO/DUSD → `v_retired` grows, `v_issued` fixed; assert `Issued−Retired == TotalDebt` at every step |
| I7 | `Emergency ⇒ Mint = 0` | Mint L154 · SetEmergency L365-373 | owner `SetEmergency()` → `protocol_state=4` → Mint → **REJECT**; Deposit → REJECT; Redeem → still OK (never blocks withdrawals). Non-owner `SetEmergency` → **REJECT** |
| I8 | `FailedTx ⇒ ΔState = 0` | DVM atomicity | run every negative scenario above, re-`SnapshotState()` → unchanged |

**Boundary tests (from v0.1 test corpus, re-run on skeleton):**
- 100 DEROS → mint exactly 0.72 → redeem 0.72 → get 72 DEROS back (28 = EF cost), `retired_dusd=0.72`, `total_debt=0`, `total_collateral=28`.
- 35M DERO ceiling-tie: gcap `25200000000000` (= 35,000,000 DERO × 72/10000) vs ceiling `25000000000000` → Mint amount 10^12+ … global ceiling binds, not solvency. (Corrected: the old `252000000000000` was 10× too large.)
- Overflow guards: col > `256204778801521550` (vault or aggregate) → REJECT; redeem amount > `184467440737095516` → REJECT.
- Redeem with `amount > Vault[SIGNER()].Debt` → REJECT (owner-redemption bound).
- Zero-value Deposit/Mint/Withdraw/Redeem → REJECT.

**Simulator gotchas (all observed on 3.5.5-142):**
- `SIGNER()` returns the raw **compressed pubkey bytes** (33 B; JSON shows string *values* hex-encoded, as in `owner`, and keys with `\xNN` escapes). Wrapping it in `ADDRESS_RAW()` is a no-op-to-catastrophe: `ADDRESS_RAW()` parses bech32 and returns `''` on the raw key, **silently merging every vault onto the shared key `"c:"`. VaultID = bare `SIGNER()`. `ADDRESS_RAW(bech32)` reproduces the identical key suffix, so `SnapshotVault(address)` works with a bech32 argument.
- `LOAD()` of a never-`STORE()`d key panics the VM ("Unhandled data_type") → every fresh key read needs the EXISTS/STORE-0 guard.
- `'` is lexed as char-literal start; only `//` and `/* */` comments are valid.
- State reads via `keysstring` return parallel `valuesstring[]`.

## PART D — Compile / deploy checklist (before running the scenarios)

1. `wget https://raw.githubusercontent.com/deroproject/derohe/master/cmd/dero/stresstest/...` → use the **remote simulator** on testnet (do NOT deplopy to mainnet).
2. Compile `DUSD-V0.2-CONTRACT.bas` with the DVM-BASIC compiler — expect 0 errors; note gas.
3. Deploy SC → capture SCID (DUSD = that SCID, so `SCID()` in the contract == DUSD token).
4. Fund two test wallets. Then run Part C scenarios 1→8 in order, recording `SnapshotState()`/`SnapshotVault()` after each step.
5. Report table back here: scenario | expected | actual | PASS/FAIL. File `DUSD-V0.2-TESTLOG.md`.