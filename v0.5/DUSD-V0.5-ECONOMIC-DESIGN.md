# DUSD V0.5 — Economic Design
## Dynamic Redemption + Unified Backing Claim
Research/Testnet only.

### Core decision
**1 DUSD is a pari-passu senior claim on the unified backing base:**
- POL
- Insurance
- Eligible vault collateral

V0.5 uses **dynamic redemption**, not fixed 100 DERO/DUSD redemption.

### 1. Genesis / Phase-1 issuance
P0 = 0.01 DUSD/DERO (Genesis issuance reference only; NOT a permanent floor).

gross_mint = C * P0 * LTV
LTV = 0.72

POL_DUSD = gross_mint * 0.0025
POL_DERO = POL_DUSD / P_spot

vault_after = C - POL_DERO

The POL DERO is a real share of the user's deposit. No DERO is created.

Existing debt does not reprice when market price changes.

### 2. Endogenous price
X = POL DERO
Y = POL DUSD
P_spot = Y / X
P_risk = internal block-anchored TWAP

No external DERO price oracle is used for internal price discovery.

### 3. Unified backing NAV
C = eligible vault DERO
X = POL DERO
Y = POL DUSD
I_DERO = insurance DERO
I_DUSD = insurance DUSD
H = collateral liquidity haircut

Research default H = 0.90

POL_NAV = Y + P_risk*X
Insurance_NAV = I_DUSD + P_risk*I_DERO
Collateral_NAV = H*P_risk*C

Backing_NAV = POL_NAV + Insurance_NAV + Collateral_NAV

### 4. Claim coverage
S = outstanding DUSD

Coverage = Backing_NAV / S
ClaimFactor = min(1, Coverage)

For redemption q:
RedeemValue = q * ClaimFactor

If Coverage >= 1, the target claim is fully backed.
If Coverage < 1, every DUSD receives the same proportional haircut.

No first-mover advantage.

### 5. Dynamic redemption
Redemption is a basket claim.

Settlement order:
1. liquid DUSD (POL + insurance)
2. liquid DERO (POL + insurance), valued at P_risk
3. mobilized/liquidated eligible collateral as permitted by the risk engine

Never transfer more assets than controlled.
Burn/reduce the redeemed DUSD before final accounting.
Residual under-collateralization is explicit system loss, not hidden token creation.

### 6. Solvency gate
Research default:
MIN_COVERAGE = 1.00

New mint is rejected if proposed backing would violate the required coverage.

Recommended later production candidates: 1.10 or 1.20.

### 7. Liquidation
LIQ_CR = 1.20

Risk decisions use internal TWAP, not spot.
Insurance absorbs bounded losses before explicit bad-debt recognition.

### 8. POL
POL = liquidity + price discovery + part of the backing claim.
POL is NOT the whole solvency reserve.

Swap fee target:
70% pool depth
10% POL growth
15% active backers
5% insurance

Fees stay in native units: DERO fees remain DERO; DUSD fees remain DUSD.

### 9. POL-origin DERO
DERO purchased from POL cannot directly become fresh mint collateral.
A cooldown alone is only rate limiting; the preferred rule is provenance-based exclusion from fresh collateral.

### 10. Lock / weight
u = cumulative mint pressure / POL value

T(u) = 30 + 1065*u^1.05977 / (u^1.05977 + 0.550857^1.05977)
bounded 30..1095 days.

POL fee weight:
W_i = LockedDERO_i * CommittedLockDays_i

Weight is constant during the commitment and becomes inactive at expiry.
Previously accrued fees remain claimable.

### 11. Fundamental limitation
Dynamic redemption can guarantee:
- pari-passu claims
- no payout above controlled assets
- explicit haircut when undercollateralized
- no hidden DERO/DUSD creation

It cannot guarantee an external $1 fiat redemption through an arbitrary 90–99% DERO collapse without an independent reserve/capital source.

### 12. Acceptance target
The final V0.5 engine must survive:
Genesis, POL accounting, recursion, Sybil split, fee accounting,
pump/dump, 25–99% crashes, 100% redemption attempt,
insurance depletion, POL depletion, TWAP manipulation, 100k fuzz,
and 50k heavy-tail Monte Carlo.
