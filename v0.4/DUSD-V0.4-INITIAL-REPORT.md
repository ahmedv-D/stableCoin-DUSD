# DUSD V0.4 — Build 1 Test Report

Status: research/testnet-only. No deployment or production changes.

Genesis:
- 1,000,000 DERO deposited
- P0=0.01 DUSD/DERO
- LTV=72%
- Gross mint=7200.00 DUSD
- POL DUSD=18.00
- POL DERO=1800.00
- User DUSD=7182.00

Anti-split:
- one-shot lock=966.86 days
- 100-part same-signer lock=966.86 days
- status=PASS

Recursive POL provenance boundary:
- POL-acquired DERO cannot be fed back into the mint API in this model.
- status=PASS at model boundary; on-chain provenance/cooldown is mandatory.

Crash ladder:
- 10% crash: price=0.009000, CR=1.2509, SAFE
- 25% crash: price=0.007500, CR=1.0424, LIQUIDATABLE
- 50% crash: price=0.005000, CR=0.6949, LIQUIDATABLE
- 75% crash: price=0.002500, CR=0.3475, LIQUIDATABLE
- 90% crash: price=0.001000, CR=0.1390, LIQUIDATABLE
- 95% crash: price=0.000500, CR=0.0695, LIQUIDATABLE
- 99% crash: price=0.000100, CR=0.0139, LIQUIDATABLE

Critical work still open:
- native-asset fee accounting
- event-time fee accrual
- block TWAP
- liquidation / insurance / redemption engine
- on-chain POL-origin provenance
- integer-safe DVM arithmetic
- adversarial Monte Carlo after all changes

Economic limitation:
An arbitrary 90%-99% collateral crash cannot coexist with unconditional $1 redemption
without additional capital/reserves or variable redemption. V0.4 therefore targets
protocol survival and bounded loss, while price discovery remains endogenous.
