/* =====================================================================
   DUSD V0.2 DVM-BASIC SKELETON (Executable Specification)
   =====================================================================
   Phase 2 deliverable: real DVM-BASIC contract implementing the v0.2
   state machine. Four operations only: Deposit / Mint / Withdraw / Redeem.

   ---------------------------------------------------------------------
   MASTER INVARIANTS (section 21 of the spec) enforced in code below and
   must appear verbatim in the simulator test plan:
   ---------------------------------------------------------------------
     [I1]  Debt_i      <= C_i x 0.0072
     [I2]  TotalDebt   = sum_i Debt_i
     [I3]  TotalCollateral = sum_i C_i
     [I4]  TotalDebt   <= TotalCollateral x 0.0072
     [I5]  TotalCollateral <= ActualRecoverableDERO
           EXTERNAL custody invariant - NOT DVM-enforced; proven on
           testnet only (PART B / T3): contract cannot audit its own
           external DERO balance. Evidence = T3 + code review.
     [I6]  OutstandingDUSD = TotalDebt = IssuedDUSD - RetiredDUSD
           (retired_dusd is an ESCROW counter, not a burn proof)
     [I7]  Emergency   => Mint = 0
     [I8]  FailedTx    => Delta State = 0
   ---------------------------------------------------------------------

   MINT_RATIO = EF x FLOOR_PRICE = 0.72 x 0.01 = 0.0072
   In atoms:  capacity = collateral_atoms x 72 / 10000
   FLOOR_PRICE = $0.01  (redemption price: 1 DERO = $0.01)
   REDEEM_RATIO = 100 DERO per DUSD  (dero_out = amount x 100)

   Overflow-safety (derived from the v0.1 tests):
     - collateral x 72 fits uint64 for collateral <= 256204778801521550
       atoms (= ~2.56e9 DERO). Guard threshold = uint64_max / 72.
     - dero_out = amount x 100 fits uint64 for amount <= 184467440737095516
       atoms (= ~1.8e9 DUSD). Guard threshold = uint64_max / 100.

   Vault identification (v0.2.1 FIX):
     VaultID = SIGNER()   (raw compressed pubkey returned by the DVM
     SIGNER() primitive). This is the unique per-signer id.
     DO NOT wrap SIGNER() in ADDRESS_RAW(): ADDRESS_RAW() expects a bech32
     string (dero1...) and returns "" for the signer's raw key, which
     silently merges every vault into the key "c:"/"d:".
     SnapshotVault(address) takes a bech32 address and ADDRESS_RAW()s it
     internally, reproducing the same raw key suffix.
     There is NO user-chosen arbitrary VaultID.

   State keys:
     "c:<raw>"   vault collateral   (DERO atoms)
     "d:<raw>"   vault debt         (DUSD atoms)
     "h:<raw>"   vault created height
     total_collateral   aggregate   (DERO atoms)
     total_debt         aggregate   (DUSD atoms) = OutstandingDUSD
     issued_dusd        cumulative minted DUSD (non-decreasing)
     retired_dusd       cumulative escrowed DUSD (non-decreasing)
     global_ceiling     = 250000 DUSD = 25000000000000 atoms ($250K)
     protocol_state     0=NORMAL 1=ELEVATED 2=STRESS 3=RECOVERY 4=EMERGENCY

   Retirement semantics (v0.2.1 precisely):
     - Redeem() moves DUSD into the contract's own encrypted balance.
     - "retired_dusd" is an ESCROW counter, NOT a destruction proof.
     - I6 identity:  IssuedDUSD - RetiredDUSD == TotalDebt.
     - True DUSD destruction is TESTNET-REQUIRED (PART B T1).
     - Abnormal exit of escrowed DUSD is structurally impossible in this
       skeleton: V1 has no UPDATE_SC_CODE and there is no release path.

   Emergency (v0.2.1):
     - SetEmergency() :: owner-only (SIGNER()==owner) -- a PLACEHOLDER
       gate. The production gate is the Oracle 5-of-7 quorum + activation
       delay spec'd in the Oracle phase; owner gate is the stand-in until
       then. One-directional: NORMAL->EMERGENCY. No revert path exists.
     - [I7] EMERGENCY => Mint = 0; Redeem/Withdraw stay open (never
       blocks collateral exit).

   DVM RUNTIME CONSTRAINT (verified on simulator 3.5.5-142):
     LOAD(key) PANICS if key was never STOREd ("Unhandled data_type").
     Every read of a possibly-fresh key MUST be guarded:
         IF EXISTS(key) THEN GOTO <load>
         STORE(key, 0)
     <load> LET x = LOAD(key)
     Comments: ' is lexed as char-literal start (invalid); use // only.

   ---------------------------------------------------------------------
   TESTNET-REQUIRED (do NOT claim as final until proven on testnet):
     T1. DUSD retirement irreversibility DUSD entering the Core in
         Redeem() is deposited into the contract's encrypted balance.
         DERO "burn + SCID" is NOT a real destruction. In this skeleton
         "redeemAmount" is MOVED to an immutable retirement escrow; there
         is no release path (V1 has no UPDATE_SC_CODE). Must be proven.
     T2. Exact DUSD asset-input semantics ASSETVALUE(SCID()) scoping
         when DUSD is the Core's own token vs. a separate SCID. The
         ConvertTOKENX pattern proves the self-token case; the separate
         SCID case must be proven on testnet.
     T3. Core DERO custody accounted vault collateral cannot be
         extracted by any path other than Intentional Withdraw/Redeem.
         Must prove ActualRecoverableDERO >= AccountedCollateral.
   ===================================================================== */

Function InitializePrivate() Uint64
    10 STORE("owner", SIGNER())
    20 STORE("protocol_version", 1)
    30 STORE("protocol_state", 0)
    40 STORE("total_collateral", 0)
    50 STORE("total_debt", 0)
    60 STORE("retired_dusd", 0)
    65 STORE("issued_dusd", 0)
    70 STORE("global_ceiling", 25000000000000)
    80 RETURN 0
End Function

/* Deposit. DERO attaches (DEROVALUE). Issues NO DUSD.
   Preconditions: amount > 0, protocol_state != EMERGENCY.            */
Function Deposit() Uint64
    10 DIM amount AS Uint64
    20 DIM vault_id AS String
    30 DIM key_col AS String
    40 DIM cur_col, new_col AS Uint64
    50 DIM tot_col AS Uint64

    60 LET amount = DEROVALUE()
    70 IF amount == 0 THEN GOTO 900
    80 IF LOAD("protocol_state") == 4 THEN GOTO 900

    90 LET vault_id = SIGNER()
    100 LET key_col = "c:" + vault_id

    // guard: fresh vault reads as collateral zero instead of panicking
    110 IF EXISTS(key_col) THEN GOTO 130
    120 STORE(key_col, 0)
    130 LET cur_col = LOAD(key_col)

    140 LET new_col = cur_col + amount
    150 IF new_col < cur_col THEN GOTO 900

    160 LET tot_col = LOAD("total_collateral") + amount
    170 IF tot_col < LOAD("total_collateral") THEN GOTO 900

    180 STORE(key_col, new_col)
    190 STORE("total_collateral", tot_col)

    200 IF EXISTS("h:" + vault_id) THEN GOTO 220
    210 STORE("h:" + vault_id, BLOCK_HEIGHT())

    220 RETURN 0
    900 RETURN 1
End Function

/* Mint. User requests DUSD; contract computes limits from current state.
   Rejects if: not NORMAL-ish, vault capacity < request, global ceiling
   < request, or global solvency < request.                           */
Function Mint(requested Uint64) Uint64
    10 DIM amount AS Uint64
    20 DIM key_col, key_debt AS String
    30 DIM col, debt AS Uint64
    40 DIM cap, avail AS Uint64
    50 DIM tot_debt, tot_col AS Uint64
    60 DIM gcap AS Uint64

    70 LET amount = requested
    80 IF amount == 0 THEN GOTO 900
    90 IF LOAD("protocol_state") == 4 THEN GOTO 900   // [I7]

    100 LET key_col = "c:" + SIGNER()
    110 LET key_debt = "d:" + SIGNER()

    // guard: fresh vault keys read as zero
    120 IF EXISTS(key_col) THEN GOTO 140
    130 STORE(key_col, 0)
    140 LET col = LOAD(key_col)
    150 IF EXISTS(key_debt) THEN GOTO 170
    160 STORE(key_debt, 0)
    170 LET debt = LOAD(key_debt)

    // [I1] per-vault: Debt + amount <= Collateral x 0.0072
    180 IF col > 256204778801521550 THEN GOTO 900
    190 LET cap = col * 72 / 10000
    200 IF cap <= debt THEN GOTO 900
    210 LET avail = cap - debt
    220 IF amount > avail THEN GOTO 900

    // Global ceiling
    230 LET tot_debt = LOAD("total_debt")
    240 IF tot_debt > LOAD("global_ceiling") THEN GOTO 900
    250 IF amount > LOAD("global_ceiling") - tot_debt THEN GOTO 900

    // [I4] global solvency: TotalDebt + amount <= TotalCollateral x 0.0072
    260 LET tot_col = LOAD("total_collateral")
    270 IF tot_col > 256204778801521550 THEN GOTO 900
    280 LET gcap = tot_col * 72 / 10000
    290 IF tot_debt > gcap THEN GOTO 900
    300 IF amount > gcap - tot_debt THEN GOTO 900

    // Atomic transition: [I6] delta DUSD issued = delta TotalDebt
    310 STORE(key_debt, debt + amount)
    320 STORE("total_debt", tot_debt + amount)
    325 STORE("issued_dusd", LOAD("issued_dusd") + amount)
    330 SEND_ASSET_TO_ADDRESS(SIGNER(), amount, SCID())

    340 RETURN 0
    900 RETURN 1
End Function

/* Withdraw. User removes DERO. Post-state must remain above required
   collateral: Debt <= C_after x 0.0072. SEND failure => no state.    */
Function Withdraw(withdrawAmount Uint64) Uint64
    10 DIM amount AS Uint64
    20 DIM key_col, key_debt AS String
    30 DIM col, debt AS Uint64
    40 DIM c_after, cap_after AS Uint64

    50 LET amount = withdrawAmount
    60 IF amount == 0 THEN GOTO 900

    70 LET key_col = "c:" + SIGNER()
    80 LET key_debt = "d:" + SIGNER()

    90 IF EXISTS(key_col) THEN GOTO 110
    100 STORE(key_col, 0)
    110 LET col = LOAD(key_col)
    120 IF EXISTS(key_debt) THEN GOTO 140
    130 STORE(key_debt, 0)
    140 LET debt = LOAD(key_debt)

    150 IF amount > col THEN GOTO 900
    160 LET c_after = col - amount

    // [I1] post-solvency: Debt <= C_after x 0.0072
    170 IF c_after > 256204778801521550 THEN GOTO 900
    180 LET cap_after = c_after * 72 / 10000
    190 IF debt > cap_after THEN GOTO 900

    // Atomic transition
    200 STORE(key_col, c_after)
    210 STORE("total_collateral", LOAD("total_collateral") - amount)
    220 SEND_DERO_TO_ADDRESS(SIGNER(), amount)

    230 RETURN 0
    900 RETURN 1
End Function

/* Redeem. DUSD sent to Core -> owner debt reduced, DERO returned.
   redeemAmount = ASSETVALUE(SCID()) (NOT a user-supplied number).
   Owner-redemption model: redeemAmount <= Vault[SIGNER()].Debt.
   [TESTNET-REQUIRED T1/T2: escrow irreversibility + asset semantics] */
Function Redeem() Uint64
    10 DIM amount AS Uint64
    20 DIM key_col, key_debt AS String
    30 DIM col, debt AS Uint64
    40 DIM dero_out AS Uint64
    50 DIM new_debt, new_col AS Uint64
    60 DIM cap_after AS Uint64

    70 LET amount = ASSETVALUE(SCID())
    80 IF amount == 0 THEN GOTO 900

    90 LET key_col = "c:" + SIGNER()
    100 LET key_debt = "d:" + SIGNER()

    110 IF EXISTS(key_debt) THEN GOTO 130
    120 STORE(key_debt, 0)
    130 LET debt = LOAD(key_debt)

    // owner-redemption bound
    140 IF amount > debt THEN GOTO 900

    // REDEEM_RATIO: 1 DUSD = 100 DERO (FLOOR_PRICE $0.01). dero_out = amount x 100
    150 IF amount > 184467440737095516 THEN GOTO 900
    160 LET dero_out = amount * 100

    170 IF EXISTS(key_col) THEN GOTO 190
    180 STORE(key_col, 0)
    190 LET col = LOAD(key_col)

    200 IF dero_out > col THEN GOTO 900              // [I5] vault bound
    210 IF dero_out > LOAD("total_collateral") THEN GOTO 900   // Core redeemable

    220 LET new_debt = debt - amount
    230 LET new_col = col - dero_out

    // [I1] post-solvency
    240 IF new_col > 256204778801521550 THEN GOTO 900
    250 LET cap_after = new_col * 72 / 10000
    260 IF new_debt > cap_after THEN GOTO 900

    // Atomic transition: retire DUSD, reduce debt + collateral, send DERO.
    // [T1] DUSD sits in immutable retirement escrow - no release path.
    270 STORE(key_debt, new_debt)
    280 STORE(key_col, new_col)
    290 STORE("total_debt", LOAD("total_debt") - amount)
    300 STORE("total_collateral", LOAD("total_collateral") - dero_out)
    310 STORE("retired_dusd", LOAD("retired_dusd") + amount)

    320 SEND_DERO_TO_ADDRESS(SIGNER(), dero_out)

    330 RETURN 0
    900 RETURN 1
End Function

/* Snapshot helpers for simulator / off-chain monitoring.
   NOTE: DVM-BASIC has no read-only views; these write the v_* keys
   each call. Named Snapshot* (not Get*) to be honest about that.     */

Function SnapshotState() Uint64
    10 DIM tot_col, tot_deb AS Uint64
    20 DIM gcap AS Uint64

    30 LET tot_col = LOAD("total_collateral")
    40 LET tot_deb = LOAD("total_debt")
    50 IF tot_col > 256204778801521550 THEN GOTO 900
    60 LET gcap = tot_col * 72 / 10000

    70 STORE("v_total_collateral", tot_col)
    80 STORE("v_total_debt", tot_deb)
    90 STORE("v_global_cap", gcap)
    100 STORE("v_ceiling", LOAD("global_ceiling"))
    110 STORE("v_retired", LOAD("retired_dusd"))
    115 STORE("v_issued", LOAD("issued_dusd"))
    120 STORE("v_state", LOAD("protocol_state"))
    130 RETURN 0
    900 RETURN 1
End Function

/* SnapshotVault(address String). address = bech32 dero1/deto1 string.
   ADDRESS_RAW(address) -> raw compressed pubkey bytes == SIGNER()
   value == the vault-key suffix used by Deposit/Mint/Withdraw/Redeem.
   This is a snapshot helper that STOREs v_* mirror keys (DVM-BASIC has
   no read-only view; the sim uses STORE as its only output channel).   */
Function SnapshotVault(address String) Uint64
    10 DIM raw AS String
    20 DIM col AS Uint64

    30 LET raw = ADDRESS_RAW(address)
    40 IF EXISTS("c:" + raw) THEN GOTO 60
    50 STORE("c:" + raw, 0)
    60 LET col = LOAD("c:" + raw)
    70 IF col > 256204778801521550 THEN GOTO 900

    80 STORE("v_col", col)
    90 IF EXISTS("d:" + raw) THEN GOTO 110
    100 STORE("d:" + raw, 0)
    110 STORE("v_debt", LOAD("d:" + raw))
    120 STORE("v_cap", col * 72 / 10000)
    130 IF EXISTS("h:" + raw) THEN GOTO 150
    140 STORE("h:" + raw, 0)
    150 STORE("v_height", LOAD("h:" + raw))
    160 RETURN 0
    900 RETURN 1
End Function

/* On-chain global solvency check (simulator verdict).                  */
Function CheckGlobal() Uint64
    10 DIM tot_col, tot_debt, gcap, ceil_v AS Uint64

    20 LET tot_col = LOAD("total_collateral")
    30 LET tot_debt = LOAD("total_debt")
    40 LET ceil_v = LOAD("global_ceiling")

    50 IF tot_debt > ceil_v THEN GOTO 900
    60 IF tot_col > 256204778801521550 THEN GOTO 900
    70 LET gcap = tot_col * 72 / 10000
    80 IF tot_debt > gcap THEN GOTO 900

    90 STORE("v_total_collateral", tot_col)
    100 STORE("v_total_debt", tot_debt)
    110 STORE("v_global_cap", gcap)
    120 STORE("v_ceiling", ceil_v)
    130 STORE("v_retired", LOAD("retired_dusd"))
    135 STORE("v_issued", LOAD("issued_dusd"))
    140 RETURN 0
    900 RETURN 1
End Function

/* SetEmergency. Owner-only, one-way NORMAL->EMERGENCY. PLACEHOLDER for
   the Oracle 5-of-7 quorum + activation delay (Oracle phase). Since V1
   is no-upgradeability this is the only write path for protocol_state
   once deployed; no revert function exists. [I7]                     */
Function SetEmergency() Uint64
    10 IF LOAD("protocol_state") == 4 THEN GOTO 900
    20 IF SIGNER() == LOAD("owner") THEN GOTO 40
    30 GOTO 900
    40 STORE("protocol_state", 4)
    50 STORE("v_state", 4)
    60 RETURN 0
    900 RETURN 1
End Function