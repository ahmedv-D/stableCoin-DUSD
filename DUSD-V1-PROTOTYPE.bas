/* DUSD V1 Prototype — Direct Redemption Mechanism
   Testnet deployment for validating the ConvertTOKENX-pattern
   token-in / DERO-out atomic swap with solvency checks.

   Based on the official token.bas ConvertTOKENX pattern.
   Proven: SEND_DERO_TO_ADDRESS(SIGNER(), ASSETVALUE(SCID())) works.
   Proven: DVM reverts atomically if contract has insufficient DERO.

   === UNIT SYSTEM ===
   All balances in atoms (10^8 per DERO or DUSD unit).
   P_floor = 10000 micro-USD ($0.01/DERO)
   EF = 720000 micro-units (0.72)
   SCALE = 1000000 (micro per unit)

   === OVERFLOW-SAFE FORMULA ===
   hard_cap = collateral_atoms * P_floor * EF / SCALE / SCALE
            = collateral_atoms * 10000 * 720000 / 1000000 / 1000000
            = collateral_atoms * 72 / 10000

   To avoid overflow, simplify BEFORE multiplying:
     step1 = collateral_atoms / 100       (= collateral * 10000 / 1000000)
     step2 = step1 * 72 / 100             (= step1 * 720000 / 1000000)
     hard_cap = step2

   For 35M DERO (3.5e15 atoms):
     step1 = 3.5e15 / 100 = 3.5e13
     step2 = 3.5e13 * 72 / 100 = 2.52e13 DUSD atoms = $252,000  ✓

   For 100 DERO (1e10 atoms):
     step1 = 1e10 / 100 = 1e8
     step2 = 1e8 * 72 / 100 = 72000000 DUSD atoms = $0.72  ✓

   === KEY INSIGHT ===
   Issuance rate = P_floor x EF = $0.0072/DERO.
   100 DERO -> 0.72 DUSD (not 1 DUSD).
   The issuance IS the cap: hard_cap - current_debt.

   === REDEMPTION ===
   RedemptionRate = P_floor = $0.01/DERO.
   DERO returned = DUSD_atoms * 100.
   0.72 DUSD burned -> 72 DERO returned.
   Net: 100 DERO in, 72 DERO out, 28 DERO = EF cost.

   === KNOWN LIMITATION ===
   Deposits < 100 atoms (< 10^-6 DERO) truncate to 0.
   Acceptable for testnet.
*/

Function InitializePrivate() Uint64
    10 STORE("owner", SIGNER())
    20 STORE("total_debt", 0)
    30 STORE("total_collateral", 0)
    40 RETURN 0
End Function

/* Mint DUSD: user sends DERO, receives DUSD at P_floor x EF rate.
   100 DERO -> 0.72 DUSD. Cap enforced atomically. */
Function Mint() Uint64
    10 DIM dero_in AS Uint64
    20 DIM current_debt AS Uint64
    30 DIM current_collateral AS Uint64
    40 DIM new_collateral AS Uint64
    50 DIM new_debt AS Uint64
    60 DIM hard_cap AS Uint64
    70 DIM step1 AS Uint64
    80 DIM step2 AS Uint64
    90 DIM dusd_to_issue AS Uint64

    100 LET dero_in = DEROVALUE()
    110 IF dero_in == 0 THEN GOTO 900

    120 LET current_debt = LOAD("total_debt")
    130 LET current_collateral = LOAD("total_collateral")

    /* New collateral (overflow check) */
    140 LET new_collateral = current_collateral + dero_in
    150 IF new_collateral < current_collateral THEN GOTO 900

    /* Hard cap via overflow-safe simplified formula:
       hard_cap = new_collateral * 72 / 10000
       Staged: step1 = collateral / 100, step2 = step1 * 72 / 100 */
    160 LET step1 = new_collateral / 100

    /* step2 = step1 * 72 (overflow check) */
    170 IF step1 > 256204778801521550 THEN GOTO 900
    180 LET step2 = step1 * 72 / 100

    190 LET hard_cap = step2

    /* Issue DUSD = hard_cap - current_debt (issue up to cap) */
    200 IF hard_cap <= current_debt THEN GOTO 900
    210 LET dusd_to_issue = hard_cap - current_debt

    /* New debt */
    220 LET new_debt = current_debt + dusd_to_issue
    230 IF new_debt < current_debt THEN GOTO 900

    /* Update state atomically */
    240 STORE("total_collateral", new_collateral)
    250 STORE("total_debt", new_debt)

    /* Issue DUSD tokens to user */
    260 SEND_ASSET_TO_ADDRESS(SIGNER(), dusd_to_issue, SCID())

    270 RETURN 0
    900 RETURN 1
End Function

/* Redeem DUSD: user sends DUSD to contract, receives DERO back.
   RedemptionRate = P_floor = $0.01/DERO.
   DERO returned = DUSD x 100.
   0.72 DUSD burned -> 72 DERO returned.
   DUSD deposited to contract (functionally burned).
   DERO sent to user atomically. */
Function Redeem() Uint64
    10 DIM dusd_in AS Uint64
    20 DIM current_debt AS Uint64
    30 DIM current_collateral AS Uint64
    40 DIM dero_to_send AS Uint64
    50 DIM new_debt AS Uint64
    60 DIM new_collateral AS Uint64
    70 DIM hard_cap AS Uint64
    80 DIM step1 AS Uint64
    90 DIM step2 AS Uint64

    100 LET dusd_in = ASSETVALUE(SCID())
    110 IF dusd_in == 0 THEN GOTO 900

    120 LET current_debt = LOAD("total_debt")
    130 LET current_collateral = LOAD("total_collateral")

    /* Cannot redeem more than total debt */
    140 IF dusd_in > current_debt THEN GOTO 900

    /* DERO to return: dusd_in * 100 (overflow check) */
    150 IF dusd_in > 1844674407370955161 / 100 THEN GOTO 900
    160 LET dero_to_send = dusd_in * 100

    /* Contract must have enough DERO */
    170 IF dero_to_send > current_collateral THEN GOTO 900

    /* New state */
    180 LET new_debt = current_debt - dusd_in
    190 LET new_collateral = current_collateral - dero_to_send

    /* Recompute hard cap on new state (same overflow-safe formula) */
    200 LET step1 = new_collateral / 100
    210 IF step1 > 256204778801521550 THEN GOTO 900
    220 LET step2 = step1 * 72 / 100
    230 LET hard_cap = step2

    /* Solvency check */
    240 IF new_debt > hard_cap THEN GOTO 900

    /* Update state atomically */
    250 STORE("total_debt", new_debt)
    260 STORE("total_collateral", new_collateral)

    /* Send DERO to user */
    270 SEND_DERO_TO_ADDRESS(SIGNER(), dero_to_send)

    280 RETURN 0
    900 RETURN 1
End Function

/* Query current protocol state. */
Function GetState() Uint64
    10 STORE("view_debt", LOAD("total_debt"))
    20 STORE("view_collateral", LOAD("total_collateral"))
    30 RETURN 0
End Function
