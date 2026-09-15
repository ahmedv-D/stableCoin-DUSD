"""
Adversarial harness for DUSD V0.4 (research/testnet only).
Phase A: attempt to BREAK the spec'd model faithfully.

Key differences from the shipped sim (dusd_v04_sim.py):
  1. A real wallet/balance ledger: mint() DEDUCTS DERO from the sender's wallet
     and credits user_dusd to it; swaps move balances between wallets and pool.
     This makes POL-origin DERO recycling testable (the shipped sim assumes it
     away via 'no protocol path', which is NOT an on-chain guarantee).
  2. Spec §8 provenance cooldown implemented as (a) per-address and (b) per-coin
     oracle-model, so we can measure how each behaves under Sybil.
  3. Invoice/assert helpers that measure solvency, redemption capacity,
     insurance backing, K drift, fee unit integrity, lock/weight gaming.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math, random, copy

P0 = 0.01            # DUSD per DERO (genesis/phase-1 issuance price)
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
COOLDOWN_DAYS = 1095.0

def lock_days(u: float) -> float:
    u = max(0.0, u)
    z = u ** LOCK_A
    b = LOCK_B ** LOCK_A
    t = MIN_LOCK_DAYS + (MAX_LOCK_DAYS - MIN_LOCK_DAYS) * z / (z + b)
    return max(MIN_LOCK_DAYS, min(MAX_LOCK_DAYS, t))

@dataclass
class User:
    dero: float = 0.0     # spendable DERO wallet balance
    dusd: float = 0.0     # spendable DUSD wallet balance
    cooldown_until: float = 0.0     # per-address POL-origin mint cooldown (days)
    # coin provenance ledger: coins received from pool swap are tagged
    tagged_coins: List[Tuple[float, float]] = field(default_factory=list)  # (locked_until, amount)

    def spend_dero(self, amt):
        if self.dero < amt - 1e-12: raise ValueError(f"insufficient DERO {self.dero} < {amt}")
        self.dero -= amt

    def receive_dero_from_pool(self, amt, t):
        self.dero += amt
        self.tagged_coins.append((t + COOLDOWN_DAYS, amt))   # pay COOLDOWN_DAYS

    def spendable_collateral(self, t):
        # per-address cooldown: whole wallet blocked during cooldown (spec §8 as-typed)
        if t < self.cooldown_until:
            return 0.0
        return self.dero

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
    def expiry(self): return self.opened_at + self.committed_days

    @property
    def active(self): return self.committed_days > 0

    @property
    def weight(self): return self.collateral * self.committed_days

class Pool:
    def __init__(self, dero, dusd):
        assert dero > 0 and dusd > 0
        self.dero, self.dusd = dero, dusd
        self.fees_dero = 0.0
        self.fees_dusd = 0.0
    @property
    def spot(self): return self.dusd / self.dero
    @property
    def k(self): return self.dero * self.dusd

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
    def __init__(self, pool: Pool, redeem_mode="pool_only"):
        self.pool = pool
        self.users: Dict[str, User] = {}
        self.positions: Dict[str, Position] = {}
        self.issued = 0.0          # cumulative user_dusd credited (spec I2)
        self.gross_minted = 0.0    # cumulative gross DUSD ever created
        self.time = 0.0
        self.insurance_dusd = 0.0
        self.fees_for_backers = 0.0
        self.fees_retained = {"dero": 0.0, "dusd": 0.0}
        self.twap = pool.spot
        self._twap_alpha = 0.05
        self.events = []
        self.redeem_mode = redeem_mode   # pool_only | fixed_rate | none

    def user(self, name) -> User:
        return self.users.setdefault(name, User())

    # ---------- accounting checks ----------
    def total_dero_accounted(self):
        pos = sum(p.collateral for p in self.positions.values())
        pos += sum(p.pol_dero for p in self.positions.values())  # pol portion belongs to pool now
        return self.pool.dero + self.positions_total_collateral()

    def positions_total_collateral(self):
        return sum(p.collateral for p in self.positions.values())

    def positions_total_debt(self):
        return sum(p.debt for p in self.positions.values())

    def total_dusd_outstanding(self):
        # all DUSD ever created remains somewhere: pool reserve + user wallets
        # (no burn path in V0.4 phase-1)
        return self.pool.dusd + sum(u.dusd for u in self.users.values())

    def redemption_capacity_at(self, spot=None):
        spot = spot or self.pool.spot
        # DUSD redeemable from pool = swap DUSD->DERO selling ALL pool DUSD
        # (approx X, minus fees)
        return self.pool.dero * 0.5  # conservative (see S12 exact calc)

    def solvency(self, price=None):
        price = price if price is not None else self.pool.spot
        col = self.positions_total_collateral() * price
        debt = self.positions_total_debt()
        return col, debt, (col / debt if debt > 0 else float('inf'))

    # ---------- core state transitions ----------
    def advance(self, days: float):
        self.time += days
        self.twap = self.twap * (1 - self._twap_alpha) + self.pool.spot * self._twap_alpha

    def mint(self, owner: str, collateral: float, tax_pol_deposit=True):
        u = self.user(owner)
        spendable = u.spendable_collateral(self.time)
        if collateral > spendable + 1e-9:
            return dict(blocked="spendable_collateral", have=spendable, need=collateral)
        assert collateral > 0
        gross = collateral * P0 * LTV
        pol_dusd = gross * Q_POL_DUSD
        pspot = self.pool.spot
        pol_dero = pol_dusd / pspot if pspot > 0 else 0.0
        if pol_dero >= collateral:
            return dict(blocked="pol_gt_collateral", pol_dero=pol_dero, collateral=collateral)
        user_dusd = gross - pol_dusd
        if self.gross_minted + gross > GLOBAL_CEILING + 1e-9:
            return dict(blocked="ceiling", gross=gross)
        pol_value_before = self.pol_value_dusd

        # same-owner rolling aggregation
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

        u_new = cumulative_gross / baseline_pol
        lock = lock_days(u_new)

        if old is not None:
            old_expiry = old.expiry
            old.collateral += collateral - pol_dero
            old.debt += user_dusd
            old.pol_dero += pol_dero
            old.pol_dusd += pol_dusd
            old.committed_days = max(max(0.0, old_expiry - self.time), lock)
            pos = old
        else:
            pos = Position(owner=owner, collateral=collateral-pol_dero, debt=user_dusd,
                           committed_days=lock, opened_at=self.time,
                           pol_dero=pol_dero, pol_dusd=pol_dusd,
                           mint_history=[(self.time, gross)],
                           mint_window_pol_value=baseline_pol)
            self.positions[owner] = pos

        self.pool.dero += pol_dero
        self.pool.dusd += pol_dusd
        self.issued += user_dusd
        self.gross_minted += gross
        # wallet settlement
        u.spend_dero(collateral)
        u.dusd += user_dusd
        self.events.append(("mint", owner, self.time))
        return dict(gross=gross, pol_dusd=pol_dusd, pol_dero=pol_dero,
                    user_dusd=user_dusd, lock_days=lock, spot=self.pool.spot,
                    collateral_after=pos.collateral, debt=pos.debt)

    def swap(self, owner: str, direction: str, amount_in: float):
        u = self.user(owner)
        if direction == "dusd_for_dero":
            if u.dusd < amount_in: return dict(blocked="insufficient_dusd")
            out = self.pool.swap_dusd_for_dero(amount_in)
            u.dusd -= amount_in
            # spec §8 provenance: pool-origin DERO blocked from collateral
            u.receive_dero_from_pool(out, self.time)
            u.cooldown_until = self.time + COOLDOWN_DAYS   # per-address flavor
            self.route_fee("dusd", amount_in * SWAP_FEE)
            self.events.append(("swap_dfd", owner, self.time, amount_in, out))
            return dict(out=out)
        elif direction == "dero_for_dusd":
            if u.dero < amount_in: return dict(blocked="insufficient_dero")
            out = self.pool.swap_dero_for_dusd(amount_in)
            u.dero -= amount_in
            u.dusd += out
            self.route_fee("dero", amount_in * SWAP_FEE)
            self.events.append(("swap_dfd_dero", owner, self.time, amount_in, out))
            return dict(out=out)
        else:
            raise ValueError(direction)

    def route_fee(self, asset: str, fee_amount: float):
        """Spec §5 routing: 10% POL growth / 15% backers / 5% insurance / 70% depth.
        Kept DERO-native and DUSD-native per the rounding rule."""
        self.fees_retained[asset] += fee_amount * 0.70   # stays in pool as depth
        growth = fee_amount * 0.10
        back = fee_amount * 0.15
        ins = fee_amount * 0.05
        if asset == "dero":
            # DERO-denominated claims must stay DERO. Store separately.
            self.insurance_reserve_dero = getattr(self, "insurance_reserve_dero", 0.0) + ins
            self.pol_growth_dero = getattr(self, "pol_growth_dero", 0.0) + growth
            self.distribute_backers_dero(back)
            self.fees_for_backers += back   # DERO-denominated
        else:
            self.insurance_dusd += ins
            self.pol_growth_dusd = getattr(self, "pol_growth_dusd", 0.0) + growth
            self.distribute_backers_dusd(back)

    def distribute_backers_dusd(self, back: float):
        active = [p for p in self.positions.values() if p.committed_days > 0 and self.time < p.expiry and p.weight > 0]
        total_w = sum(p.weight for p in active)
        if total_w <= 0: return
        for p in active:
            p.accrued_dusd += back * p.weight / total_w

    def distribute_backers_dero(self, back: float):
        active = [p for p in self.positions.values() if p.committed_days > 0 and self.time < p.expiry and p.weight > 0]
        total_w = sum(p.weight for p in active)
        if total_w <= 0: return
        for p in active:
            p.accrued_dero = getattr(p, "accrued_dero", 0.0) + back * p.weight / total_w

    @property
    def pol_value_dusd(self):
        return self.pool.dero * self.pool.spot + self.pool.dusd

    def claim_fees(self, owner: str):
        p = self.positions.get(owner)
        if not p: return dict(blocked="no_position")
        c = p.accrued_dusd
        p.accrued_dusd = 0.0
        self.user(owner).dusd += c   # THIS would be mint from nothing if DERO-fees were converted (S7 test)
        return dict(claimed=c)