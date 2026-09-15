"""
DUSD V0.5 -- UNIFIED CLAIM / DYNAMIC REDEMPTION adversarial sandbox engine.

Faithful implementation of DUSD-V0.5-ECONOMIC-DESIGN.md with STRICT
DERO/DUSD conservation ledgers (accounts must balance after every op).

Units: float DERO / DUSD in this research sim. A separate U64 module
checks DVM-BASIC integer safety (Section 16).

Core identity (design sec.3/4):
  Backing_NAV = POL_NAV + Insurance_NAV + Collateral_NAV
  POL_NAV        = Y + P_risk * X
  Insurance_NAV  = I_DUSD + P_risk * I_DERO
  Collateral_NAV = H * P_risk * C
  Coverage       = Backing_NAV / S        (S = outstanding DUSD)
  ClaimFactor    = min(1, Coverage)
  redeem(q) pays a basket worth q * ClaimFactor.

Design knobs (research defaults, section 1/4/6/7):
  P0=0.01  LTV=0.72  Q=0.0025  H=0.90
  MIN_COVERAGE=1.00  LIQ_CR=1.20
  SWAP_FEE=0.003  split 0.70 depth / 0.10 growth / 0.15 backers / 0.05 ins

Redemption basket order (design sec.5):
  1. liquid DUSD in POL
  2. liquid DUSD in insurance
  3. liquid DERO in POL   (valued at P_risk)
  4. liquid DERO in insurance
  5. eligible collateral via permitted liquidation/settlement

This engine is the FAITHFUL shipping model (attack target). Fix toggles
for V0.5.1 are added as FIX_* class constants so we can A/B the same
attack suite against shipped vs fixed semantics.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math, random

# ---------------- design constants ----------------
P0 = 0.01
LTV = 0.72
Q_POL = 0.0025
H_HAIRCUT = 0.90
MIN_COVERAGE = 1.00
LIQ_CR = 1.20
SWAP_FEE = 0.003
FEE_DEPTH = 0.70
FEE_GROWTH = 0.10
FEE_BACKERS = 0.15
FEE_INS = 0.05
GENESIS_X = 100_000.0     # bootstrap POL DERO
GENESIS_Y = 1_000.0       # bootstrap POL DUSD  (spot = 0.01)
GLOBAL_CEILING = 250_000.0
MIN_LOCK = 30.0
MAX_LOCK = 1095.0
LOCK_A = 1.05977
LOCK_B = 0.550857
ROLLING_WINDOW = 1095.0
BLOCK_S = 18.5
TWAP_ALPHA = 0.05

def lock_days(u: float) -> float:
    u = max(0.0, u)
    z = u ** LOCK_A
    b = LOCK_B ** LOCK_A
    t = MIN_LOCK + (MAX_LOCK - MIN_LOCK) * z / (z + b)
    return max(MIN_LOCK, min(MAX_LOCK, t))

@dataclass
class User:
    dero: float = 0.0
    dusd: float = 0.0
    last_pool_outflow_at: float = -1e18    # provenance tag (per address)

@dataclass
class Position:
    owner: str
    collateral: float          # vault DERO
    debt: float                # DUSD
    committed_days: float
    opened_at: float
    pol_dero: float
    pol_dusd: float
    accrued_dusd: float = 0.0
    accrued_dero: float = 0.0
    weight: float = 0.0        # LockedDERO * CommittedDays (constant)
    mint_hist: List[list] = field(default_factory=list)
    mint_window_pol: float = 0.0
    @property
    def expiry(self): return self.opened_at + self.committed_days
    @property
    def active(self): return self.committed_days > 0

@dataclass
class Pool:
    dero: float
    dusd: float
    fee_dero: float = 0.0
    fee_dusd: float = 0.0
    growth_dero: float = 0.0
    growth_dusd: float = 0.0
    ins_dero: float = 0.0
    ins_dusd: float = 0.0
    backers_paid_dusd: float = 0.0
    backers_paid_dero: float = 0.0
    @property
    def spot(self):
        return self.dusd / self.dero if self.dero > 0 else 0.0

class System:
    FIX = dict(
        haircut_on_payout=False,   # V0.5.1: collateral payouts valued at H*P_risk
        fixed_portfolio=False,     # V0.5.1: snapshot backing at redemption epoch
        provenance=True,           # V0.5: per-address POL-origin quarantine
        global_anti_split=True,
        min_coverage=MIN_COVERAGE,
        insurance_backstop=True,
        twap_block_anchored=True,
        # ---- V0.5.1 additions (default OFF = shipped behaviour) ----
        mint_price="fixed",        # "fixed"=P0 (shipped) | "twap"=min(P0, TWAP)
        redeem_dero_settle="twap", # "twap" (shipped) | "max"=max(TWAP, spot)
        amm_cap=0.0,               # >0: max effective DUSD in per swap (u64 guard)
        pol_drawdown_guard=False,  # True: net DERO outflow from POL <= fee-in * ratio
        pol_drawdown_ratio=4.0,
    )
    def __init__(self, fix: Optional[dict] = None):
        self.pool = Pool(GENESIS_X, GENESIS_Y)
        if fix: self.FIX = dict(self.FIX, **fix)
        self.users: Dict[str, User] = {}
        self.positions: Dict[str, Position] = {}
        self.S = GENESIS_Y       # outstanding DUSD (all buckets), incl. bootstrap pool DUSD
        self.time = 0.0
        self.block = 0
        self.twap = self.pool.spot
        self.sys_mint_hist: List[list] = []
        self.insurance_used = 0.0
        self.bad_debt = 0.0
        self.audit_fail = 0
        self.audit_diag = None
        self.strict_audit = True
        self.events: List[str] = []
        self.pol_dero_in = 0.0    # cumulative POL DERO inflow via fees/mints
        self.pol_dero_out = 0.0   # cumulative POL DERO outflow via swaps

    # ---------------- helpers ----------------
    def fund(self, name: str, dero=0.0, dusd=0.0):
        u = self.user(name)
        u.dero += dero
        u.dusd += dusd
        if dero: self._dero_seed += dero
        if dusd: self.S += dusd
        return u
    def user(self, name): return self.users.setdefault(name, User())
    def positions_collateral(self): return sum(p.collateral for p in self.positions.values())
    def positions_debt(self): return sum(p.debt for p in self.positions.values())
    def pol_value(self): return self.pool.dero * self.pool.spot + self.pool.dusd
    def coverage(self, p=None):
        p = self.twap if p is None else p
        nav = self.nav(p)
        return float('inf') if self.S <= 0 else nav / self.S
    def claim_factor(self, p=None):
        return min(1.0, self.coverage(p))
    def nav(self, p=None):
        p = self.twap if p is None else p
        pol = self.pool.dusd + p * self.pool.dero
        ins = self.pool.ins_dusd + p * self.pool.ins_dero
        col = H_HAIRCUT * p * self.positions_collateral()
        return pol + ins + col

    # Conservation audit. Two exact identities must hold after every op:
#   (A) DERO total  = GENESIS_X + funds in  (nothing creates/destroys DERO)
#   (B) DUSD ledger = S  (outstanding DUSD == every DUSD in all buckets)
    _dero_seed = GENESIS_X

    def _totals(self):
        dero = (self.pool.dero + self.pool.ins_dero
                + self.positions_collateral()
                + sum(u.dero for u in self.users.values())
                + sum(p.accrued_dero for p in self.positions.values()))
        dusd = (self.pool.dusd + self.pool.ins_dusd
                + sum(u.dusd for u in self.users.values())
                + sum(p.accrued_dusd for p in self.positions.values()))
        return dero, dusd

    def audit(self, label="?"):
        dero, dusd = self._totals()
        msg = None
        if abs(dero - self._dero_seed) > 1e-6:
            msg = f"{label}: DERO leaked {dero - self._dero_seed:+.6f}"
        elif abs(dusd - self.S) > 1e-6:
            msg = f"{label}: DUSD ledger {dusd:.6f} != S {self.S:.6f}"
        elif any(u.dero < -1e-6 or u.dusd < -1e-6 for u in self.users.values()):
            msg = f"{label}: negative wallet"
        elif self.S > 1e-4 and self.pool.dero <= 0 and self.pool.dusd <= 0 \
                and self.positions_collateral() <= 0:
            msg = f"{label}: pool drained, no collateral, DUSD outstanding"
        if msg:
            self.audit_fail += 1
            if not self.strict_audit:
                if self.audit_diag is None:
                    self.audit_diag = (label, dero, dusd, self._dero_seed, self.S)
                return False
            raise AssertionError(msg)
        return True

    # ---------------- time ----------------
    def advance(self, days: float):
        if days <= 0: return
        self.time += days
        n = max(1, int(days * 86400 / BLOCK_S))
        a = TWAP_ALPHA
        # closed form: twap_{n} = spot + (twap_0 - spot)*(1-a)^n
        self.twap = self.pool.spot + (self.twap - self.pool.spot) * ((1 - a) ** n)
        self.block += n
        cutoff = self.time - ROLLING_WINDOW
        self.sys_mint_hist = [(t, g) for (t, g) in self.sys_mint_hist if t >= cutoff]

    # ---------------- mint ----------------
    def mint(self, owner: str, collateral: float):
        u = self.user(owner)
        if collateral <= 0 or u.dero < collateral - 1e-9:
            return dict(blocked="insufficient")
        if self.FIX.get("provenance") and self.time < u.last_pool_outflow_at + 1095.0:
            return dict(blocked="provenance", till=u.last_pool_outflow_at + 1095.0)
        # V0.5.1 mint_price: "fixed" mints at P0 (shipped); "twap" mints at
        # min(P0, TWAP) so a crashed market cannot be borrowed against at the
        # genesis price. This directly starves the POL-origin recursion (S9/S10)
        # which depends on minting DUSD at a fixed premium over backing value.
        if self.FIX.get("mint_price") == "twap":
            pm = min(P0, self.twap)
        else:
            pm = P0
        gross = collateral * pm * LTV
        pol_dusd = gross * Q_POL
        pspot = self.pool.spot
        pol_dero = pol_dusd / pspot if pspot > 0 else 0.0
        if pol_dero >= collateral:
            return dict(blocked="pol_ge_collateral", pol_dero=pol_dero, coll=collateral)
        user_dusd = gross - pol_dusd

        # mint gate: proposed backing after mint must cover S.
        # Backing after: current nav + new collateral(0.9) + new pol nav
        p = self.twap
        new_col = collateral - pol_dero
        added_nav = (pol_dusd + p * pol_dero) + H_HAIRCUT * p * new_col
        if self.S + gross > 0:
            cv = (self.nav(p) + added_nav) / (self.S + gross)
            if cv < self.FIX.get("min_coverage", MIN_COVERAGE) - 1e-12:
                return dict(blocked="coverage_gate", coverage=cv, need=MIN_COVERAGE)

        pol_value_before = self.pol_value()
        # mint pressure (anti-split: owner-rolling + global-rolling)
        self.sys_mint_hist.append((self.time, gross))
        own = gross / max(pol_value_before, 1e-9)
        glob = sum(g for (t, g) in self.sys_mint_hist) / max(pol_value_before, 1e-9)
        u_eff = own if not self.FIX.get("global_anti_split") else max(own, glob)
        lock = lock_days(u_eff)

        old = self.positions.get(owner)
        if old:
            old.collateral += new_col
            old.debt += user_dusd
            old.pol_dero += pol_dero
            old.pol_dusd += pol_dusd
            old_exp = old.expiry
            old.committed_days = max(max(0.0, old_exp - self.time), lock)
            old.weight = old.collateral * old.committed_days
            pos = old
        else:
            pos = Position(owner=owner, collateral=new_col, debt=user_dusd,
                           committed_days=lock, opened_at=self.time,
                           pol_dero=pol_dero, pol_dusd=pol_dusd,
                           weight=new_col * lock, mint_hist=[(self.time, gross)],
                           mint_window_pol=pol_value_before)
            self.positions[owner] = pos

        u.dero -= collateral
        u.dusd += user_dusd
        self.pool.dero += pol_dero
        self.pol_dero_in += pol_dero
        self.pool.dusd += pol_dusd
        self.S += gross
        self.events.append(f"mint {owner} C={collateral:.2f} gross={gross:.6f}")
        self.audit("mint")
        return dict(gross=gross, pol_dusd=pol_dusd, pol_dero=pol_dero,
                    user_dusd=user_dusd, lock_days=lock,
                    collateral=new_col, debt=pos.debt)

    # ---------------- swaps (AMM) ----------------
    def swap(self, owner: str, direction: str, amount_in: float):
        u = self.user(owner)
        if direction == "dusd_for_dero":
            if u.dusd < amount_in - 1e-9: return dict(blocked="ins")
            fee = amount_in * SWAP_FEE
            eff = amount_in - fee
            cap = self.FIX.get("amm_cap", 0.0)
            if cap and eff > cap:
                return dict(blocked="amm_cap_u64", eff=eff, cap=cap)
            x, y = self.pool.dero, self.pool.dusd
            if self.FIX.get("pol_drawdown_guard"):
                # net DERO already pulled from POL (swaps) must stay within
                # the DERO the pool has earned back via fees*ratio.
                budget = self.pol_dero_in * self.FIX.get("pol_drawdown_ratio", 4.0)
                if self.pol_dero_out - budget > 1e-9:
                    return dict(blocked="pol_drawdown",
                                net_out=self.pol_dero_out, budget=budget)
            out = x * eff / (y + eff)
            # 0.70 depth + 0.10 growth reinvested -> pool liquidity
            # 0.05 insurance bucket, 0.15 backers (new outstanding DUSD)
            self.pool.dusd += eff + fee * (FEE_DEPTH + FEE_GROWTH)
            self.pool.ins_dusd += fee * FEE_INS
            self._distribute_backers("dusd", fee * FEE_BACKERS)
            self.pool.dero -= out
            self.pol_dero_out += out
            self._flow_track("dusd", amount_in)
            u.dusd -= amount_in
            u.dero += out
            u.last_pool_outflow_at = self.time
            self._distribute_backers("dero", 0.0)
            self.audit("swap dfd")
            return dict(out=out, fee=fee, spot_after=self.pool.spot)
        elif direction == "dero_for_dusd":
            if u.dero < amount_in - 1e-9: return dict(blocked="ins")
            fee = amount_in * SWAP_FEE
            eff = amount_in - fee
            cap = self.FIX.get("amm_cap", 0.0)
            if cap and eff > cap:
                return dict(blocked="amm_cap_u64", eff=eff, cap=cap)
            x, y = self.pool.dero, self.pool.dusd
            out = y * eff / (x + eff)
            self.pool.dero += eff + fee * (FEE_DEPTH + FEE_GROWTH)
            self.pol_dero_in += eff + fee * (FEE_DEPTH + FEE_GROWTH)
            self.pool.ins_dero += fee * FEE_INS
            self._distribute_backers("dero", fee * FEE_BACKERS)
            self.pool.dusd -= out
            u.dero -= amount_in
            u.dusd += out
            self.audit("swap fdd")
            return dict(out=out, fee=fee, spot_after=self.pool.spot)
        return dict(blocked="dir")

    _pool_in_dero = 0.0
    _pool_out_dero = 0.0
    def _flow_track(self, direction, amt):
        pass

    def _distribute_backers(self, asset, back):
        active = [p for p in self.positions.values() if self.time < p.expiry and p.weight > 0]
        tw = sum(p.weight for p in active)
        if tw <= 0:
            # no active lockers: unclaimed backers fee rolls to insurance so
            # the ledger identity DERO_total / DUSD=S is never violated
            if asset == "dusd": self.pool.ins_dusd += back
            else:               self.pool.ins_dero += back
            return
        for p in active:
            s = back * p.weight / tw
            if asset == "dusd": p.accrued_dusd += s
            else:               p.accrued_dero += s

    # ---------------- dynamic redemption (THE attack target) ----------------
    def redeem(self, owner: str, q: float, p_risk: Optional[float] = None):
        """
        Shipped V0.5 dynamic redemption. Basket order per design sec.5:
        POL DUSD -> insurance DUSD -> POL DERO -> insurance DERO -> collateral.
        Target = q * ClaimFactor(P_risk). Paid value uses NAV valuation of
        the assets LEAVEing the backing pool.
        """
        u = self.user(owner)
        if u.dusd < q - 1e-9:
            return dict(blocked="insufficient_dusd", have=u.dusd, want=q)
        p = self.twap if p_risk is None else p_risk
        cf = self.claim_factor(p)
        target = q * cf
        # V0.5.1: DERO legs must never settle at a price below the pool's own
        # ask (spot). Paying at max(TWAP, spot) closes the pump-wedge subsidy
        # where a redeemer drove spot up then redeemed DERO at a stale TWAP.
        pdero = p if self.FIX.get("redeem_dero_settle") != "max" else max(p, self.pool.spot)
        paid_value = 0.0
        paid_dusd = 0.0
        paid_dero = 0.0

        # 1: liquid DUSD in POL (face value)
        use = min(self.pool.dusd, target - paid_value)
        self.pool.dusd -= use
        paid_dusd += use
        paid_value += use
        # 2: liquid DUSD in insurance
        use = min(self.pool.ins_dusd, target - paid_value)
        self.pool.ins_dusd -= use
        paid_dusd += use
        paid_value += use
        # 3: POL DERO at settle price
        if paid_value < target and pdero > 0:
            need_dero = (target - paid_value) / pdero
            use = min(self.pool.dero, need_dero)
            self.pool.dero -= use
            paid_dero += use
            paid_value += use * pdero
        # 4: insurance DERO at settle price
        if paid_value < target and pdero > 0:
            need_dero = (target - paid_value) / pdero
            use = min(self.pool.ins_dero, need_dero)
            self.pool.ins_dero -= use
            paid_dero += use
            paid_value += use * pdero
# 5: eligible collateral via settlement. NAV counts collateral at
        # H*P_risk (haircut); paying it out at full P_risk would move MORE
        # real value than NAV recognises. V0.5.1 fix: settle at H*P_risk.
        settle_price = (H_HAIRCUT * p) if self.FIX.get("haircut_on_payout") else p
        if self.FIX.get("redeem_dero_settle") == "max":
            settle_price = max(settle_price, pdero)
        if paid_value < target and settle_price > 0:
            need_dero = (target - paid_value) / settle_price
            use = min(self.positions_collateral(), need_dero)
            if use > 0:
                # remove collateral pro-rata across positions
                tot_col = self.positions_collateral()
                for pos in self.positions.values():
                    share = use * pos.collateral / tot_col
                    pos.collateral -= share
                paid_dero += use
                paid_value += use * settle_price

        # finalize: burn q DUSD (S shrinks by exactly q; any DUSD paid back to
        # the user are simply re-circulating outstanding DUSD, and any DERO
        # paid goes to the user's wallet)
        u.dusd -= q
        u.dusd += paid_dusd
        u.dero += paid_dero
        self.S -= q
        self.events.append(f"redeem {owner} q={q:.4f} cf={cf:.4f}")
        self.audit("redeem")
        unpaid = max(0.0, target - paid_value)
        return dict(requested=q, claim_factor=cf, target=target,
                    paid_value=paid_value, paid_dusd=paid_dusd,
                    paid_dero=paid_dero, unpaid=unpaid,
                    dp_dero_value=paid_dero*p,
                    dp_per_dusd=paid_value/max(q,1e-12))

    # ---------------- liquidation ----------------
    def liquidate(self, owner: str, p_risk: Optional[float] = None):
        p = self.twap if p_risk is None else p_risk
        pos = self.positions.get(owner)
        if not pos or pos.debt <= 0: return dict(blocked="none")
        cr = pos.collateral * p / pos.debt
        if cr > LIQ_CR:
            return dict(blocked="cr_high", cr=cr)
        col = pos.collateral
        col_value = col * p
        debt = pos.debt
        # seize collateral: DERO relocates vault -> POL backing (conserved)
        self.pool.dero += col
        pos.collateral = 0.0
        pos.debt = 0.0
        # obligation written off. The collateral now backs the unified claim,
        # so outstanding DUSD (S) is untouched by the collateral itself.
        # The shortfall beyond collateral value is absorbed by insurance, which
        # is BURNED from the ledger (so S drops exactly with the burn):
        shortfall = max(0.0, debt - col_value)
        use_dusd = min(self.pool.ins_dusd, shortfall)
        self.pool.ins_dusd -= use_dusd
        self.S -= use_dusd
        self.insurance_used += use_dusd
        rem = shortfall - use_dusd
        # insurance DERO joining POL backing absorbs more shortfall without
        # affecting S (it is backing, not a burn)
        if rem > 0 and p > 0:
            use_dero = min(self.pool.ins_dero, rem / p)
            self.pool.ins_dero -= use_dero
            self.pool.dero += use_dero
            self.insurance_used += use_dero * p
            rem -= use_dero * p
        # residue = explicit bad debt: outstanding DUSD remain outstanding;
        # the unified claim absorbs them (claim factor < 1 at redemption).
        self.bad_debt += max(0.0, rem)
        self.audit("liquidate")
        return dict(liquidated=col, cr=cr, insurance_used=self.insurance_used,
                    bad_debt=self.bad_debt, shortfall=max(0.0, rem))

    def repay(self, owner: str, amt: float):
        pos = self.positions.get(owner)
        u = self.user(owner)
        if not pos or u.dusd < amt - 1e-9: return dict(blocked="ins")
        amt = min(amt, pos.debt)
        u.dusd -= amt
        pos.debt -= amt
        self.S -= amt
        self.audit("repay")
        return dict(repaid=amt, rem=pos.debt)

    def claim_fees(self, owner: str):
        pos = self.positions.get(owner)
        if not pos: return dict(blocked="none")
        u = self.user(owner)
        u.dusd += pos.accrued_dusd
        u.dero += pos.accrued_dero
        pos.accrued_dusd = 0.0
        pos.accrued_dero = 0.0
        self.audit("claim_fees")
        return dict(claimed=True)

    def withdraw(self, owner: str):
        pos = self.positions.get(owner)
        if not pos or self.time < pos.expiry:
            return dict(blocked="locked")
        u = self.user(owner)
        avail = max(0.0, pos.collateral - pos.debt / P0)   # P0 floor
        avail = min(pos.collateral, avail)
        u.dero += avail
        pos.collateral -= avail
        self.audit("withdraw")
        return dict(withdrawn=avail)

    # ---------------- full snapshot ----------------
    def snapshot(self, p=None):
        p = self.twap if p is None else p
        return dict(
            S=self.S,
            pool_dero=self.pool.dero, pool_dusd=self.pool.dusd,
            ins_dero=self.pool.ins_dero, ins_dusd=self.pool.ins_dusd,
            vault_dero=self.positions_collateral(),
            debt=self.positions_debt(),
            nav=self.nav(p), coverage=self.coverage(p),
            claim_factor=self.claim_factor(p), twap=p, spot=self.pool.spot,
            bad_debt=self.bad_debt, insurance_used=self.insurance_used,
            block=self.block, time=self.time,
        )