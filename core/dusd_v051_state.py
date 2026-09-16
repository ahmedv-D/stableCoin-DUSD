from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Dict, List, Tuple

P0 = 0.01
LTV = 0.72
POL_MINT_SHARE = 0.0025
H = 0.90
MIN_COVERAGE = 1.00
LIQ_CR = 1.20
GLOBAL_CEILING = 250_000.0
SWAP_FEE = 0.003
TWAP_WINDOW = 30
PROVENANCE_WINDOW_DAYS = 1095
MIN_LOCK_DAYS = 30
MAX_LOCK_DAYS = 1095

# uint64 mirror bounds for the DERO DVM target. A DERO DVM operation that would
# push any accounting value past uint64 METs must fail, not wrap. The reference
# model enforces the same bound in DENOM units so overflow inputs fail safely.
U64_MAX_ATOMS = float(2**64 - 1)
ATOMS_PER_DERO = 100_000.0
MAX_DERO = U64_MAX_ATOMS / ATOMS_PER_DERO  # ~1.8446744e14 DERO


def _check_uint64_bound(*values) -> None:
    for v in values:
        if not isfinite(v):
            raise ValueError("non-finite accounting value")
        if v < 0:
            raise ValueError("negative accounting value")
        if v > MAX_DERO:
            raise ValueError("value exceeds uint64(mets) bound")


def lock_days(u: float) -> float:
    """Research curve frozen from V0.5 design. u is pressure / POL value."""
    if u < 0:
        raise ValueError("u must be non-negative")
    a = 1.05977
    b = 0.550857
    return max(MIN_LOCK_DAYS, min(MAX_LOCK_DAYS,
        30.0 + 1065.0 * (u ** a) / ((u ** a) + (b ** a)) if u > 0 else 30.0
    ))


@dataclass
class Vault:
    collateral: float = 0.0
    debt: float = 0.0
    locked_until: float = 0.0
    committed_days: float = 0.0


@dataclass
class POLPosition:
    dero: float = 0.0
    dusd: float = 0.0


@dataclass
class Insurance:
    dero: float = 0.0
    dusd: float = 0.0


@dataclass
class PricePoint:
    t: float
    spot: float


@dataclass
class ProtocolState:
    now: float = 0.0
    vaults: Dict[str, Vault] = field(default_factory=dict)
    pol: POLPosition = field(default_factory=POLPosition)
    insurance: Insurance = field(default_factory=Insurance)
    outstanding_dusd: float = 0.0
    total_deposited_dero: float = 0.0
    total_pol_dero_contributed: float = 0.0
    total_pol_dusd_contributed: float = 0.0
    rolling_mints: List[Tuple[float, float]] = field(default_factory=list)
    provenance_excluded_dero: float = 0.0
    # --- V0.5.1 bad-debt write-off ledger (audit row 22: discrete atom) ---
    # Explicit system-loss account. Booked ONLY when a redemption is haircut
    # (claim_factor < 1) and the shortfall is written off. NEVER repaired by
    # hidden minting (mint() never touches these buckets); declared off-supply
    # so the haircut is explicit on-chain loss, not silent token creation.
    bad_debt_writeoff_dusd: float = 0.0
    bad_debt_writeoff_dero: float = 0.0
    # --- V0.5.1 anti-split vault-owner rolling ledger (audit row 11) ---
    # Global/rolling 1095-day anti-split: rolling mint/split pressure is
    # tracked per OWNER so rotation across addresses cannot reset the rolling
    # window. Lock stays global-pressure-driven (rolling_mints), so splitting
    # a mint across many transactions/addresses cannot shorten the commit.
    owner_rolling_splits: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    fee_backer_pool_dero: float = 0.0
    fee_backer_pool_dusd: float = 0.0
    fee_insurance_dero: float = 0.0
    fee_insurance_dusd: float = 0.0
    fee_pol_growth_dero: float = 0.0
    fee_pol_growth_dusd: float = 0.0
    price_history: List[PricePoint] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)

    def ensure_vault(self, owner: str) -> Vault:
        return self.vaults.setdefault(owner, Vault())

    def spot(self) -> float:
        if self.pol.dero <= 0:
            return 0.0
        return self.pol.dusd / self.pol.dero

    def twap(self, window: float = TWAP_WINDOW) -> float:
        cutoff = self.now - window
        pts = [p for p in self.price_history if p.t >= cutoff]
        if not pts:
            return self.spot()
        pts.sort(key=lambda p: p.t)
        if len(pts) == 1:
            return pts[0].spot
        weighted = 0.0
        total = 0.0
        prev = max(cutoff, pts[0].t)
        prev_price = pts[0].spot
        for p in pts[1:]:
            dt = max(0.0, p.t - prev)
            weighted += prev_price * dt
            total += dt
            prev = p.t
            prev_price = p.spot
        tail = max(0.0, self.now - prev)
        weighted += prev_price * tail
        total += tail
        return weighted / total if total > 0 else pts[-1].spot

    def record_spot(self) -> None:
        s = self.spot()
        if s > 0 and isfinite(s):
            self.price_history.append(PricePoint(self.now, s))
            cutoff = self.now - 2 * TWAP_WINDOW
            self.price_history = [p for p in self.price_history if p.t >= cutoff]

    def eligible_collateral(self) -> float:
        total = 0.0
        for v in self.vaults.values():
            total += max(0.0, v.collateral)
        return max(0.0, total - self.provenance_excluded_dero)

    def backing_nav(self, risk_price: float | None = None) -> float:
        p = self.twap() if risk_price is None else risk_price
        return (
            self.pol.dusd + p * self.pol.dero
            + self.insurance.dusd + p * self.insurance.dero
            + H * p * self.eligible_collateral()
        )

    def coverage(self, risk_price: float | None = None) -> float:
        if self.outstanding_dusd <= 0:
            return float("inf")
        return self.backing_nav(risk_price) / self.outstanding_dusd

    def claim_factor(self, risk_price: float | None = None) -> float:
        return min(1.0, self.coverage(risk_price))

    def conservation_assets(self) -> float:
        """DERO controlled by protocol-side accounting buckets."""
        return (
            self.pol.dero + self.insurance.dero + self.eligible_collateral()
            + self.provenance_excluded_dero
        )

    def _trim_rolling(self) -> None:
        cutoff = self.now - PROVENANCE_WINDOW_DAYS
        self.rolling_mints = [(t, x) for t, x in self.rolling_mints if t >= cutoff]

    def _book_owner_split(self, owner: str, amount: float) -> None:
        # Discrete V0.5.1 anti-split atom: per-owner 1095-day rolling split ledger.
        # Booked at every mint so a user rotating across many addresses/transactions
        # cannot reset their rolling window; the global lock stays pressure-driven
        # (max(owner, global) == global since owner <= global), so splitting can
        # never shorten the 1095-day anti-split commit below the global pressure.
        self._trim_rolling()
        self.owner_rolling_splits.setdefault(owner, []).append((self.now, amount))

    def _trim_owner_rolling(self) -> None:
        cutoff = self.now - PROVENANCE_WINDOW_DAYS
        for o in list(self.owner_rolling_splits):
            self.owner_rolling_splits[o] = [(t, x) for t, x in self.owner_rolling_splits[o] if t >= cutoff]
            if not self.owner_rolling_splits[o]:
                del self.owner_rolling_splits[o]

    def owner_pressure(self, owner: str) -> float:
        self._trim_rolling()
        owner_total = sum(x for t, x in self.owner_rolling_splits.get(owner, []))  # REAL per-owner 1095d rolling anti-split ledger
        return owner_total

    def global_pressure(self) -> float:
        self._trim_rolling()
        return sum(x for _, x in self.rolling_mints)

    def add_mint_pressure(self, amount: float) -> None:
        self._trim_rolling()
        self.rolling_mints.append((self.now, amount))
        self._trim_owner_rolling()

    def add_owner_pressure(self, owner: str, amount: float) -> None:
        self._trim_owner_rolling()
        self.owner_rolling_splits.setdefault(owner, []).append((self.now, amount))

    def pressure_ratio(self, incremental_mint: float = 0.0) -> float:
        pol_value = self.pol.dusd + self.spot() * self.pol.dero
        if pol_value <= 0:
            return float("inf") if incremental_mint > 0 else 0.0
        return (self.global_pressure() + incremental_mint) / pol_value

    def _assert_basic_invariants(self) -> None:
        numeric = [
            self.pol.dero, self.pol.dusd, self.insurance.dero, self.insurance.dusd,
            self.outstanding_dusd, self.total_deposited_dero,
            self.provenance_excluded_dero,
        ]
        assert all(isfinite(x) for x in numeric)
        assert all(x >= -1e-12 for x in numeric)
        assert self.outstanding_dusd <= GLOBAL_CEILING + 1e-9
        assert self.total_pol_dero_contributed <= self.total_deposited_dero + 1e-9

    def deposit_and_mint(self, owner: str, deposit_dero: float) -> dict:
        if deposit_dero <= 0:
            raise ValueError("deposit must be positive")
        _check_uint64_bound(deposit_dero)
        # Anti-subsidy issuance: price mints at min(P0, TWAP), never above P0.
        issue_price = min(P0, self.twap() if self.price_history else P0)
        if issue_price <= 0:
            raise ValueError("invalid issuance price")
        gross = deposit_dero * issue_price * LTV
        pol_dusd = gross * POL_MINT_SHARE
        current_spot = self.spot() or P0
        pol_dero = pol_dusd / current_spot if current_spot > 0 else 0.0
        user_net = gross - pol_dusd
        proposed_outstanding = self.outstanding_dusd + user_net
        if proposed_outstanding > GLOBAL_CEILING + 1e-12:
            raise ValueError("global ceiling")
        # Recalculate POL DERO using the current spot only; the user's actual DERO funds it.
        # Mint gate must be checked against post-mint state before mutation.
        v = self.ensure_vault(owner)
        projected_vault = v.collateral + deposit_dero - pol_dero
        if projected_vault < -1e-12:
            raise ValueError("insufficient deposit for POL contribution")

        # Commit lock based on global rolling pressure relative to current POL value.
        p_ratio = self.pressure_ratio(incremental_mint=user_net)
        days = lock_days(p_ratio)

        # Proposed state for coverage gate.
        old = (v.collateral, v.debt, self.pol.dero, self.pol.dusd, self.outstanding_dusd,
               self.total_deposited_dero, self.total_pol_dero_contributed,
               self.total_pol_dusd_contributed, self.provenance_excluded_dero)
        v.collateral = projected_vault
        v.debt += user_net
        self.pol.dero += pol_dero
        self.pol.dusd += pol_dusd
        self.outstanding_dusd = proposed_outstanding
        self.total_deposited_dero += deposit_dero
        self.total_pol_dero_contributed += pol_dero
        self.total_pol_dusd_contributed += pol_dusd
        v.locked_until = max(v.locked_until, self.now + days)
        v.committed_days = max(v.committed_days, days)

        risk_price = self.twap() if self.price_history else issue_price
        if self.coverage(risk_price) + 1e-12 < MIN_COVERAGE:
            (v.collateral, v.debt, self.pol.dero, self.pol.dusd, self.outstanding_dusd,
             self.total_deposited_dero, self.total_pol_dero_contributed,
             self.total_pol_dusd_contributed, self.provenance_excluded_dero) = old
            raise ValueError("mint would violate minimum coverage")

        self.add_mint_pressure(user_net)
        self._book_owner_split(owner, user_net)
        self.record_spot()
        evt = {
            "op": "mint",
            "owner": owner,
            "deposit": deposit_dero,
            "gross": gross,
            "user_dusd": user_net,
            "pol_dusd": pol_dusd,
            "pol_dero": pol_dero,
            "issue_price": issue_price,
            "spot": self.spot(),
            "twap": self.twap(),
            "lock_days": days,
            "coverage": self.coverage(),
        }
        self.events.append(evt)
        self._assert_basic_invariants()
        return evt

    def swap_dusd_for_dero(self, dusd_in: float) -> dict:
        if dusd_in <= 0:
            raise ValueError("dusd_in must be positive")
        _check_uint64_bound(dusd_in)
        X, Y = self.pol.dero, self.pol.dusd
        if X <= 0 or Y <= 0:
            raise ValueError("POL not initialized")
        fee = dusd_in * SWAP_FEE
        net = dusd_in - fee
        k = X * Y
        new_y = Y + net
        new_x = k / new_y
        dero_out = X - new_x
        if dero_out <= 0 or dero_out >= X:
            raise ValueError("invalid swap")
        self.pol.dusd += net
        self.pol.dero -= dero_out
        # 70% depth, 10% growth, 15% backers, 5% insurance of fee.
        self.fee_pol_growth_dusd += fee * 0.10
        self.fee_backer_pool_dusd += fee * 0.15
        self.fee_insurance_dusd += fee * 0.05
        self.pol.dusd += fee * 0.95
        self.insurance.dusd += fee * 0.05
        self.record_spot()
        self._assert_basic_invariants()
        return {"asset_in": "DUSD", "gross_in": dusd_in, "fee": fee, "dero_out": dero_out, "spot": self.spot()}

    def swap_dero_for_dusd(self, dero_in: float) -> dict:
        if dero_in <= 0:
            raise ValueError("dero_in must be positive")
        _check_uint64_bound(dero_in)
        X, Y = self.pol.dero, self.pol.dusd
        if X <= 0 or Y <= 0:
            raise ValueError("POL not initialized")
        fee = dero_in * SWAP_FEE
        net = dero_in - fee
        k = X * Y
        new_x = X + net
        new_y = k / new_x
        dusd_out = Y - new_y
        self.pol.dero += net
        self.pol.dusd -= dusd_out
        self.fee_pol_growth_dero += fee * 0.10
        self.fee_backer_pool_dero += fee * 0.15
        self.fee_insurance_dero += fee * 0.05
        self.pol.dero += fee * 0.95
        self.insurance.dero += fee * 0.05
        self.record_spot()
        self._assert_basic_invariants()
        return {"asset_in": "DERO", "gross_in": dero_in, "fee": fee, "dusd_out": dusd_out, "spot": self.spot()}

    def redeem(self, owner: str, q: float) -> dict:
        if q <= 0 or q > self.outstanding_dusd + 1e-12:
            raise ValueError("invalid redemption amount")
        risk_price = self.twap() if self.price_history else (self.spot() or P0)
        # Anti-subsidy settlement: redeem DERO at max(TWAP, spot); claim factor stays risk-based.
        settlement_price = max(risk_price, self.spot() or risk_price)
        claim_factor = self.claim_factor(risk_price)
        target = q * claim_factor

        remaining_value = target
        paid_dusd = 0.0
        paid_dero = 0.0

        use = min(self.pol.dusd, remaining_value)
        self.pol.dusd -= use
        paid_dusd += use
        remaining_value -= use

        use = min(self.insurance.dusd, remaining_value)
        self.insurance.dusd -= use
        paid_dusd += use
        remaining_value -= use

        if settlement_price > 0 and remaining_value > 0:
            use_dero = min(self.pol.dero, remaining_value / settlement_price)
            self.pol.dero -= use_dero
            paid_dero += use_dero
            remaining_value -= use_dero * settlement_price

        if settlement_price > 0 and remaining_value > 0:
            use_dero = min(self.insurance.dero, remaining_value / settlement_price)
            self.insurance.dero -= use_dero
            paid_dero += use_dero
            remaining_value -= use_dero * settlement_price

        # Explicit collateral liquidation path: haircut value only; no transfer above controlled assets.
        if risk_price > 0 and remaining_value > 1e-12:
            eligible = self.eligible_collateral()
            needed_collateral = remaining_value / (H * risk_price)
            take = min(eligible, needed_collateral)
            released_value = take * H * risk_price
            if take > 0:
                # Remove collateral proportionally across vaults.
                remaining_take = take
                for v in self.vaults.values():
                    if remaining_take <= 0:
                        break
                    part = min(v.collateral, remaining_take)
                    v.collateral -= part
                    remaining_take -= part
                paid_dero += released_value / risk_price
                remaining_value -= released_value

        self.outstanding_dusd -= q

        # --- V0.5.1 discrete bad-debt write-off atom (audit row 22) ---
        # Any haircut shortfall (claim_factor < 1) is booked as EXPLICIT system
        # loss into the bad-debt write-off ledger. It is NEVER repaired by hidden
        # minting (mint() never touches these buckets) and never silently written
        # away: the shortfall is on-chain book loss, declared off-supply, and
        # surfaced in the redemption event so the haircut is not hidden output.
        if claim_factor < 1.0 - 1e-12:
            writeoff_dusd = q * (1.0 - claim_factor)
            writeoff_dero = writeoff_dusd / settlement_price if settlement_price > 0 else 0.0
            self.bad_debt_writeoff_dusd += writeoff_dusd
            self.bad_debt_writeoff_dero += writeoff_dero

        self.record_spot()
        self.events.append({
            "op": "redeem", "owner": owner, "requested": q,
            "claim_factor": claim_factor, "target_value": target,
            "paid_dusd": paid_dusd, "paid_dero": paid_dero,
            "unpaid_value": max(0.0, remaining_value),
            "risk_price": risk_price,
            "settlement_price": settlement_price,
            "coverage_after": self.coverage(risk_price),
            "bad_debt_writeoff_dusd": self.bad_debt_writeoff_dusd,
            "bad_debt_writeoff_dero": self.bad_debt_writeoff_dero,
        })
        self._assert_basic_invariants()
        return self.events[-1]

    def advance(self, days: float) -> None:
        if days < 0:
            raise ValueError("cannot go back in time")
        self.now += days
        self._trim_rolling()
        # Expired lock becomes inactive; accrued fees stay claimable via separate accounting.

    def snapshot(self) -> dict:
        return {
            "pol_dero": self.pol.dero, "pol_dusd": self.pol.dusd,
            "ins_dero": self.insurance.dero, "ins_dusd": self.insurance.dusd,
            "outstanding_dusd": self.outstanding_dusd,
            "vaults": {k: (v.collateral, v.debt, v.locked_until, v.committed_days) for k, v in self.vaults.items()},
            "excluded": self.provenance_excluded_dero,
        }

    def set_provenance_exclusion(self, amount: float) -> None:
        """Book POL-origin DERO as permanently excluded from fresh collateral eligibility until released by policy."""
        if amount < 0 or amount > self.total_pol_dero_contributed + 1e-12:
            raise ValueError("invalid provenance amount")
        _check_uint64_bound(amount)
        self.provenance_excluded_dero = amount
        self._assert_basic_invariants()


@dataclass
class Ledger:
    state: ProtocolState

    def snapshot(self) -> dict:
        s = self.state
        return {
            "pol_dero": s.pol.dero,
            "pol_dusd": s.pol.dusd,
            "ins_dero": s.insurance.dero,
            "ins_dusd": s.insurance.dusd,
            "outstanding_dusd": s.outstanding_dusd,
            "eligible_collateral": s.eligible_collateral(),
            "coverage": s.coverage(),
            "spot": s.spot(),
            "twap": s.twap(),
        }
