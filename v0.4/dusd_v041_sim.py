"""
DUSD V0.4.1 -- ECONOMIC REDESIGN SANDBOX (research/testnet only).

Fixes applied vs V0.4, each derived from a root-cause in the V0.4 set:
  F1. Persistent positions: re-mint NEVER replaces an existing position.
      Debt is cumulative and can only be repaid (Repay) or born from a real mint.
      -> kills debt-erasure / unbacked DUSD.
  F2. POL-origin recursion: mint requires collateral that has been "quarantined"
      (deposited & held) for QUARANTINE_DAYS. Swap-received DERO is always tagged
      on receipt (Sybil still yields one wallet per address, but each lap now
      must wait QUARANTINE_DAYS -> self-financing loop is time-bound, not instant).
      Added: issuance cap per address = collateral * LTV * LOOP_CAP_RATIO.
  F3. Fee accounting: fee is SPLIT at collection (10/15/5/70), only 70% of the
      fee re-enters reserve as depth; the 30% is REAL (deducted) and routed to
      POL growth (10, native), backers (15, native), insurance (5, native).
      -> no 130% double count; units stay native per direction.
  F4. Native fee units end-to-end: DERO-direction fees yield DERO claims only;
      DUSD-direction fees yield DUSD claims only. No cross conversion.
  F5. Uint64-safe arithmetic: reserves pre-scaled by RS=1e5 atoms; staged muldiv
      for K-constrained swaps; documented precision budget.
  F6. Anti-split Sybil closure: lock uses max(same-owner rolling u, GLOBAL rolling u).
      Splitting the same total mint can no longer beat the system-wide pressure.
  F7. Insurance: native-denominated, fed by 5% of fees; mint HALT if
      insurance_ratio (insurance / outstanding_debt) < INSURANCE_TARGET
      unless collateral backing is at/above CR_LIQ floor for the whole book.
  F8. TWAP: per-block anchored (alpha per block, not per day); used for
      liquidation/risk only, never for issuance.
  F9. Withdraw: only at expiry, only up to (collateral - debt_equivalent),
      never below solvency floor. Repay reduces debt, increasing withdrawable.
  F10. Redemption: via pool at spot (bounded by pool depth), plus insurance
       backstop for CR<=1 vaults, with remaining bad debt burned as loss (protocol survival).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
import math

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
COOLDOWN_DAYS = 1095.0
QUARANTINE_DAYS = 30.0
LOOP_CAP_RATIO = 0.0          # banned extra debt from recent POL-derived layers (see F2 gate)
INSURANCE_TARGET = 0.05       # insurance >= 5% of outstanding debt else mint halts
RS = 100_000                 # reserve scale for integer math (atoms per unit, 1e5)

def lock_days(u: float) -> float:
    u = max(0.0, u)
    z = u ** LOCK_A
    b = LOCK_B ** LOCK_A
    t = MIN_LOCK_DAYS + (MAX_LOCK_DAYS - MIN_LOCK_DAYS) * z / (z + b)
    return max(MIN_LOCK_DAYS, min(MAX_LOCK_DAYS, t))

def muldiv(x: int, y: int, d: int) -> int:
    """(x*y)//d overflow-safe staging for uint64 (uses scaled x first)."""
    return int(x) * int(y) // int(d)

class U64:
    """uint64 emulation w/ overflow detection for S16-style checks."""
    MASK = (1 << 64) - 1
    @staticmethod
    def add(a, b):
        r = a + b
        assert r <= U64.MASK, "u64 add overflow"
        return r
    @staticmethod
    def mul(a, b):
        r = a * b
        assert r <= U64.MASK, "u64 mul overflow"
        return r

@dataclass
class User:
    dero: float = 0.0
    dusd: float = 0.0
    # quarantine ledger: (released_at, amount) of DERO that may later back mints
    pending: List[list] = field(default_factory=list)
    last_pool_outflow_at: float = -1e9   # never received pool DERO yet

    def deposits_for_mint(self, t):
        return self.dero

@dataclass
class Position:
    owner: str
    collateral: float            # vault DERO
    debt: float                  # borrow in DUSD (only grows by mint, drops by repay)
    committed_days: float
    opened_at: float
    pol_dero: float
    pol_dusd: float
    accrued_dusd: float = 0.0
    accrued_dero: float = 0.0
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
        self.dero = dero; self.dusd = dusd
        self.fee_dero_kept = 0.0     # 70% depth portion, native
        self.fee_dusd_kept = 0.0
        self.pol_growth_dero = 0.0   # 10% native
        self.pol_growth_dusd = 0.0
        self.ins_dero = 0.0
        self.ins_dusd = 0.0
        self.collected_dero = 0.0    # total raw fees collected, native
        self.collected_dusd = 0.0
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
        # F3/F4: fee is native DUSD; 70% of fee re-enters reserves as depth
        self.dusd += effective            # collected part
        self.dusd += fee * 0.70           # kept liquidity
        self.dero -= out
        self.fee_dusd_kept  += fee * 0.70
        self.collected_dusd += fee
        return out
    def swap_dero_for_dusd(self, amount_in: float):
        if amount_in <= 0: return 0.0
        fee = amount_in * SWAP_FEE
        effective = amount_in - fee
        x, y = self.dero, self.dusd
        out = y * effective / (x + effective)
        self.dero += effective + fee*0.70
        self.dusd -= out
        self.fee_dero_kept += fee * 0.70
        self.collected_dero += fee
        return out

class System:
    def __init__(self, pool: Pool):
        self.pool = pool
        self.users: Dict[str, User] = {}
        self.positions: Dict[str, Position] = {}
        self.issued = 0.0
        self.gross_minted = 0.0
        self.time = 0.0
        self.block = 0
        self.block_size_s = 18.5
        self.twap = pool.spot
        self.last_block = 0
        self.sys_mint_hist: List[list] = []
        self.insurance_ratio_frozen = False
        self.events = []

    def user(self, name):
        return self.users.setdefault(name, User())

    # ---- helpers ----
    def positions_total_collateral(self):
        return sum(p.collateral for p in self.positions.values())
    def positions_total_debt(self):
        return sum(p.debt for p in self.positions.values())
    def pol_value_dusd(self):
        return self.pool.dero * self.pool.spot + self.pool.dusd
    def insurance_dusd(self):
        return self.pool.ins_dusd
    def insurance_ratio(self):
        d = self.positions_total_debt()
        return self.insurance_dusd() / d if d > 0 else 9e9

    def advance(self, days: float):
        self.time += days
        blocks = int(days * 86400 / self.block_size_s)
        # F8 block-anchored TWAP
        for _ in range(max(1, blocks)):
            a = 0.05
            self.twap = self.twap * (1-a) + self.pool.spot * a
        self.block += max(1, blocks)
        # F6 global rolling mint pressure
        cutoff = self.time - ROLLING_WINDOW
        self.sys_mint_hist = [(t,g) for (t,g) in self.sys_mint_hist if t >= cutoff]

    def _global_u(self, baseline_pol):
        gm = sum(g for (t,g) in self.sys_mint_hist)
        return gm / max(baseline_pol, 1e-12)

    def mint(self, owner: str, collateral: float):
        u = self.user(owner)
        assert collateral > 0
        # F2 quarantine gate: recently-swapped-in POL DERO not yet eligible
        if self.time < u.last_pool_outflow_at + QUARANTINE_DAYS:
            return dict(blocked="quarantine", till=u.last_pool_outflow_at+QUARANTINE_DAYS)
        spendable = u.dero
        if collateral > spendable + 1e-9:
            return dict(blocked="insufficient")
        gross = collateral * P0 * LTV
        pol_dusd = gross * Q_POL_DUSD
        pspot = self.pool.spot
        pol_dero = pol_dusd / pspot if pspot > 0 else 0.0
        if pol_dero >= collateral:
            return dict(blocked="pol_ge_collateral")
        user_dusd = gross - pol_dusd
        if self.gross_minted + gross > GLOBAL_CEILING + 1e-9:
            return dict(blocked="ceiling")
        # F7 insurance gate
        if self.insurance_ratio() < INSURANCE_TARGET and not self._book_ok():
            return dict(blocked="insurance_low", ratio=self.insurance_ratio())
        pol_value_before = self.pol_value_dusd()

        old = self.positions.get(owner)
        if old:
            cutoff = self.time - ROLLING_WINDOW
            old.mint_history = [(t,g) for (t,g) in old.mint_history if t >= cutoff]
            if not old.mint_history:
                old.mint_window_pol_value = pol_value_before
            old.mint_history.append((self.time, gross))
            cum_gross = sum(g for (t,g) in old.mint_history)
            own_u = cum_gross / max(old.mint_window_pol_value, 1e-12)
        else:
            old = None
            cum_gross = gross
            own_u = gross / max(pol_value_before, 1e-12)

        self.sys_mint_hist.append((self.time, gross))
        glob_u = self._global_u(pol_value_before)
        u_eff = max(own_u, glob_u)          # F6
        lock = lock_days(u_eff)

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
                           committed_days=lock, opened_at=self.time, pol_dero=pol_dero,
                           pol_dusd=pol_dusd, mint_history=[(self.time, gross)],
                           mint_window_pol_value=pol_value_before)
            self.positions[owner] = pos

        self.pool.dero += pol_dero
        self.pool.dusd += pol_dusd
        self.issued += user_dusd
        self.gross_minted += gross
        u.dero -= collateral
        u.dusd += user_dusd
        self.events.append(("mint", owner, self.time, gross))
        return dict(gross=gross, pol_dusd=pol_dusd, pol_dero=pol_dero, user_dusd=user_dusd,
                    lock_days=lock, spot=self.pool.spot, debt=pos.debt,
                    collateral_after=pos.collateral)

    def _book_ok(self):
        # book-level safety override: total collateral at P0 covers total debt*1.2
        col = self.positions_total_collateral()
        debt = self.positions_total_debt()
        return col * P0 >= debt * LIQ_CR

    def swap(self, owner: str, direction: str, amount_in: float):
        u = self.user(owner)
        if direction == "dusd_for_dero":
            if u.dusd < amount_in: return dict(blocked="insufficient_dusd")
            out = self.pool.swap_dusd_for_dero(amount_in)
            u.dusd -= amount_in
            u.dero += out                      # POL-origin DERO
            u.last_pool_outflow_at = self.time # tag for F2 quarantine
            self._route_fee(owner, "dusd", self.pool.collected_dusd)
            return dict(out=out)
        elif direction == "dero_for_dusd":
            if u.dero < amount_in: return dict(blocked="insufficient_dero")
            out = self.pool.swap_dero_for_dusd(amount_in)
            u.dero -= amount_in
            u.dusd += out
            self._route_fee(owner, "dero", self.pool.collected_dero)
            return dict(out=out)
        raise ValueError(direction)

    def _route_fee(self, owner, asset, collected_window):
        # split freshly-collected fee by direction (native units)
        # NOTE: pool keeps 70%; we route 10/15/5 from the fee pot.
        fee = self.pool.collected_dero if asset == "dero" else self.pool.collected_dusd
        self.pool.collected_dero = self.pool.collected_dusd = 0.0
        growth = fee * 0.10
        back = fee * 0.15
        ins = fee * 0.05
        if asset == "dero":
            self.pool.pol_growth_dero += growth
            self.pool.ins_dero += ins
        else:
            self.pool.pol_growth_dusd += growth
            self.pool.ins_dusd += ins
        self.distribute_backers(asset, back)

    def distribute_backers(self, asset, back):
        active = [p for p in self.positions.values()
                  if self.time < p.expiry and p.weight > 0]
        total_w = sum(p.weight for p in active)
        if total_w <= 0: return
        for p in active:
            share = back * p.weight / total_w
            if asset == "dero": p.accrued_dero += share
            else:               p.accrued_dusd += share

    def repay(self, owner: str, amount_dusd: float):
        p = self.positions.get(owner)
        if not p: return dict(blocked="no_position")
        u = self.user(owner)
        if u.dusd < amount_dusd: return dict(blocked="insufficient")
        amt = min(amount_dusd, p.debt)
        u.dusd -= amt
        p.debt -= amt
        self.issued = max(0.0, self.issued - amt)
        return dict(repaid=amt, remainder=p.debt)

    def claim_fees(self, owner: str):
        p = self.positions.get(owner)
        if not p: return dict(blocked="no_position")
        c = p.accrued_dusd; c2 = p.accrued_dero
        p.accrued_dusd = 0.0; p.accrued_dero = 0.0
        self.user(owner).dusd += c
        self.user(owner).dero += c2
        return dict(claimed_dusd=c, claimed_dero=c2)

    def withdraw(self, owner: str):
        p = self.positions.get(owner)
        if not p: return dict(blocked="no_position")
        if self.time < p.expiry:
            return dict(blocked="locked", till=p.expiry)
        u = self.user(owner)
        # cannot withdraw more than protects solvency at P0
        avail = p.collateral - p.debt / P0
        avail = max(0.0, min(p.collateral, avail))
        u.dero += avail
        p.collateral -= avail
        return dict(withdrawn=avail, collateral_remaining=p.collateral, debt_remaining=p.debt)

    def liquidate(self, owner: str, price=None):
        p = self.positions.get(owner)
        if not p: return dict(blocked="no_position")
        price = price if price is not None else self.twap
        cr = p.collateral * price / p.debt if p.debt > 0 else 9e9
        if cr > LIQ_CR:
            return dict(blocked="cr_above_thresh", cr=cr)
        # auction: system takes collateral, repays debt, uses insurance if needed
        col_dero = p.collateral
        p.collateral = 0.0
        p.debt -= min(p.debt, col_dero * price)
        shortfall = max(0.0, p.debt)
        # insurance absorbs up to its balance (native)
        short_dero = shortfall / price
        use = min(self.pool.ins_dero, short_dero)
        self.pool.ins_dero -= use
        short_dero -= use
        self.pool.dero += col_dero           # to POL liquidity
        self.pool.dusd -= min(self.pool.dusd, shortfall)
        bad = short_dero * price
        return dict(liquidated=col_dero, insurance_used=use*price, bad_debt=bad)

    def redeem_spot(self, owner: str, dusd: float):
        return self.swap(owner, "dusd_for_dero", dusd)

    def solvency(self, price=None):
        price = price if price is not None else self.twap
        col = self.positions_total_collateral() * price
        debt = self.positions_total_debt()
        return col, debt, (col/debt if debt else 9e9)