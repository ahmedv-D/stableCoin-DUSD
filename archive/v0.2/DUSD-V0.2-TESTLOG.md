# DUSD V0.2.1 — Simulator Test Log (vault-isolation fix verification)

Contract: `DUSD-V0.2-CONTRACT.bas` (as edited for v0.2.1 + vault-key fix)
Simulator: 3.5.5-142.DEROHE.STARGATE+13082025 (fresh instance, deterministic wallet addrs)
Test instance SCID: `d9831ed32dd583f781f815b732da154b8254a6c8ea9fa0a2b5c2dd5a215f7ed2`
Wallets: wallet2/wallet9 hosted RPC 127.0.0.1:30002 / 30009 · daemon 127.0.0.1:39991

## Scenario results

| # | Scenario | Expected | Actual | Verdict |
|---|----------|----------|--------|---------|
| 1 | wallet2 Deposit 300000, wallet9 Deposit 200000 | 2 distinct vault keys, Σcol=500000 | 2 `c:` keys (300000 / 200000), total_collateral=500000 | PASS (I3) |
| 2 | wallet2 Mint 2160 (own cap = 300000×72/10000) | debt on vault2 = 2160 | `d:` vault2 = 2160, total_debt=2160, issued=2160 | PASS |
| 3 | wallet2 `/getbalance` with DUSD scid | 2160 DUSD | 2160 | PASS |
| 4 | wallet9 Mint 2000 (own cap = 200000×72/10000 = 1440) | REJECT (per-vault cap) | rejected; no new `d:` key, issued/debt unchanged | PASS (I1 isolate) |
| 5 | wallet9 Mint 1440 (own cap) | 1440 on vault9 | `d:` vault9=1440, issued=3600, total_debt=3600 | PASS (I2: 2160+1440=3600) |
| 6 | SnapshotVault(wallet2 bech32) | v_col=300000, v_debt=2160, v_cap=2160 | v_col=300000, v_debt=2160, v_cap=2160 | PASS |
| 7 | non-owner (wallet2) SetEmergency | REJECT, state stays 0 | protocol_state=0 | PASS |
| 8 | owner (wallet9) SetEmergency | state=4 | protocol_state=4 | PASS (I7) |
| 9 | Mint 1 while EMERGENCY | REJECT | rejected, issued=3600 debt=3600 unchanged | PASS (I8) |
| 10 | Redeem 2160 while EMERGENCY | allowed; col 500000→284000 (2160×100), DUSD burned | col=284000, retired=2160, debt=1440; wallet2 DUSD=0 | PASS (I6: 3600−2160=1440=debt) |
| 11 | wallet9 Withdraw 1 at exactly floor (200000 col / 1440 debt → min 200000) | REJECT | rejected, col unchanged 284000 | PASS (I1 floor) |
| 12 | wallet2 Deposit 1 while EMERGENCY | REJECT + funds returned | rejected, col unchanged 284000, DERO returned | PASS (I7+I8) |

## Critical defect found & fixed

**Vault-key merge (silent, security-relevant):** the original code used
`ADDRESS_RAW(SIGNER())` as the vault-key suffix. `SIGNER()` returns the raw
compressed pubkey bytes; `ADDRESS_RAW()` expects bech32 and returns `''`,
so every vault collapsed onto the shared key `"c:"`/`"d:"` — wallets pooled
collateral and crossed per-vault caps while still minting up to the *aggregate*
cap. Fixed by using bare `SIGNER()` as `c:`/`d:` suffix and letting
`SnapshotVault(address String)` call `ADDRESS_RAW(address)` internally.
Verified: wallets get distinct keys and per-vault caps bind independently.

## Still blocked (Part B — TESTNET REQUIRED)

- T1 DUSD retirement irreversibility (escrow, not burn) — needs testnet attempt-all-paths loop.
- T2 exact DUSD asset-input semantics (`ASSETVALUE(SCID())` binding).
- T3 Core DERO custody (`TotalCollateral ≤ ActualRecoverableDERO` + hostile extraction).