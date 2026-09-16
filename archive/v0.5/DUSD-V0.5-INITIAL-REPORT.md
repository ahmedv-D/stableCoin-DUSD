# DUSD V0.5 — Build 1 Result

**Decision:** Dynamic redemption.

**Definition of 1 DUSD:** a pari-passu senior claim on the unified backing NAV made of:
POL + Insurance + eligible Vault Collateral.

**Phase-1 issuance:** P0=0.01 DUSD/DERO and LTV=72%; P0 is Genesis issuance price only, not a permanent floor.

## Genesis example
Bootstrap POL = 100,000 DERO + 1,000 DUSD.
New deposit = 1,000,000 DERO.

Gross mint = 7200.00 DUSD
POL DUSD = 18.00
POL DERO = 1800.00
User DUSD = 7182.00
New vault collateral = 998200.00 DERO

The deposit is conserved exactly:
vault collateral + new POL DERO contribution = 1,000,000 DERO.

## Dynamic claim
Backing NAV = POL NAV + Insurance NAV + 0.90 × eligible collateral NAV.

Coverage = Backing NAV / outstanding DUSD.
ClaimFactor = min(1, Coverage).
Redeem target for q DUSD = q × ClaimFactor.

At the example's Genesis risk price, coverage = 1.534364.
At a 90% price crash to 0.001 DUSD/DERO, coverage = 0.281005.

If coverage is below 1, every DUSD receives the same haircut. There is no privileged first redeemer.

## Tests
[32m.[0m[32m.[0m[32m.[0m[32m.[0m[32m                                                                     [100%][0m
[32m[32m[1m4 passed[0m[32m in 0.04s[0m[0m

## Open work
The next build must add:
- full liquidation + insurance state transitions
- many-redeemer pari-passu redemption tests
- native-asset fee accounting
- block TWAP
- immutable POL-origin provenance
- integer-safe DVM arithmetic
- 100k fuzz + 50k heavy-tail Monte Carlo
- integration with the existing DUSD repository
