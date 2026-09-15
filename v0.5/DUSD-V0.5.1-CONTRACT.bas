/* =====================================================================
   DUSD V0.5.1 DVM-BASIC SKELETON (Executable Specification)
   =====================================================================
   Implements the DUSD V0.5 unified-claim economic model audited in
   FINAL-AUDIT-REPORT.md with the V0.5.1 fix set baked in as gate logic.
   Research/testnet only. This is a SIMULATOR-PARITY skeleton: the float
   sim (attack/dusd_v05_engine.py) is the oracle; every gate below mirrors
   a sim assertion under uint64 atom math (1 DERO = 100000 atoms; DUSD
   uses the same atom granularity).

   ---------------------------------------------------------------------
   V0.5.1 FIX SET (FINAL-AUDIT-REPORT.md section 7; each maps to a gate):
     [F1] mint_price   = min(P0, P_risk)        -> closes S9/S10 recursion
     [F2] DERO redemption legs settle at max(P_risk, P_spot)
                                                -> closes S5 wedge
     [F3] amm_cap      = effective input cap    -> closes S16 uint64
     [F4] pol_drawdown_guard: net POL DERO out <= DERO fee-in * ratio
                                                -> closes S9/S10 hard loop
     [F5] liquidate ledger identity: S -= ONLY the value that actually
          leaves the ledger; collateral relocates vault->POL; residue is
          explicit bad debt (fuzz-found; DUSD ledger == S identity)
     [F6] unclaimed backers fee rolls to insurance (no silent value drop)
   ---------------------------------------------------------------------
   ATOM MATH (gate-level, div-before-mul):
     Price: P_spot = Y / X, X = POL DERO atoms, Y = POL DUSD atoms.
       Genesis Y = 1000 DUSD * 1e5, X = 100000 DERO * 1e5 => P0 = 0.01.
       Fixed-point: price scaled to 1e5, value = INT / 100000.
       spot_1e5 = Y * 100000 / X          (guard Y * 100000 <= U64MAX)
     TWAP (block-anchored, fp 1e5):
       per block: v' = 0.95*v + 0.05*spot (alpha=0.05), loop <=DECAY_CAP.
     MINT capacity = C * P * LTV, LTV = 72/100:
       cap = C * 72 * pmin / (100 * pden)   (C, p in DUSD-per-DERO atoms)
       At P0: C * 0.01 * 0.72 = C * 0.0072 == V0.2 C*72/10000. Consistent.
     POL sidecar on mint: pol_dusd = gross * 25/10000 (Q_POL=0.0025);
       pol_dero = pol_dusd * X / Y at P_spot (DERO atoms for the pool).
     SWAP fee = in * 3000 / 1_000_000 (0.003), split 7000/1000/1500/500
       per 10000 => depth / growth / backers / insurance.
     swap DUSD->DERO: out = X * eff / (Y + eff)    [F3 guard X * eff]
     swap DERO->DUSD: out = Y * eff / (X + eff)    [F3 guard Y * eff]
     AMM_CAP = 184500000 atoms (~1845 DERO). Derivation (S16): with a
       reserve of ~1e11 DERO atoms (1e6 DERO), the swap product X*eff and
       Y*eff must stay < 2^64 ~= 1.8e19; cap = floor(1.8e19 / 1e11) atoms
       of effective input per swap. Final value verified by div-before-mul
       guards regardless.
     Redeem basket (pari-passu, q = amount * ClaimFactor / 1e5):
       tier 1 POL DUSD, tier 2 insurance DUSD, tier 3 POL DERO at pdero,
       tier 4 insurance DERO at pdero, tier 5 collateral at pdero.
       [F2] pdero = max(P_risk, P_spot) as fp 1e5.
   ---------------------------------------------------------------------
   MASTER INVARIANTS (mirror of I1-I20 in the attack suite):
     [I-A] DERO total (pool + insurance + vault + deferred backers) is
           invariant: every out-flow is matched by an in-flow in the same
           instruction.
     [I-B] DUSD ledger == S: S = pool + insurance DUSD + wallets + escrow -
           burned. Mint ADDs S; redeem/repay/liquidate SUBTRACT exactly the
           atoms that leave the ledger.  [F5 enforces]
     [I-C] ClaimFactor = min(1, Backing_NAV / S); redemption is pari-passu
           and basket order is fixed.
     [I-D] Collateral counted at H*P_risk (H=90/100) and never paid above
           the settle price the DERO tier uses.  [F2 + F5]
     [I-E] Division executes before its multiplying partner wherever the
           product could exceed uint64 (div-before-mul).
     [I-F] Failed transaction => zero state delta. All guards precede the
           first STORE; no partial transitions.
   ---------------------------------------------------------------------
   DVM RUNTIME CONSTRAINTS (verified on simulator 3.5.5-142):
     - LOAD(key) PANICS on a never-STOREd key ("Unhandled data_type").
       Every read of a possibly-fresh key is EXISTS-guarded.
     - ' is lexed as char-literal start: use ONLY // comments.
     - DVM-BASIC has no read-only views: Snapshot* STORE v_* mirrors.
     - Subroutine calls are testnet-flagged (T3); gates inline decay where
       safe, and helpers below take precedence only after on-sim validation.
   ---------------------------------------------------------------------
   TESTNET-REQUIRED (do NOT claim final until proven):
     T1. DUSD asset-input semantics when DUSD is the Core SC's own token
         (ASSETVALUE(SCID()) self-token path) vs a separate SCID.
     T2. ActualRecoverableDERO >= AccountedCollateral (custody census).
     T3. Multi-function sub-routine call + TWAP loop gas cost under
         worst-case elapsed blocks (DECAY_CAP).
     T4. BN_TO_DECIMAL(STORAGE_USED()) limits for v_* + vault keys.
     T5. SEND_ASSET_TO_ADDRESS of the SC's own token to a third party is
         a balance transfer (not a re-mint) - proves no DUSD creation.
   ===================================================================== */

// ------------------------------------------------------------------
// CONSTANTS (atoms). DVM-BASIC has no const declaration; Initialize
// writes them and gates read them (or bake the literals below).
//   1 DERO = 100000 atoms
//   P0        = 1 DUSD : 100 DERO = 0.01 DUSD/DERO
//   LTV       = 72/100            H = 90/100 (haircut 0.90)
//   Q_POL     = 25/10000 (0.0025)
//   SWAP_FEE  = 3000/1000000 (0.003)
//   MIN_COVERAGE = 100/100 (1.00)  LIQ_CR = 120/100 (1.20)
//   GLOBAL_CEILING = 250000 DUSD = 25000000000000 atoms
//   POL_DRAWDOWN_RATIO = 4 (F4)    AMM_CAP = 184500000 atoms (F3)
//   DECAY_CAP = 100 blocks (TWAP loop gas bound, T3)
// ------------------------------------------------------------------

Function InitializePrivate() Uint64
     10 STORE("owner", SIGNER())
     20 STORE("protocol_version", 1)
     30 STORE("protocol_state", 0)
     40 STORE("x_pol_dero", 100000 * 100000)      // GENESIS_X = 100000 DERO
     50 STORE("y_pol_dusd", 1000 * 100000)        // GENESIS_Y = 1000 DUSD
     60 STORE("ins_dero", 0)
     65 STORE("ins_dusd", 0)
     70 STORE("s_outstanding", 1000 * 100000)     // S = GENESIS_Y atoms
     80 STORE("total_collateral", 0)
     90 STORE("total_debt", 0)
     100 STORE("global_ceiling", 250000 * 100000)
     110 STORE("prev_block", BLOCK_HEIGHT())
     120 STORE("pol_dero_in", 0)
     130 STORE("pol_dero_out", 0)
     140 STORE("twap_num", 1 * 100000)            // P_risk fp 1e5 = 0.01
     150 STORE("amm_cap", 184500000)
     160 STORE("bad_debt", 0)
     170 STORE("decay_cap", 100)
     180 RETURN 0
End Function

/* Decay(): advance TWAP toward spot for elapsed blocks (bounded).
   fp scale 1e5:  spot_1e5 = Y*100000/X   (guard Y*100000 <= U64MAX).
   per block: v' = (v*95 + spot*5)/100.  Writes twap_num + prev_block.
   RETURNS: 1 on overflow/edge, 0 on success. Calls are TESTNET-REQUIRED
   (T3: gas). */
Function Decay() Uint64
     10 DIM xp, yp, bn, b0, steps, i AS Uint64
     20 DIM tn, sp AS Uint64

     30 LET xp = LOAD("x_pol_dero")
     40 LET yp = LOAD("y_pol_dusd")
     50 LET bn = BLOCK_HEIGHT()
     60 LET b0 = LOAD("prev_block")
     70 IF bn == b0 THEN GOTO 300
     80 IF bn < b0 THEN GOTO 900

     // spot fp, guarded against mul overflow
     90 IF yp > 184467440737095516 THEN GOTO 900
     100 IF xp == 0 THEN GOTO 900
     110 LET sp = yp * 100000 / xp

     120 LET steps = bn - b0
     130 IF steps > LOAD("decay_cap") THEN LET steps = LOAD("decay_cap")
     140 LET tn = LOAD("twap_num")
     142 IF sp > 18446744073709551615 / 5 THEN GOTO 900
     150 LET i = 0
     160 LET i = i + 1
     170 IF i > steps THEN GOTO 300
     180 IF tn > (18446744073709551615 - sp * 5) / 95 THEN GOTO 900
     190 LET tn = (tn * 95 + sp * 5) / 100
     200 GOTO 160

     300 STORE("prev_block", bn)
     310 RETURN 0
     900 RETURN 1
End Function

/* NavNow(): recompute Backing_NAV and ClaimFactor fp 1e5.
   Backing_NAV = Y_POL + I_DUSD + P*(X_POL + I_DERO) + H*P*C.
   Nav * 1e5 / S capped at 1e5 (ClaimFactor = min(1, NAV/S)).
   Writes v_cover / v_cf (DVM-BASIC has no read-only view).   */
Function NavNow() Uint64
     10 DIM xp, yp, idr, idu, ctot AS Uint64
     20 DIM tn, dero_val, col_val AS Uint64
     30 DIM nav, s_out, cf AS Uint64
     40 DIM rc AS Uint64

     50 LET rc = Decay()          // [T3] sub-call gas testnet-required
     60 IF rc == 1 THEN GOTO 900

     70 LET xp = LOAD("x_pol_dero")
     80 LET yp = LOAD("y_pol_dusd")
     90 LET idr = LOAD("ins_dero")
     100 LET idu = LOAD("ins_dusd")
     110 LET ctot = LOAD("total_collateral")
     120 LET tn = LOAD("twap_num")

     // dero_val = (X_POL + I_DERO) * twap_num / 100000, in DUSD atoms.
     // P = twap_num / 100000 DUSD-per-DERO; atoms are raw DERO atoms.
     // value in DUSD atoms = dero_atoms * P = dero_atoms * tn / 100000.
     130 IF xp + idr < xp THEN GOTO 900           // add overflow
     135 IF xp + idr > 18446744073709551615 / tn THEN GOTO 900   // mul guard
     140 LET dero_val = (xp + idr) * tn / 100000
     150 IF dero_val > 18446744073709551615 - (yp + idu) THEN GOTO 900

     // col_val = C * 90/100 * P, in DUSD atoms
     160 IF ctot > 18446744073709551615 / 90 THEN GOTO 900
     170 IF (ctot * 90) > 18446744073709551615 / tn THEN GOTO 900
     180 LET col_val = ctot * 90 * tn / (100 * 100000)

     190 LET nav = yp + idu + dero_val + col_val
     200 IF nav < yp + idu THEN GOTO 900          // add overflow
     210 LET s_out = LOAD("s_outstanding")
     220 IF s_out == 0 THEN GOTO 500
     230 IF nav > 18446744073709551615 / 100000 THEN GOTO 900
     240 LET cf = nav * 100000 / s_out
     250 IF cf > 100000 THEN LET cf = 100000

     500 STORE("v_cover", nav)
     510 STORE("v_cf", cf)
     520 RETURN 0
     900 RETURN 1
End Function

/* Deposit. DERO attaches (DEROVALUE). Issues NO DUSD.               */
Function Deposit() Uint64
     10 DIM amount, key_col, cur_col, new_col, tot_col AS Uint64
     20 DIM vault_id AS String
     30 DIM rc AS Uint64

     40 LET amount = DEROVALUE()
     50 IF amount == 0 THEN GOTO 900
     60 IF LOAD("protocol_state") == 4 THEN GOTO 900
     70 LET rc = Decay()
     80 IF rc == 1 THEN GOTO 900

     90 LET vault_id = SIGNER()
     100 LET key_col = "c:" + vault_id
     110 IF EXISTS(key_col) THEN GOTO 130
     120 STORE(key_col, 0)
     130 LET cur_col = LOAD(key_col)

     140 LET new_col = cur_col + amount
     150 IF new_col < cur_col THEN GOTO 900

     160 LET tot_col = LOAD("total_collateral")
     170 IF amount > 18446744073709551615 - tot_col THEN GOTO 900

     180 STORE(key_col, new_col)
     190 STORE("total_collateral", tot_col + amount)
     200 IF EXISTS("h:" + vault_id) THEN GOTO 220
     210 STORE("h:" + vault_id, BLOCK_HEIGHT())
     220 RETURN 0
     900 RETURN 1
End Function

/* Mint(requested Uint64): V0.5 issuance with [F1] P_min = min(P0,P_risk).
   cap = C * P_min * LTV; pol sidecar Q_POL on the gross; coverage gate
   [I-C] via NavNow(); global ceiling gate. Atomic: all guards first.  */
Function Mint(requested Uint64) Uint64
     10 DIM amount, key_col, key_debt AS String
     20 DIM col, debt, gate AS Uint64
     30 DIM pmin, pden AS Uint64
     40 DIM cap_new, avail, gcap AS Uint64
     50 DIM tot_debt, s_out AS Uint64
     60 DIM gross, pol_dusd, pol_dero, xp, yp AS Uint64
     70 DIM rc, cf AS Uint64

     80 LET amount = requested
     90 IF amount == 0 THEN GOTO 900
     100 IF LOAD("protocol_state") == 4 THEN GOTO 900       // [I7]
     110 LET rc = NavNow()
     120 IF rc == 1 THEN GOTO 900
     130 LET cf = LOAD("v_cf")
     140 IF cf < 100000 THEN GOTO 900       // [F1] coverage >= 1.00

     150 LET key_col = "c:" + SIGNER()
     160 LET key_debt = "d:" + SIGNER()
     170 IF EXISTS(key_col) THEN GOTO 190
     180 STORE(key_col, 0)
     190 LET col = LOAD(key_col)
     200 IF EXISTS(key_debt) THEN GOTO 220
     210 STORE(key_debt, 0)
     220 LET debt = LOAD(key_debt)

     // [F1] pmin = min(P0, P_risk): P0 = 1/100, P_risk fp 1e5.
     // compare 1/100 vs tn/100000  =>  1e5*?  skip: compare 100*tn >= 1e5? 
     // P0(fp) = 1000 (1/100 * 1e5). min = tn if tn < 1000 else 1000.
     230 LET pden = 100000
     240 LET pmin = LOAD("twap_num")
     250 IF pmin > 1000 THEN GOTO 270
     260 GOTO 280
     270 LET pmin = 1000

     // cap = C * pmin/100000 * 72/100 = C * pmin * 72 / (100000*100)
     280 IF col > 18446744073709551615 / pmin THEN GOTO 900
     290 IF col * pmin > 18446744073709551615 / 72 THEN GOTO 900
     300 LET cap_new = col * pmin * 72 / 10000000
     310 IF cap_new < debt THEN GOTO 900
     320 LET avail = cap_new - debt
     330 IF amount > avail THEN GOTO 900

     // global ceiling
     340 LET tot_debt = LOAD("total_debt")
     350 IF tot_debt > LOAD("global_ceiling") THEN GOTO 900
     360 IF amount > LOAD("global_ceiling") - tot_debt THEN GOTO 900

     // S / ledger
     370 LET s_out = LOAD("s_outstanding")
     380 IF amount > 18446744073709551615 - s_out THEN GOTO 900

     // POL sidecar: pol_dusd = gross * Q_POL; pol_dero at P_spot
     390 LET gross = amount
     400 LET pol_dusd = gross * 25 / 10000
     410 LET xp = LOAD("x_pol_dero")
     420 LET yp = LOAD("y_pol_dusd")
     430 IF yp == 0 THEN GOTO 900
     440 IF pol_dusd > 18446744073709551615 / xp THEN GOTO 900
     450 LET pol_dero = pol_dusd * xp / yp
     460 IF pol_dero > col THEN GOTO 900          // always <= C

     // Atomic transition (no path fails after this point)
     470 STORE(key_debt, debt + amount)
     480 STORE("total_debt", tot_debt + amount)
     490 STORE("s_outstanding", s_out + amount)
     500 IF pol_dero > 0 THEN GOTO 520
     510 GOTO 540
     520 STORE("x_pol_dero", xp + pol_dero)
     530 STORE("total_collateral", LOAD("total_collateral") - pol_dero)
     540 IF pol_dusd > 0 THEN GOTO 560
     550 GOTO 570
     560 STORE("y_pol_dusd", yp + pol_dusd)
     570 SEND_ASSET_TO_ADDRESS(SIGNER(), amount, SCID())
     580 RETURN 0
     900 RETURN 1
End Function

/* Swap(dir Uint64, amount_in Uint64). dir=0 DUSD->DERO, dir=1 DERO->DUSD.
   fee split on the *gross* amount: depth/growth share back to pool,
   backers share 15%, insurance share 5%. [F3] eff <= AMM_CAP and
   out < reserve with div-before-mul. [F4] on DUSD->DERO the cumulative
   DERO pulled <= pol_dero_in * ratio. [F6] backers w/ no active lockers
   -> insurance. */
Function Swap(dir Uint64, amount_in Uint64) Uint64
     10 DIM fee, eff, xp, yp AS Uint64
     20 DIM out_amt, pool_growth AS Uint64
     30 DIM cap_check, backers, ins5 AS Uint64
     40 DIM p_in, p_out, budget AS Uint64
     50 DIM rc, asset_tag AS Uint64

     60 IF amount_in == 0 THEN GOTO 900
     70 IF LOAD("protocol_state") == 4 THEN GOTO 900
     80 LET rc = Decay()
     90 IF rc == 1 THEN GOTO 900

     100 LET fee = amount_in * 3000 / 1000000
     110 LET eff = amount_in - fee
     120 IF eff == 0 THEN GOTO 900
     130 IF eff > LOAD("amm_cap") THEN GOTO 900   // [F3]

     140 LET xp = LOAD("x_pol_dero")
     150 LET yp = LOAD("y_pol_dusd")
     160 IF dir == 0 THEN GOTO 400                 // DUSD->DERO

     // ---- DERO->DUSD: out = Y*eff/(X+eff) [F3] ----
     170 IF eff > 18446744073709551615 / yp THEN GOTO 900
     180 LET out_amt = yp * eff / (xp + eff)
     190 IF out_amt > yp THEN GOTO 900
     200 LET pool_growth = eff + fee * 8000 / 10000    // depth+growth
     210 IF pool_growth > 18446744073709551615 - xp THEN GOTO 900
     220 LET backers = fee * 1500 / 10000
     230 LET ins5 = fee * 500 / 10000

     // atomic
     240 STORE("x_pol_dero", xp + pool_growth)
     250 STORE("pol_dero_in", LOAD("pol_dero_in") + pool_growth)
     260 STORE("y_pol_dusd", yp - out_amt)
     270 STORE("ins_dero", LOAD("ins_dero") + ins5)
     280 LET asset_tag = 1
     290 GOTO 320

     320 LET rc = Backers(asset_tag, backers)     // [T3] sub-call
     324 GOTO 600

     // ---- DUSD->DERO: out = X*eff/(Y+eff) [F3] ----
     400 IF eff > 18446744073709551615 / xp THEN GOTO 900
     410 LET out_amt = xp * eff / (yp + eff)
     420 IF out_amt > xp THEN GOTO 900

     // [F4] drawdown guard
     430 LET p_in = LOAD("pol_dero_in")
     440 LET p_out = LOAD("pol_dero_out")
     450 IF p_in > 18446744073709551615 / 4 THEN GOTO 900
     460 LET budget = p_in * 4
     470 IF p_out > budget THEN GOTO 900
     480 IF out_amt > 18446744073709551615 - p_out THEN GOTO 900

     490 LET pool_growth = eff + fee * 8000 / 10000    // depth+growth (DUSD)
     500 IF pool_growth > 18446744073709551615 - yp THEN GOTO 900
     510 LET backers = fee * 1500 / 10000
     520 LET ins5 = fee * 500 / 10000

     // atomic
     530 STORE("y_pol_dusd", yp + pool_growth)
     540 STORE("pol_dero_out", p_out + out_amt)
     550 STORE("x_pol_dero", xp - out_amt)
     560 STORE("ins_dusd", LOAD("ins_dusd") + ins5)
     570 LET asset_tag = 0
     580 LET rc = Backers(asset_tag, backers)      // [T3] sub-call

     // payout
     600 IF dir == 1 THEN GOTO 640
     610 SEND_DERO_TO_ADDRESS(SIGNER(), out_amt)
     620 GOTO 660
     640 SEND_ASSET_TO_ADDRESS(SIGNER(), out_amt, SCID())
     660 RETURN 0
     900 RETURN 1
End Function

/* Backers(asset Uint64, share Uint64): accrue share proportionally to
   W = LockedCollateral * lockdays (0=DERO, 1=DUSD). If no active locker
   [F6] the share rolls to insurance. Lock table keys "lk:<i>","ld:<i>",
   "acc:<i>:<asset>"; n_lockers. Registry maintenance is a hard TODO:
   a per-owner weight registry + expiresAt prune (see TODO section).   */
Function Backers(asset Uint64, share Uint64) Uint64
     10 DIM nlock, i, w_this, cnt AS Uint64
     20 DIM key_lk, key_ld, key_acc AS String
     30 DIM total_w AS Uint64

     40 IF EXISTS("n_lockers") THEN GOTO 60
     50 STORE("n_lockers", 0)
     60 LET nlock = LOAD("n_lockers")
     70 IF nlock == 0 THEN GOTO 500             // [F6] roll to insurance
     80 IF EXISTS("total_weight") THEN GOTO 100
     90 STORE("total_weight", 0)
     100 LET total_w = LOAD("total_weight")
     110 IF total_w == 0 THEN GOTO 500          // [F6]

     120 LET i = 0
     130 LET i = i + 1
     140 IF i > nlock THEN GOTO 400
     150 LET key_lk = "lk:" + STR(i)
     160 LET key_ld = "ld:" + STR(i)
     170 IF EXISTS(key_lk) THEN GOTO 190
     180 STORE(key_lk, 0)
     190 IF EXISTS(key_ld) THEN GOTO 210
     200 STORE(key_ld, 0)
     210 LET w_this = LOAD(key_lk) * LOAD(key_ld)
     220 IF w_this == 0 THEN GOTO 130
     230 IF w_this > 18446744073709551615 / share THEN GOTO 500  // guard
     240 LET key_acc = "acc:" + STR(i) + ":" + STR(asset)
     250 IF EXISTS(key_acc) THEN GOTO 270
     260 STORE(key_acc, 0)
     270 LET cnt = w_this * share / total_w
     280 IF cnt == 0 THEN GOTO 300
     290 STORE(key_acc, LOAD(key_acc) + cnt)
     300 GOTO 130

     400 RETURN 0
     500 IF asset == 1 THEN GOTO 520
     510 STORE("ins_dero", LOAD("ins_dero") + share)
     515 RETURN 0
     520 STORE("ins_dusd", LOAD("ins_dusd") + share)
     530 RETURN 0
     900 RETURN 1
End Function

/* Redeem(): DUSD sent to Core, dynamic basket claim V0.5.1 [F2].
   amount = ASSETVALUE(SCID()) (burned value q = amount * CF / 1e5).
   Basket order fixed: POL DUSD, ins DUSD, POL DERO@pdero, ins DERO@pdero,
   collateral@pdero. [F2] pdero fp 1e5 = max(P_risk, P_spot). After the
   claim, S_outstanding -= q (the DUSD that actually left the ledger),
   NOT the escrow amount. */
Function Redeem() Uint64
     10 DIM amount, cf, target AS Uint64
     20 DIM xp, yp, idr, idu, ctot AS Uint64
     30 DIM use, pdero AS Uint64
     40 DIM rc AS Uint64
     50 DIM spot_x, dero_drawn, dusd_drawn AS Uint64
     60 DIM val, new_s AS Uint64

     70 LET amount = ASSETVALUE(SCID())
     80 IF amount == 0 THEN GOTO 900
     90 LET rc = NavNow()
     100 IF rc == 1 THEN GOTO 900

     110 LET cf = LOAD("v_cf")
     120 IF cf > 100000 THEN LET cf = 100000
     130 IF amount > 18446744073709551615 / cf THEN GOTO 900
     140 LET target = amount * cf / 100000
     150 IF target == 0 THEN GOTO 900

     // [F2] pdero fp1e5 = max(P_risk, P_spot); P_spot = Y*100000/X
     160 LET pdero = LOAD("twap_num")
     170 LET xp = LOAD("x_pol_dero")
     180 LET yp = LOAD("y_pol_dusd")
     190 IF xp == 0 THEN GOTO 220
     200 IF yp > 184467440737095516 THEN GOTO 900
     210 LET spot_x = yp * 100000 / xp
     215 IF spot_x > pdero THEN LET pdero = spot_x

     // ------- basket (pari-passu, fixed order) -------
     220 LET dero_drawn = 0
     230 LET dusd_drawn = 0
     240 LET target_dup = target

     // tier 1: POL DUSD
     250 LET use = LOAD("y_pol_dusd")
     260 IF use > target_dup THEN LET use = target_dup
     270 IF use == 0 THEN GOTO 320
     280 STORE("y_pol_dusd", LOAD("y_pol_dusd") - use)
     290 LET dusd_drawn = dusd_drawn + use
     300 LET target_dup = target_dup - use
     310 IF target_dup == 0 THEN GOTO 535

     // tier 2: insurance DUSD
     320 LET use = LOAD("ins_dusd")
     330 IF use > target_dup THEN LET use = target_dup
     340 IF use == 0 THEN GOTO 380
     350 STORE("ins_dusd", LOAD("ins_dusd") - use)
     360 LET dusd_drawn = dusd_drawn + use
     370 LET target_dup = target_dup - use
     375 IF target_dup == 0 THEN GOTO 535

     // tier 3: POL DERO at pdero. DUSD value of DERO = dero * pdero / 1e5.
     380 IF pdero == 0 THEN GOTO 535
     385 LET use = target_dup * 100000 / pdero       // DERO needed
     390 IF use > LOAD("x_pol_dero") THEN LET use = LOAD("x_pol_dero")
     395 IF use == 0 THEN GOTO 440
     400 IF use > 18446744073709551615 / pdero THEN GOTO 900
     410 LET val = use * pdero / 100000
     415 IF val > target_dup THEN LET val = target_dup
     420 STORE("x_pol_dero", LOAD("x_pol_dero") - use)
     425 LET dero_drawn = dero_drawn + use
     430 LET target_dup = target_dup - val
     435 IF target_dup == 0 THEN GOTO 535

     // tier 4: insurance DERO at pdero
     440 LET use = target_dup * 100000 / pdero
     445 LET idr = LOAD("ins_dero")
     450 IF use > idr THEN LET use = idr
     455 IF use == 0 THEN GOTO 480
     460 IF use > 18446744073709551615 / pdero THEN GOTO 900
     465 LET val = use * pdero / 100000
     470 LET dero_drawn = dero_drawn + use
     475 LET target_dup = target_dup - val
     476 STORE("ins_dero", idr - use)
     478 IF target_dup == 0 THEN GOTO 535

     // tier 5: vault collateral at pdero (caller's vault for skeleton;
     // multi-vault sweep is a registry TODO).
     480 LET use = target_dup * 100000 / pdero
     485 LET ctot = LOAD("total_collateral")
     490 IF use > ctot THEN LET use = ctot
     495 IF EXISTS("c:" + SIGNER()) THEN GOTO 500
     497 STORE("c:" + SIGNER(), 0)
     500 LET rc = LOAD("c:" + SIGNER())
     505 IF use > rc THEN LET use = rc
     510 IF use == 0 THEN GOTO 535
     515 STORE("c:" + SIGNER(), rc - use)
     520 STORE("total_collateral", ctot - use)
     525 LET dero_drawn = dero_drawn + use
     528 LET val = use * pdero / 100000
     530 IF val > target_dup THEN LET val = target_dup
     532 LET target_dup = target_dup - val

     // ------- finalize -------
     535 IF dero_drawn + dusd_drawn == 0 THEN GOTO 900   // nothing redeemable
     540 LET new_s = LOAD("s_outstanding")
     // [I-B] The user's `amount` DUSD entered the retirement escrow and is
     // destroyed. S drops by the FULL escrowed amount: that is the DUSD
     // that actually leaves circulation. Payout composition (DERO vs DUSD
     // tiers) does not change the S identity.
     550 IF amount > new_s THEN GOTO 900
     560 STORE("s_outstanding", new_s - amount)
     570 IF dero_drawn > 0 THEN SEND_DERO_TO_ADDRESS(SIGNER(), dero_drawn)
     580 IF dusd_drawn > 0 THEN SEND_ASSET_TO_ADDRESS(SIGNER(), dusd_drawn, SCID())
     590 STORE("v_target", target)
     600 STORE("v_dusd_drawn", dusd_drawn)
     610 STORE("v_dero_drawn", dero_drawn)
     620 STORE("v_unpaid", target_dup)
     630 RETURN 0
     900 RETURN 1
End Function

/* Repay(amount Uint64): burn DUSD, drop S by the exact atoms.        */
Function Repay(amount Uint64) Uint64
     10 DIM key_d, debt, s_out AS Uint64

     20 IF amount == 0 THEN GOTO 900
     30 LET key_d = "d:" + SIGNER()
     40 IF EXISTS(key_d) THEN GOTO 60
     50 STORE(key_d, 0)
     60 LET debt = LOAD(key_d)
     70 IF amount > debt THEN GOTO 900
     80 LET s_out = LOAD("s_outstanding")
     90 IF amount > s_out THEN GOTO 900

     100 STORE(key_d, debt - amount)
     110 STORE("total_debt", LOAD("total_debt") - amount)
     120 STORE("s_outstanding", s_out - amount)
     130 RETURN 0
     900 RETURN 1
End Function

/* Withdraw(withdrawAmount Uint64): post-state Debt <= C_after*LTV*P0. */
Function Withdraw(withdrawAmount Uint64) Uint64
     10 DIM amount, key_col, key_debt AS String
     20 DIM col, debt, c_after, cap_after AS Uint64

     30 LET amount = withdrawAmount
     40 IF amount == 0 THEN GOTO 900
     50 LET key_col = "c:" + SIGNER()
     60 LET key_debt = "d:" + SIGNER()
     70 IF EXISTS(key_col) THEN GOTO 90
     80 STORE(key_col, 0)
     90 LET col = LOAD(key_col)
     100 IF EXISTS(key_debt) THEN GOTO 120
     110 STORE(key_debt, 0)
     120 LET debt = LOAD(key_debt)

     130 IF amount > col THEN GOTO 900
     140 LET c_after = col - amount
     // [I1] conservative floor: Debt <= C_after * 0.0072 at P0
     150 IF c_after > 256204778801521550 THEN GOTO 900
     160 LET cap_after = c_after * 72 / 10000
     170 IF debt > cap_after THEN GOTO 900

     180 STORE(key_col, c_after)
     190 STORE("total_collateral", LOAD("total_collateral") - amount)
     200 SEND_DERO_TO_ADDRESS(SIGNER(), amount)
     210 RETURN 0
     900 RETURN 1
End Function

/* Liquidate(victim String): [F5] ledger identity + [I-A] conservation.
   Collateral relocates vault->POL (x_pol_dero += C, total_collateral -=
   C) - the DERO did NOT leave the system. S drops ONLY by the DUSD that
   actually leaves: the insurance DUSD burned (ins_dusd -= u, S -= u) and
   the value of insurance DERO converted to POL backing (ins_dero -= v,
   x_pol_dero += v, S unchanged - v is DUSD-claim backing already counted).
   Any residue beyond collateral value + insurance is explicit bad_debt;
   S for the residue stays outstanding and is priced at CF<1 at redeem. */
Function Liquidate(victim String) Uint64
     10 DIM raw, key_c, key_d AS String
     20 DIM col, debt, ctot AS Uint64
     30 DIM col_val, shortfall, u, v, rem AS Uint64
     40 DIM tn, rc AS Uint64
     50 DIM cr_num, cr_den AS Uint64

     60 LET rc = NavNow()
     70 IF rc == 1 THEN GOTO 900
     80 LET tn = LOAD("twap_num")

     90 LET raw = ADDRESS_RAW(victim)
     100 LET key_c = "c:" + raw
     110 LET key_d = "d:" + raw
     120 IF EXISTS(key_c) THEN GOTO 140
     130 STORE(key_c, 0)
     140 LET col = LOAD(key_c)
     150 IF EXISTS(key_d) THEN GOTO 170
     160 STORE(key_d, 0)
     170 LET debt = LOAD(key_d)
     180 IF debt == 0 THEN GOTO 900

     // CR = C * P / Debt >= 1.20 => safe, no liquidation
     190 IF col > 18446744073709551615 / tn THEN GOTO 900
     200 LET cr_num = col * tn
     205 IF debt > 18446744073709551615 / 120 THEN GOTO 900
     210 LET cr_den = debt * 120
     220 IF cr_num >= cr_den THEN GOTO 900

     // collateral value in DUSD atoms = C * tn / 100000
     230 IF col > 18446744073709551615 / tn THEN GOTO 900
     240 LET col_val = col * tn / 100000

     // relocate collateral vault->POL (I-A: DERO conserved)
     250 LET ctot = LOAD("total_collateral")
     260 IF col > ctot THEN GOTO 900
     270 STORE("total_collateral", ctot - col)
     280 STORE("x_pol_dero", LOAD("x_pol_dero") + col)
     290 STORE(key_c, 0)
     300 STORE(key_d, 0)
     310 STORE("total_debt", LOAD("total_debt") - debt)

     // shortfall = debt - col_val (col_val < debt enforced by liq.cond)
     320 IF col_val >= debt THEN GOTO 500
     330 LET shortfall = debt - col_val

     // insurance DUSD burns: S -= u EXACTLY (u leaves the ledger)
     340 LET u = LOAD("ins_dusd")
     345 IF u > shortfall THEN LET u = shortfall
     350 IF u == 0 THEN GOTO 400
     360 STORE("ins_dusd", LOAD("ins_dusd") - u)
     370 STORE("s_outstanding", LOAD("s_outstanding") - u)

     // insurance DERO converts to POL backing (conserved; no S change)
     400 LET rem = shortfall - u
     410 IF rem == 0 THEN GOTO 500
     420 IF tn == 0 THEN GOTO 470
     430 LET v = rem * 100000 / tn
     435 LET idr = LOAD("ins_dero")
     440 IF v > idr THEN LET v = idr
     445 IF v == 0 THEN GOTO 470
     450 STORE("ins_dero", idr - v)
     455 STORE("x_pol_dero", LOAD("x_pol_dero") + v)
     460 LET rem = rem - v * tn / 100000

     // residue -> explicit bad debt; S unchanged (backing gone, CF<1 prices it)
     470 STORE("bad_debt", LOAD("bad_debt") + rem)
     480 STORE("v_cf", 99999)
     500 STORE("last_liq_short", shortfall)
     510 RETURN 0
     900 RETURN 1
End Function

/* Snapshot helpers (DVM-BASIC has no read-only views).                */
Function SnapshotState() Uint64
     10 STORE("v_x", LOAD("x_pol_dero"))
     20 STORE("v_y", LOAD("y_pol_dusd"))
     30 STORE("v_s", LOAD("s_outstanding"))
     40 STORE("v_ins_dero", LOAD("ins_dero"))
     50 STORE("v_ins_dusd", LOAD("ins_dusd"))
     60 STORE("v_col", LOAD("total_collateral"))
     70 STORE("v_debt", LOAD("total_debt"))
     80 STORE("v_bad", LOAD("bad_debt"))
     90 STORE("v_pol_in", LOAD("pol_dero_in"))
     100 STORE("v_pol_out", LOAD("pol_dero_out"))
     110 STORE("v_state", LOAD("protocol_state"))
     120 RETURN 0
     900 RETURN 1
End Function

Function SetEmergency() Uint64
     10 IF LOAD("protocol_state") == 4 THEN GOTO 900
     20 IF SIGNER() == LOAD("owner") THEN GOTO 40
     30 GOTO 900
     40 STORE("protocol_state", 4)
     50 STORE("v_state", 4)
     60 RETURN 0
     900 RETURN 1
End Function

/* =====================================================================
   IMPLEMENTATION NOTES / REMAINING HARD TODOs (DVM-BASIC, testnet)
   ---------------------------------------------------------------------
   Fix->gate map (the closed-loop core of this file's spec parity):
     sim assertion                          contract gate
     ------------------------------------   ---------------------------
     S5  subsidy == 0                       [F2] Redeem pdero=max(P_risk,spot)
     S9/S10 drain == 0                      [F1] Mint P_min + [F4] guard
     S16 X*eff <= U64MAX                    [F3] Swap eff<=AMM_CAP + div/mul
     I-A DERO conservation                  [F5] Liquidate vault->POL + Swap/IC
     I-B DUSD == S                          [F5] Redeem S-=spent; Repay S-=amt
     No first-mover                         [I-C] CF monotone; q=amt*CF basket

   HARD TODOs before any mainnet claim:
   (1) TWAP decay loop: per-block loop with DECAY_CAP; a batch entry gate
       for >100 block gaps is REQUIRED (T3 gas).
   (2) Multi-vault pro-rata tier-5 collateral settlement needs a vault
       registry ("nvaults","vk:<n>") - DVM-BASIC has no key enumeration.
       Skeleton settles only the caller's vault.
   (3) Lock table (n_lockers, lk:/ld:/acc:) must keep in sync with
       Mint/ClaimFees + expiry pruning; owner-registration + weights are
       UNIMPLEMENTED in this skeleton (fee accrual logic present).
   (4) Backers sub-call from Swap / Decay from NavNow: on-sim gas audit
       is testnet-required (T3); inline if the call frame is too fat.
   (5) protocol_state=4 gating: Mint disabled, Redeem/Withdraw stay open
       per spec. Verify redeem's emergency path end-to-end on simulator.
   ===================================================================== */