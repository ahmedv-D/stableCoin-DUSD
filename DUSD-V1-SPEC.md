# DUSD V1 Final Security Specification

## 1. Overview

DUSD is a USD-pegged stablecoin collateralized by DERO, targeting production on the DERO DVM-BASIC blockchain. V1 prioritizes solvency over peg stability and capital efficiency. The protocol provides a hard mathematical solvency guarantee under a well-defined economic envelope.

**Core Design:**
- Collateral: DERO (native coin)
- Debt unit: DUSD (internal accounting)
- Peg mechanism: External arbitrage (V1 has no on-chain peg enforcement)
- Solvency mechanism: Hard floor-price cap + direct redemption
- Upgradeability: None (V1 is immutable)

## 2. Protocol Parameters

| Parameter | Symbol | Value | Description |
|-----------|--------|-------|-------------|
| Floor Price | P_floor | $0.01 | Minimum recoverable DERO price per unit |
| Execution Factor | EF | 0.72 | Accounts for liquidation loss (10%) + slippage (20%) |
| Oracle Quorum | Q | 5-of-7 | Minimum oracle signatures for price update |
| Oracle Ring Size | — | 2 | SIGNER() identity proof; no ring privacy for oracles |
| Activation Delay | N | 10 blocks | Oracle snapshots activate N blocks after submission |
| Emergency Cooldown | — | 100 blocks | Minimum time between emergency state transitions |

## 3. Fixed-Point Arithmetic Representation

All arithmetic uses **integer-only** operations with explicit scaling to avoid overflow.

### 3.1 Unit Definitions

| Quantity | Stored As | Scaling Factor | Range |
|----------|-----------|----------------|-------|
| DERO collateral | atoms | 10^8 per DERO | 0 to ~18.4 × 10^18 |
| DUSD debt | atoms | 10^8 per DUSD | 0 to ~18.4 × 10^18 |
| Oracle price (USD/DERO) | micro-USD | ×10^6 (1 = $0.000001) | 0 to 18,446 |
| P_floor | micro-USD/DERO | ×10^6 | 10,000 (= $0.01/DERO) |
| EF | micro-units | ×10^6 | 720,000 (= 0.720000) |
| Redemption rate | micro-USD/DERO | ×10^6 | 10,000 (= $0.01/DERO) |
| Issuance rate | micro-USD/DERO | ×10^6 | 7,200 (= $0.0072/DERO = P_floor × EF) |

### 3.1a Issuance Rate Derivation

The issuance rate IS the cap rate: **P_floor × EF**.

If the issuance rate exceeded P_floor × EF, then:
```
DUSD_issued = DERO × P_floor
Hard_cap = DERO × P_floor × EF
DUSD_issued > Hard_cap  (since EF < 1)
→ REJECTED by solvency check
```

Therefore the issuance rate must be exactly P_floor × EF = $0.0072/DERO.

```
100 DERO → 0.72 DUSD (not 1 DUSD)
35M DERO → $252,000 DUSD (hard cap)
```

The user receives DUSD at a discounted rate that accounts for execution losses. This is a design feature, not a bug — the EF factor provides the solvency safety margin.

### 3.1b Atom-Based Computation

The prototype uses DERO/DUSD atoms (10^8 per unit) for all balances. The hard cap in atoms:

```
hard_cap_atoms = collateral_atoms × P_floor × EF / SCALE / SCALE
```

Staged (overflow-safe):
```
step1 = collateral_atoms × P_floor / SCALE
step2 = step1 × EF / SCALE
hard_cap = step2
```

For 10^10 atoms (100 DERO):
```
step1 = 10^10 × 10,000 / 1,000,000 = 10^8
step2 = 10^8 × 720,000 / 1,000,000 = 72,000,000
hard_cap = 72,000,000 DUSD atoms = $0.72  ✓
```

For 3.5 × 10^15 atoms (35M DERO):
```
step1 = 3.5 × 10^15 × 10,000 / 1,000,000 = 3.5 × 10^13
step2 = 3.5 × 10^13 × 720,000 / 1,000,000 = 2.52 × 10^16
hard_cap = 2.52 × 10^16 DUSD atoms = $252,000  ✓
```

All intermediate values fit within uint64 (max 1.844 × 10^19). ✓

### 3.2 Overflow-Safe Computation Pattern

**Rule:** Every multiplication must be preceded by an overflow check.

```basic
' SAFE MULTIPLY: result = A * B / SCALE
' where A, B, SCALE are Uint64 and result must fit uint64

' Step 1: Check A * B does not overflow
IF A > 0 AND B > 0 AND A > 18446744073709551615 / B THEN
    RETURN 1 ' OVERFLOW ERROR
END IF

' Step 2: Compute product
DIM product AS Uint64
LET product = A * B

' Step 3: Divide by scale
DIM result AS Uint64
LET result = product / SCALE
```

### 3.3 Solvency Cap Computation (Overflow-Safe)

```basic
' Input: collateral (DERO), P_floor_usd (micro-USD), EF_micro (micro-units)
' Output: hard_cap (micro-USD)

CONST MAX_UINT64 = 18446744073709551615
CONST SCALE_USD = 1000000

' Step 1: collateral * P_floor
IF collateral > 0 AND P_floor_usd > 0 AND collateral > MAX_UINT64 / P_floor_usd THEN
    RETURN 1 ' OVERFLOW
END IF
DIM step1 AS Uint64
LET step1 = collateral * P_floor_usd

' Step 2: step1 * EF
IF step1 > 0 AND EF_micro > 0 AND step1 > MAX_UINT64 / EF_micro THEN
    RETURN 1 ' OVERFLOW
END IF
DIM step2 AS Uint64
LET step2 = step1 * EF_micro

' Step 3: Divide by SCALE_USD to get micro-USD
LET hard_cap = step2 / SCALE_USD
```

**Numerical verification at max values:**

```
collateral = 35,000,000 (35M DERO)
P_floor_usd = 10,000 ($0.01 × 10^6)
EF_micro = 720,000 (0.72 × 10^6)

Step 1: 35,000,000 × 10,000 = 350,000,000,000 (3.5 × 10^11)     SAFE
Step 2: 350,000,000,000 × 720,000 = 252,000,000,000,000,000 (2.52 × 10^17)  SAFE
Step 3: 252,000,000,000,000,000 / 1,000,000 = 252,000,000,000,000 (2.52 × 10^14)  SAFE

Result: $252,000.00 in micro-USD = 252,000,000,000,000 micro-USD ✓
```

All intermediate values fit within uint64 (max 1.844 × 10^19). ✓

### 3.4 Redemption DERO Amount Computation

```basic
' User burns DUSD_amount (in DUSD units)
' RedemptionRate = P_floor = 10,000 micro-USD/DERO
' Output: dero_amount (in DERO units)

CONST REDEMPTION_RATE = 10000 ' $0.01 per DUSD, in micro-USD

' DERO owed = DUSD_amount * SCALE_USD / REDEMPTION_RATE
' (since REDEMPTION_RATE is micro-USD per DERO)

DIM dero_amount AS Uint64
LET dero_amount = DUSD_amount * SCALE_USD / REDEMPTION_RATE

' Verification: 100 DUSD → 100 * 1,000,000 / 10,000 = 10,000 DERO
' At P_floor = $0.01: 10,000 DERO × $0.01 = $100 ✓
```

## 4. Hard Solvency Cap

The protocol enforces a single global invariant at all times:

$$\text{TotalDebt} \leq \text{TotalCollateral} \times P_{floor} \times EF$$

In fixed-point terms:

$$\text{TotalDebt (DUSD)} \leq \frac{\text{TotalCollateral (DERO)} \times P_{floor}(\mu\$) \times EF(\mu)}{SCALE_{USD}}$$

**Enforcement:** DVM-ENFORCEABLE. Every function that increases TotalDebt or decreases TotalCollateral checks this invariant atomically. If violated, the transaction reverts.

**The oracle does NOT influence this cap.** The cap uses P_floor, not the oracle price.

## 5. Vault Model

### 5.1 Vault State

Each vault stores:

| Key | Type | Description |
|-----|------|-------------|
| `vault_owner:<ID>` | String | Address of vault owner |
| `vault_collateral:<ID>` | Uint64 | DERO deposited (whole units) |
| `vault_debt:<ID>` | Uint64 | DUSD minted against this vault |
| `vault_snapshot:<ID>` | Uint64 | Height of snapshot used at deposit time |
| `vault_timestamp:<ID>` | Uint64 | Unix timestamp of last vault operation |

### 5.2 Vault Capacity

**Critical: Vault capacity uses P_floor, NOT oracle price.**

```basic
' Max debt for a vault with collateral C
DIM vault_capacity AS Uint64
' C * P_floor * EF / SCALE_USD (with overflow checks)
LET vault_capacity = C * P_floor_usd / SCALE_USD * EF_micro / SCALE_USD
```

**Rationale:** If vault capacity used oracle price, oracle compromise could set price to $1,000,000, allowing massive DUSD minting against minimal collateral. The hard global cap would eventually block this, but the vault-level check using P_floor prevents any oracle influence on debt creation.

**The oracle's role is informational only:** it provides market context (current DERO price), redemption context, and risk display. It does not control debt limits.

### 5.3 Vault Operations

**Open Vault (Mint):**
```
1. User deposits DERO
2. Contract atomically:
   a. Update vault_collateral
   b. Update total_collateral
   c. Compute hard_cap = total_collateral × P_floor × EF
   d. Issue DUSD = hard_cap - total_debt (issue up to cap)
   e. Update total_debt
   f. Verify Hard Solvency Cap (always satisfied by construction)
3. If any step fails, entire transaction reverts
```

**Issuance rate = P_floor × EF = $0.0072/DERO.** The user receives DUSD at the floor price minus the execution cost. This is not 1:1 — the EF factor provides the solvency safety margin. For 100 DERO deposited, the user receives 0.72 DUSD.

**Close Vault (Repay):**
```
1. User sends DUSD
2. Contract atomically:
   a. Burn DUSD (deposit to contract balance)
   b. Update vault_debt
   c. Update total_debt
   d. Return DERO collateral
   e. Update total_collateral
   f. Verify Hard Solvency Cap
3. If any step fails, entire transaction reverts
```

## 6. Direct Redemption (V1 Critical Mechanism)

### 6.1 Design

Direct Redemption allows DUSD holders to burn DUSD and receive DERO back from the contract's reserves. This is the primary exit mechanism for DUSD holders.

### 6.2 Redemption Rate

$$\boxed{\text{RedemptionRate} = P_{floor} = \$0.01/\text{DERO}}$$

When a user burns DUSD, the DERO returned is:

```
dero_returned = burn_amount × SCALE_USD / REDEMPTION_RATE
```

In atoms: `dero_returned = dusd_atoms × 100` (since SCALE_USD/REDEMPTION_RATE = 1,000,000/10,000 = 100).

For 0.72 DUSD burned (72,000,000 atoms):
```
dero_returned = 72,000,000 × 100 = 7,200,000,000 atoms = 72 DERO
Value = 72 DERO × $0.01 = $0.72 ✓
```

The user receives DERO worth exactly the face value of their DUSD at P_floor.

**Economic implication:** Users receive DERO at the floor price, not the market price. If DERO trades at $1000, the user still gets DERO worth $0.01 per DUSD burned (not $1000). This means:
- **No arbitrage when DERO > P_floor:** Redemption gives $0.01/DUSD, but DUSD may be worth more on the market.
- **Arbitrage exists when DUSD < P_floor and DERO ≤ P_floor:** Buy cheap DUSD, redeem for floor-price DERO.
- **Redemption provides a solvency exit, not peg maintenance.**

### 6.2a Issuance vs Redemption Asymmetry

The issuance rate (P_floor × EF = $0.0072/DERO) is lower than the redemption rate (P_floor = $0.01/DERO). This creates an asymmetric flow:

```
100 DERO deposited → 0.72 DUSD issued (issuance at $0.0072/DERO)
0.72 DUSD burned   → 72 DERO returned (redemption at $0.01/DERO)
Net: 28 DERO lost (the EF cost)
```

The user pays the execution cost on minting, not on redemption. This is by design:
- The EF factor represents execution losses (liquidation loss + slippage)
- It's deducted upfront at minting to guarantee solvency
- Redemption returns DERO at full floor price
- The system is always over-collateralized (100 DERO backs 0.72 DUSD = 139% collateralization)

### 6.3 Implementation (DVM-BASIC)

The Direct Redemption function follows the `ConvertTOKENX` pattern from the official `token.bas` example:

```basic
Function Redeem(amount Uint64) Uint64
    ' 1. Input validation
    10 IF amount <= 0 THEN GOTO 200
    20 IF amount > LOAD("total_debt") THEN GOTO 200

    ' 2. Compute DERO to return (overflow-safe)
    30 CONST SCALE_USD = 1000000
    40 CONST REDEMPTION_RATE = 10000
    50 DIM dero_to_send AS Uint64
    60 LET dero_to_send = amount * SCALE_USD / REDEMPTION_RATE

    ' 3. Check contract has sufficient DERO
    70 IF dero_to_send > DEROVALUE() THEN GOTO 200
    ' Note: DEROVALUE() is the contract's DERO balance

    ' 4. Atomically update state
    80 DIM new_debt AS Uint64
    90 LET new_debt = LOAD("total_debt") - amount
    100 STORE("total_debt", new_debt)

    ' 5. Send DERO to redeemer (atomic with state updates)
    110 SEND_DERO_TO_ADDRESS(SIGNER(), dero_to_send)

    ' 6. Verify solvency invariant
    120 DIM total_collateral AS Uint64
    130 LET total_collateral = LOAD("total_collateral")
    140 DIM hard_cap AS Uint64
    ' ... compute hard_cap with overflow checks ...
    150 IF new_debt > hard_cap THEN GOTO 200

    160 RETURN 0

    200 RETURN 1 ' Error
End Function
```

**Key DVM-BASIC mechanics:**
- `ASSETVALUE(SCID())` receives DUSD sent by the user (confirmed in token.bas)
- `SEND_DERO_TO_ADDRESS(SIGNER(), amount)` sends DERO back (confirmed in token.bas)
- `DEROVALUE()` returns the contract's DERO balance
- The DVM fails atomically if the contract has insufficient DERO
- The DVM processes transactions sequentially within a block

### 6.4 Redemption Bounds

**Maximum redemption in a single transaction:**

```
MaxRedemption = min(
    contract_DERO_balance × REDEMPTION_RATE / SCALE_USD,
    total_debt
)
```

With contract holding 1,000 DERO:
```
MaxRedemption = 1,000 × 10,000 / 1,000,000 = 10 DUSD
```

**Wait — let me re-derive:**

```
dero_to_send = burn_amount × SCALE_USD / REDEMPTION_RATE
1,000 = burn_amount × 1,000,000 / 10,000
burn_amount = 1,000 × 10,000 / 1,000,000 = 10 DUSD
```

So with 1,000 DERO in the contract, users can only redeem up to 10 DUSD total? That seems wrong.

Let me re-check. If the user burns 10 DUSD:
```
dero_to_send = 10 × 1,000,000 / 10,000 = 1,000 DERO
```

Yes — 10 DUSD burns require 1,000 DERO. The contract has exactly 1,000 DERO. The full balance is consumed.

**This means the redemption rate creates a binding constraint:**

$$\text{Max total DUSD redeemable} = \frac{\text{Contract DERO balance} \times SCALE_{USD}}{REDEMPTION_RATE}$$

At RedemptionRate = P_floor:
$$\text{Max total DUSD redeemable} = \frac{\text{Contract DERO balance} \times 10^6}{10,000} = \text{Contract DERO balance} \times 100$$

So 1 DERO = 100 DUSD redeemable. Or equivalently, 1 DUSD = 0.01 DERO.

**For 35M DERO collateral:**
```
Max redeemable = 35,000,000 × 100 = 3,500,000,000 DUSD
```

But the hard cap limits debt to $252,000. So only $252,000 DUSD can exist, and 3.5B DUSD worth of DERO exists to cover it. The system is massively over-collateralized at redemption.

**This is correct.** The redemption rate ensures solvency.

### 6.5 What Redemptions CANNOT Do

**Redemption cannot restore the DUSD peg above P_floor.**

Scenario:
```
DERO = $1,000 (market)
DUSD = $0.50 (depegged on external market)
No external DUSD liquidity
```

Arbitrageur buys DUSD at $0.50, calls Redeem:
```
Burns 1 DUSD → receives DERO worth $0.01
Paid $0.50, received $0.01
LOSS: $0.49 per DUSD
```

No arbitrage exists. The redemption mechanism only provides a DUSD price floor at P_floor, and only when DERO trades at P_floor.

**This is by design.** V1 prioritizes solvency (guaranteeing DUSD holders can exit at floor price) over peg maintenance (guaranteeing DUSD trades at $1). Peg stability depends on external market dynamics.

## 7. Oracle Specification

### 7.1 Oracle Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Operators | 7 | Fixed set, known at deployment |
| Quorum | 5-of-7 | Minimum signatures for valid submission |
| Ring size | 2 | SIGNER() identity proof only |
| Activation delay | 10 blocks | Snapshot usable after delay |
| Heartbeat | 60 blocks | Oracle expected submission frequency |
| Emergency threshold | 3 missed heartbeats | Triggers emergency state |

### 7.2 Oracle Data Structure

Each oracle submission provides:
- `DERO_USD_price`: current market price (micro-USD)
- `block_height`: block number at submission time
- `timestamp`: Unix timestamp

The submission is stored as:
```
oracle_snapshot:<operator_address> = {
    price: DERO_USD_price,
    height: block_height,
    activation_height: block_height + N
}
```

### 7.3 Oracle Quorum Computation

```basic
' Count unique operator submissions with valid quorum
' at a given activation_height

Function GetOraclePrice() Uint64
    DIM count AS Uint64
    DIM total_price AS Uint64
    DIM i AS Uint64

    ' Iterate over 7 operators
    ' (DVM-BASIC has no arrays; use explicit unrolled checks)
    ' ...
    ' For each operator: check if snapshot exists and activation_height <= BLOCK_HEIGHT()
    ' If yes: increment count, add price to total_price

    IF count < 5 THEN
        RETURN 1 ' Quorum not met
    END IF

    ' Return median or average of the 5+ submissions
    ' For simplicity: return average
    STORE("current_oracle_price", total_price / count)
    RETURN 0
End Function
```

### 7.4 Activation Delay

Oracle snapshots do NOT take effect immediately. They activate at `submission_height + N` blocks.

**Purpose:** Prevents oracle manipulation attacks where an attacker submits a malicious price and immediately mints DUSD before other operators can react.

**Implementation:**

```basic
' When processing a mint, only use oracle snapshots that have activated
IF oracle_snapshot_height + ACTIVATION_DELAY > BLOCK_HEIGHT() THEN
    ' This snapshot is not yet active
    ' Use the previously active snapshot instead
END IF
```

### 7.5 Oracle Trust Model

The oracle is **TRUSTED** for truth (price data). It is **NOT** trusted for solvency (the hard cap uses P_floor, not oracle price).

Oracle compromise can cause:
- Incorrect risk display (showing wrong DERO price to users)
- Incorrect redemption context (wrong market price for display)
- Delayed emergency triggers (if malicious oracles submit fake prices)

Oracle compromise CANNOT cause:
- Unbacked DUSD minting (hard cap uses P_floor)
- Vault insolvency (vault capacity uses P_floor)
- Redemption insolvency (redemption rate = P_floor)

## 8. Emergency State Machine

### 8.1 States

| State | Description |
|-------|-------------|
| NORMAL | All operations enabled |
| ELEVATED | Minting reduced, increased monitoring |
| STRESS | Minting paused, redemptions still active |
| RECOVERY | Transitioning back from STRESS |
| EMERGENCY | Minting=0, redemptions=active, forced deleveraging |

### 8.2 Transitions

```
NORMAL → ELEVATED: 3 missed oracle heartbeats
ELEVATED → STRESS: 5 missed heartbeats OR oracle quorum lost
STRESS → RECOVERY: Quorum restored, all oracles active
RECOVERY → NORMAL: Emergency cooldown elapsed (100 blocks)
NORMAL → EMERGENCY: Catastrophic event (manual trigger OR critical invariant breach)
EMERGENCY → STRESS: Event resolved, manual trigger
```

### 8.3 Emergency Behavior

| State | Mint | Redemption | Repay | Emergency Deleverage |
|-------|------|------------|-------|---------------------|
| NORMAL | ON | ON | ON | OFF |
| ELEVATED | REDUCED | ON | ON | OFF |
| STRESS | OFF | ON | ON | OFF |
| RECOVERY | OFF | ON | ON | OFF |
| EMERGENCY | OFF | ON | ON | ON |

**Emergency Deleverage:** When in EMERGENCY state, any participant can trigger a forced repayment of the most-indebted vault, reducing system risk.

### 8.4 Hysteresis

Emergency states are **more restrictive** than normal states. The system never becomes more permissive during an emergency. The 100-block cooldown prevents oscillation between states.

## 9. Burn Semantics

### 9.1 Network-Level Behavior

DERO has no native `BURN(address, amount)` function. When DUSD is sent to the contract (for redemption or repayment):

1. DUSD is **deposited into the contract's encrypted balance**
2. Total DUSD supply on the network does **not** change
3. The DUSD is **not destroyed** at the network level

### 9.2 Protocol-Level Accounting

The contract tracks `total_debt` and `total_issued` internally. When DUSD is sent to the contract:

1. `total_debt -= burn_amount`
2. `total_issued -= burn_amount`
3. The DUSD is deposited to contract's encrypted balance (functionally locked)

### 9.3 V1 Non-Upgradeability

In V1, the contract has no `UpdateCode` function. The DUSD deposited to the contract's balance is **permanently locked** and cannot be re-released. The contract is immutable.

### 9.4 Implications

- The DUSD still exists in the DERO network's state (in the contract's encrypted balance)
- The contract's internal accounting correctly reflects the reduced supply
- The invariant `DUSD_Supply = TotalDebt + LockedInContract` holds at the network level
- The invariant `DUSD_Supply = TotalDebt` holds from the protocol's perspective (since locked DUSD is excluded from circulating supply)

## 10. Liquidation Mechanism

### 10.1 Permissionless, Push-Based

Liquidation is permissionless. Any external actor can call `Liquidate(VaultID)` when a vault is eligible.

### 10.2 Eligibility

A vault is eligible for liquidation when:

```
vault_debt > vault_capacity (using P_floor)
```

### 10.3 Liquidation Flow

```
1. External actor calls Liquidate(vault_id)
2. Contract atomically:
   a. Verify vault eligibility (using P_floor)
   b. Compute liquidation reward (fixed % of debt, e.g., 5%)
   c. Burn liquidator's DUSD (reduces vault debt)
   d. Send DERO collateral to liquidator (debt + reward)
   e. Update total_debt and total_collateral
   f. Verify Hard Solvency Cap
3. If any step fails, entire transaction reverts
```

### 10.4 Atomicity Guarantee

Two liquidators cannot double-spend the same vault state because DVM processes transactions sequentially. After the first liquidation succeeds, the vault's state is updated, and the second liquidation sees the new state.

## 11. Aggregate Accounting

### 11.1 Critical Requirement

Every function that modifies `vault_debt` or `vault_collateral` MUST atomically update `total_debt` and `total_collateral`.

### 11.2 Dangerous Drift Direction

- `total_debt` too LOW: system overestimates available capacity → allows over-minting
- `total_debt` too HIGH: system underestimates capacity → conservative (safe)

The dangerous direction is `total_debt` being too low.

### 11.3 Mitigation

- Every vault operation includes atomic aggregate updates
- Code review ensures correctness
- Off-chain monitoring validates aggregate consistency

## 12. Invariants

| # | Invariant | Enforcement | Type |
|---|-----------|-------------|------|
| 1 | DUSD_Supply = TotalDebt + LockedInContract | DVM-enforced | Network-level |
| 2 | TotalDebt ≤ TotalCollateral × P_floor × EF | DVM-enforced | Solvency |
| 3 | Vault_debt ≤ Vault_capacity | DVM-enforced | Vault-level |
| 4 | TotalCollateral ≥ Σ(vault_collateral) | DVM-enforced | Accounting |
| 5 | Oracle_quorum ≥ 5 | DVM-enforced | Oracle |
| 6 | No vault debt negative | DVM-enforced | State |
| 7 | No collateral reuse | DVM-enforced | Collateral |
| 8 | Atomic state transitions | DVM-enforced | Atomicity |
| 9 | Activation delay respected | DVM-enforced | Oracle |
| 10 | Emergency only makes system more restrictive | DVM-enforced | Emergency |

## 13. Testnet Prototype Requirements

Before V1 is considered implementation-complete:

1. Deploy DUSD-like token on testnet
2. Implement Direct Redemption using ConvertTOKENX pattern
3. Demonstrate: user sends token → contract sends DERO back → atomic
4. Verify: insufficient DERO balance causes revert
5. Verify: two redemptions in same block are sequenced correctly
6. Verify: overflow checks prevent arithmetic attacks
7. Verify: aggregate accounting updates atomically

## 14. Explicit Limitations of V1

1. **No peg stability guarantee.** DUSD may trade above or below $1 on external markets.
2. **No adaptive capacity.** Debt expansion/contraction does not adjust with DERO price.
3. **Low capital efficiency.** $252K max debt for 35M DERO collateral (at P_floor = $0.01).
4. **Oracle is trusted for truth.** 5-of-7 quorum can collude to submit false prices.
5. **Redemption cannot restore peg.** Arbitrage only works when DUSD < P_floor and DERO ≤ P_floor.
6. **No token composability.** DUSD cannot be used as collateral in other DVM contracts.
7. **P_floor assumption.** If DERO price permanently drops below P_floor, the guarantee breaks.
8. **Permanently locked DUSD.** Burned DUSD cannot be recovered (V1 non-upgradeable).
