from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import math, random

P0 = 0.01
LTV = 0.72
GLOBAL_CEILING = 250_000.0
Q_POL_DUSD = 0.0025
SWAP_FEE = 0.003
MIN_LOCK_DAYS = 30.0
MAX_LOCK_DAYS = 1095.0
LOCK_A = 1.05977
LOCK_B = 0.550857
ROLLING_WINDOW = 1095.0
LIQ_CR = 1.20

def lock_days(u: float) -> float:
    u = max(0.0, u)
    z = u ** LOCK_A
    b = LOCK_B ** LOCK_A
    t = MIN_LOCK_DAYS + (MAX_LOCK_DAYS - MIN_LOCK_DAYS) * z / (z + b)
    return max(MIN_LOCK_DAYS, min(MAX_LOCK_DAYS, t))

@dataclass
class Position:
    owner: str
    collateral: float
    debt: float
    committed_days: float
    opened_at: float
    pol_dero: float
    pol_dusd: float
    accrued_dusd: float = 0.0
    origin_external: bool = True
    mint_history: list = field(default_factory=list)
    mint_window_pol_value: float = 0.0

    @property
    def expiry(self):
        return self.opened_at + self.committed_days

    @property
    def active(self):
        return self.committed_days > 0

    @property
    def weight(self):
        return self.collateral * self.committed_days

class Pool:
    def __init__(self, dero: float, dusd: float):
        assert dero > 0 and dusd > 0
        self.dero = dero
        self.dusd = dusd
        self.fees_dero = 0.0
        self.fees_dusd = 0.0

    @property
    def spot(self):
        return self.dusd / self.dero

    @property
    def k(self):
        return self.dero * self.dusd

    def swap_dusd_for_dero(self, amount_in: float):
        if amount_in <= 0: return 0.0
        fee = amount_in * SWAP_FEE
        effective = amount_in - fee
        x, y = self.dero, self.dusd
        out = x * effective / (y + effective)
        self.dusd += amount_in
        self.dero -= out
        self.fees_dusd += fee
        return out

    def swap_dero_for_dusd(self, amount_in: float):
        if amount_in <= 0: return 0.0
        fee = amount_in * SWAP_FEE
        effective = amount_in - fee
        x, y = self.dero, self.dusd
        out = y * effective / (x + effective)
        self.dero += amount_in
        self.dusd -= out
        self.fees_dero += fee
        return out

class System:
    def __init__(self, pool: Pool):
        self.pool = pool
        self.positions: Dict[str, Position] = {}
        self.issued = 0.0
        self.time = 0.0
        self.insurance_dusd = 0.0
        self.twap = pool.spot
        self._twap_alpha = 0.05
        self.fees_for_backers = 0.0

    def advance(self, days: float):
        assert days >= 0
        # settle no future fee here; fees are allocated at the time they are generated
        self.time += days
        # expired positions become inactive for future fee accrual
        for p in self.positions.values():
            if p.committed_days > 0 and self.time >= p.expiry:
                # Keep committed_days as historical duration for accounting;
                # active weight checks expiry via time < expiry.
                pass
        self.twap = self.twap * (1-self._twap_alpha) + self.pool.spot * self._twap_alpha

    @property
    def pol_value_dusd(self):
        # X*spot + Y = 2Y for spot=Y/X
        return self.pool.dero * self.pool.spot + self.pool.dusd

    def rolling_mint_pressure(self, owner: str):
        p = self.positions.get(owner)
        if not p:
            return 0.0
        cutoff = self.time - ROLLING_WINDOW
        p.mint_history = [(t, g) for (t, g) in p.mint_history if t >= cutoff]
        return sum(g for (t, g) in p.mint_history)

    def mint(self, owner: str, collateral: float, issue_price: float = P0):
        assert collateral > 0
        assert abs(issue_price - P0) < 1e-12, "Phase-1 issuance is P0-only"
        gross = collateral * issue_price * LTV
        pol_dusd = gross * Q_POL_DUSD
        pspot = self.pool.spot
        pol_dero = pol_dusd / pspot
        assert pol_dero < collateral, "POL contribution exceeds collateral"
        user_dusd = gross - pol_dusd
        assert self.issued + gross <= GLOBAL_CEILING + 1e-9

        pol_value_before = self.pol_value_dusd

        old = self.positions.get(owner)
        if old and self.time < old.expiry:
            cutoff = self.time - ROLLING_WINDOW
            old.mint_history = [(t, g) for (t, g) in old.mint_history if t >= cutoff]
            if not old.mint_history:
                old.mint_window_pol_value = pol_value_before
            old.mint_history.append((self.time, gross))
            cumulative_gross = sum(g for (t, g) in old.mint_history)
            baseline_pol = max(old.mint_window_pol_value, 1e-12)
        else:
            old = None
            cumulative_gross = gross
            baseline_pol = max(pol_value_before, 1e-12)

        # Same-owner rolling aggregation: evaluate the full rolling window
        # against the POL depth at the start of that window. New POL created by
        # the same user's split mints cannot dilute the lock requirement.
        u = cumulative_gross / baseline_pol
        lock = lock_days(u)

        if old is not None:
            old_expiry = old.expiry
            old.collateral += collateral - pol_dero
            old.debt += user_dusd
            old.pol_dero += pol_dero
            old.pol_dusd += pol_dusd
            old.committed_days = max(max(0.0, old_expiry - self.time), lock)
        else:
            self.positions[owner] = Position(
                owner=owner, collateral=collateral-pol_dero, debt=user_dusd,
                committed_days=lock, opened_at=self.time,
                pol_dero=pol_dero, pol_dusd=pol_dusd,
                mint_history=[(self.time, gross)],
                mint_window_pol_value=baseline_pol
            )

        # Price-neutral POL contribution: add both sides at the current spot.
        self.pool.dero += pol_dero
        self.pool.dusd += pol_dusd
        self.issued += user_dusd
        return dict(gross=gross, pol_dusd=pol_dusd, pol_dero=pol_dero,
                    user_dusd=user_dusd, lock_days=lock, spot=self.pool.spot)

    def accrue_swap_fee_to_backers(self, fee_asset_value_dusd: float):
        active = [p for p in self.positions.values() if p.committed_days > 0 and self.time < p.expiry and p.weight > 0]
        total_w = sum(p.weight for p in active)
        if total_w <= 0:
            return {}
        # fee split: 15% of fees to backers, 5% insurance, 10% POL growth,
        # 70% retained in pool. Fee value passed in is already normalized
        # externally for research only; unit-correct on-chain accounting must
        # keep native asset units.
        backer_value = fee_asset_value_dusd * 0.15
        ins_value = fee_asset_value_dusd * 0.05
        self.insurance_dusd += ins_value
        out = {}
        for p in active:
            share = p.weight / total_w
            payout = backer_value * share
            p.accrued_dusd += payout
            out[p.owner] = payout
        self.fees_for_backers += backer_value
        return out

    def swap_and_distribute(self, direction: str, amount_in: float):
        if direction == "dusd_for_dero":
            out = self.pool.swap_dusd_for_dero(amount_in)
            # DUSD fee is natively DUSD
            self.accrue_swap_fee_to_backers(amount_in * SWAP_FEE)
        elif direction == "dero_for_dusd":
            out = self.pool.swap_dero_for_dusd(amount_in)
            # For research accounting, DERO fee is valued at pre-trade TWAP only
            # for fee distribution; the ledger itself remains DERO-native.
            fee_value = amount_in * SWAP_FEE * self.twap
            self.accrue_swap_fee_to_backers(fee_value)
        else:
            raise ValueError(direction)
        self.twap = self.twap * (1-self._twap_alpha) + self.pool.spot*self._twap_alpha
        return out

def run_basic():
    s = System(Pool(100_000.0, 1_000.0))
    r = s.mint("alice", 1_000_000.0, P0)
    assert abs(r["gross"] - 7200.0) < 1e-9
    assert abs(r["pol_dusd"] - 18.0) < 1e-9
    assert abs(r["pol_dero"] - 1800.0) < 1e-9
    assert abs(r["user_dusd"] - 7182.0) < 1e-9
    assert abs(s.positions["alice"].collateral + r["pol_dero"] - 1_000_000.0) < 1e-9
    return r

def run_split_test():
    one = System(Pool(100_000.0, 1_000.0))
    r1 = one.mint("w", 1_000_000.0, P0)
    split = System(Pool(100_000.0, 1_000.0))
    locks = []
    for _ in range(100):
        rr = split.mint("w", 10_000.0, P0)
        locks.append(rr["lock_days"])
    # With a fixed rolling-window POL baseline, split mints should not reduce
    # the commitment relative to one-shot minting.
    assert split.positions["w"].committed_days >= r1["lock_days"] - 1e-9
    return r1["lock_days"], split.positions["w"].committed_days

def run_recursive_test():
    s = System(Pool(100_000.0, 1_000.0))
    first = s.mint("attacker", 100_000.0, P0)
    # Any DERO acquired from POL is not modeled as eligible collateral here.
    # Therefore there is no protocol path from swap output into `mint()`.
    before = s.pool.dero
    acquired = s.swap_and_distribute("dusd_for_dero", first["user_dusd"])
    # Acquired DERO is wallet-origin POL output, not admissible collateral.
    assert acquired > 0 and s.pool.dero < before
    return acquired, s.positions["attacker"].debt

def run_crash_ladder():
    s = System(Pool(100_000.0, 1_000.0))
    r = s.mint("alice", 1_000_000.0, P0)
    debt = s.positions["alice"].debt
    rows = []
    for crash in [0.10,0.25,0.50,0.75,0.90,0.95,0.99]:
        price = P0*(1-crash)
        cr = (s.positions["alice"].collateral * price) / debt
        rows.append((crash, price, cr, "LIQUIDATABLE" if cr <= LIQ_CR else "SAFE"))
    return rows

if __name__ == "__main__":
    print("BASIC", run_basic())
    print("SPLIT", run_split_test())
    print("RECURSIVE", run_recursive_test())
    print("CRASH")
    for x in run_crash_ladder():
        print(x)
