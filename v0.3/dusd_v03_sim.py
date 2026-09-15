#!/usr/bin/env python3
"""DUSD V0.3 economic sandbox.

This is NOT production smart-contract code. It is a deterministic simulator for
price discovery, mint pressure, POL growth, time-weighted POL shares, and stress
scenarios. Units:
  - DERO and DUSD are real units (not atomic units) in the sandbox.
  - USD is only used as a human-readable Genesis denomination.

Core design assumptions:
  Genesis price P0 = 0.01 DUSD/DERO.
  Mint capacity is locked-collateral * issuance_price * LTV.
  Old debt is NOT repriced when market price moves.
  A small mandatory fraction of each mint funds POL; the POL DERO side is
  matched at current spot price so the contribution is price-neutral at entry.
  Swap is a constant-product AMM with fee.
  Lock time responds smoothly to mint pressure vs POL value.
  POL fee share is proportional to remaining_locked_DERO * remaining_days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math, random, statistics
from typing import Dict, List, Tuple

P0 = 0.01
LTV = 0.72
GLOBAL_CEILING = 250_000.0
POL_DUSD_RATE = 0.0025       # 0.25% of gross minted DUSD
SWAP_FEE = 0.003             # 0.30%
MIN_LOCK_DAYS = 30.0
MAX_LOCK_DAYS = 1095.0
LOCK_K = 1065.0
LOCK_ALPHA = 1.05977
LOCK_U0 = 0.550857
EPS = 1e-12


def lock_days(mint_dusd: float, pool_value_dusd: float) -> float:
    if pool_value_dusd <= EPS:
        return MAX_LOCK_DAYS
    u = max(mint_dusd / pool_value_dusd, 0.0)
    z = u ** LOCK_ALPHA
    d = MIN_LOCK_DAYS + LOCK_K * z / (z + LOCK_U0 ** LOCK_ALPHA)
    return min(MAX_LOCK_DAYS, max(MIN_LOCK_DAYS, d))


@dataclass
class Vault:
    collateral: float
    issue_price: float
    debt: float
    lock_days_total: float
    lock_remaining: float
    pol_weight_principal: float = 0.0

    @property
    def current_weight(self) -> float:
        return self.collateral * max(self.lock_remaining, 0.0)


@dataclass
class Pool:
    dero: float
    dusd: float
    fees_dusd: float = 0.0

    @property
    def spot(self) -> float:
        if self.dero <= EPS:
            return math.inf
        return self.dusd / self.dero

    @property
    def value_dusd(self) -> float:
        return self.dero * self.spot + self.dusd if self.dero > EPS else self.dusd

    @property
    def k(self) -> float:
        return self.dero * self.dusd

    def swap_dusd_for_dero(self, amount_in: float) -> Tuple[float, float]:
        if amount_in <= 0 or self.dero <= EPS or self.dusd <= EPS:
            raise ValueError("Pool cannot execute this swap")
        fee = amount_in * SWAP_FEE
        net = amount_in - fee
        x, y = self.dero, self.dusd
        out = x * net / (y + net)
        self.dusd += net
        self.dero -= out
        self.fees_dusd += fee
        return out, fee

    def swap_dero_for_dusd(self, amount_in: float) -> Tuple[float, float]:
        if amount_in <= 0 or self.dero <= EPS or self.dusd <= EPS:
            raise ValueError("Pool cannot execute this swap")
        fee = amount_in * SWAP_FEE
        net = amount_in - fee
        x, y = self.dero, self.dusd
        out = y * net / (x + net)
        self.dero += net
        self.dusd -= out
        self.fees_dusd += fee
        return out, fee


@dataclass
class System:
    pool: Pool
    vaults: Dict[str, Vault] = field(default_factory=dict)
    issued: float = 0.0
    retired: float = 0.0
    total_fees_distributed: float = 0.0
    time_days: float = 0.0
    audit: List[str] = field(default_factory=list)

    @property
    def outstanding(self) -> float:
        return self.issued - self.retired

    @property
    def total_collateral(self) -> float:
        return sum(v.collateral for v in self.vaults.values())

    def assert_invariants(self) -> None:
        assert self.issued + 1e-9 >= self.retired
        assert self.outstanding >= -1e-9
        assert self.outstanding <= GLOBAL_CEILING + 1e-6
        for vid, v in self.vaults.items():
            assert v.debt <= v.collateral * v.issue_price * LTV + 1e-8, f"vault cap {vid}"
            assert v.collateral >= -1e-9

    def mint(self, vid: str, collateral: float, issue_price: float | None = None) -> Dict[str, float]:
        if collateral <= 0:
            raise ValueError("collateral must be positive")
        if issue_price is None:
            issue_price = self.pool.spot
        if not math.isfinite(issue_price) or issue_price <= 0:
            raise ValueError("invalid issue price")
        gross = collateral * issue_price * LTV
        if self.outstanding + gross > GLOBAL_CEILING + 1e-9:
            raise ValueError("global ceiling exceeded")

        pool_value_before = self.pool.value_dusd
        lock_d = lock_days(gross, pool_value_before)

        # The mandatory POL DUSD comes from the same mint. The DERO side is
        # matched at the current spot so the entry does not intentionally move price.
        pol_dusd = gross * POL_DUSD_RATE
        pol_dero = pol_dusd / max(self.pool.spot, EPS)
        user_dusd = gross - pol_dusd

        v = self.vaults.get(vid)
        if v is None:
            v = Vault(collateral=collateral, issue_price=issue_price, debt=gross,
                      lock_days_total=lock_d, lock_remaining=lock_d)
            self.vaults[vid] = v
        else:
            # New deposits create a new debt tranche only for their actual locked amount.
            # We store a weighted-average issue price for sandbox accounting and keep
            # debt <= sum(tranche caps) by conservative aggregation.
            old_cap = v.collateral * v.issue_price * LTV
            old_debt = v.debt
            v.collateral += collateral
            v.debt += gross
            v.issue_price = (old_cap + collateral * issue_price * LTV) / (v.collateral * LTV)
            v.lock_days_total = max(v.lock_days_total, lock_d)
            v.lock_remaining = max(v.lock_remaining, lock_d)

        # Add price-neutral POL liquidity. For sandboxing, this is protocol-owned
        # liquidity and cannot be withdrawn by the minter before expiry.
        self.pool.dero += pol_dero
        self.pool.dusd += pol_dusd
        self.issued += gross
        self.assert_invariants()
        return {
            "gross_mint": gross,
            "user_dusd": user_dusd,
            "pol_dusd": pol_dusd,
            "pol_dero": pol_dero,
            "lock_days": lock_d,
            "spot_after": self.pool.spot,
        }

    def advance(self, days: float) -> None:
        if days < 0:
            raise ValueError("days must be non-negative")
        self.time_days += days
        for v in self.vaults.values():
            v.lock_remaining = max(0.0, v.lock_remaining - days)
        self.assert_invariants()

    def fee_weight(self) -> Dict[str, float]:
        weights = {vid: v.current_weight for vid, v in self.vaults.items()}
        total = sum(weights.values())
        if total <= EPS:
            return {vid: 0.0 for vid in weights}
        return {vid: w / total for vid, w in weights.items()}

    def distribute_fees(self) -> Dict[str, float]:
        weights = self.fee_weight()
        pool_fees = self.pool.fees_dusd
        dist = {vid: pool_fees * share for vid, share in weights.items()}
        self.total_fees_distributed += pool_fees
        self.pool.fees_dusd = 0.0
        return dist


def scenario_round_trip() -> Dict[str, float]:
    # Chosen so the initial DUSD reserve is 0.25% of a larger DUSD base outside
    # the pool in the theoretical price-range analysis.
    p = Pool(dero=100_000.0, dusd=1_000.0)
    s = System(p)
    start = s.pool.spot
    # Use a sequence of swaps to push price high, then reverse.
    spend = 0.0
    for _ in range(200):
        # gradually spend up to 99% of pool DUSD; stop if pool gets too thin
        amount = min(10_000.0, max(1.0, s.pool.dusd * 0.02))
        if amount >= s.pool.dusd * 0.999:
            break
        s.pool.swap_dusd_for_dero(amount)
        spend += amount
    high = s.pool.spot
    for _ in range(200):
        amount = min(1_000.0, max(0.1, s.pool.dero * 0.02))
        if amount >= s.pool.dero * 0.999:
            break
        s.pool.swap_dero_for_dusd(amount)
    low = s.pool.spot
    return {"start": start, "high": high, "end": low, "swapped_dusd": spend}


def scenario_recursive_attack() -> Dict[str, float]:
    # Attack attempt where the attacker repeatedly uses newly marked market price.
    # V0.3 rule is that existing vault debt is NOT repriced, so attack cannot mint
    # against the same old collateral simply because spot moved.
    p = Pool(dero=100_000.0, dusd=1_000.0)
    s = System(p)
    r = s.mint("attacker", collateral=1_000_000.0, issue_price=P0)
    prices = [s.pool.spot]
    failed_extra_mints = 0
    for _ in range(5):
        # try to mint more against existing collateral at new spot
        try:
            s.mint("attacker", collateral=0.0)
        except Exception:
            failed_extra_mints += 1
        # push spot
        s.pool.swap_dusd_for_dero(min(100.0, s.pool.dusd * 0.25))
        prices.append(s.pool.spot)
    return {
        "initial_debt": r["gross_mint"],
        "final_debt": s.outstanding,
        "failed_extra_mint_attempts": failed_extra_mints,
        "max_observed_spot": max(prices),
    }


def scenario_crash() -> Dict[str, float]:
    # One vault issued at Genesis; market gets repriced by a sell-off.
    p = Pool(dero=100_000.0, dusd=1_000.0)
    s = System(p)
    m = s.mint("alice", collateral=1_000_000.0, issue_price=P0)
    start_spot = s.pool.spot
    # 90% market-price shock approximated by selling a large DERO amount.
    # Do not let reserve go negative.
    target = start_spot * 0.1
    for _ in range(400):
        if s.pool.spot <= target * 1.02:
            break
        amount = min(10_000.0, s.pool.dero * 0.03)
        if amount <= 0:
            break
        try:
            s.pool.swap_dero_for_dusd(amount)
        except ValueError:
            break
    end_spot = s.pool.spot
    vault = s.vaults["alice"]
    collateral_value = vault.collateral * end_spot
    cr = collateral_value / vault.debt if vault.debt else math.inf
    return {
        "issue_price": m["spot_after"],
        "end_spot": end_spot,
        "debt": vault.debt,
        "collateral_value": collateral_value,
        "collateral_ratio": cr,
    }


def monte_carlo(seed: int = 7, paths: int = 10_000, steps: int = 365) -> Dict[str, float]:
    rng = random.Random(seed)
    failures = 0
    negative_pool = 0
    ceiling_hits = 0
    min_cr = float("inf")
    max_price = 0.0

    for _ in range(paths):
        p = Pool(dero=100_000.0, dusd=1_000.0)
        s = System(p)
        s.mint("seed", collateral=1_000_000.0, issue_price=P0)
        for _step in range(steps):
            # External-ish flow proxy: random swaps. Importantly, no oracle.
            if p.dero <= 1e-6 or p.dusd <= 1e-6:
                negative_pool += 1
                break
            u = rng.random()
            try:
                if u < 0.48:
                    amt = p.dusd * rng.uniform(0.0005, 0.03)
                    p.swap_dusd_for_dero(amt)
                elif u < 0.96:
                    amt = p.dero * rng.uniform(0.0005, 0.03)
                    p.swap_dero_for_dusd(amt)
                else:
                    # New users mint with modest amounts at Genesis price only.
                    c = 100.0 * 10 ** rng.uniform(1, 3)
                    if s.outstanding < GLOBAL_CEILING * 0.98:
                        s.mint(f"u{rng.randrange(1000)}", collateral=c, issue_price=P0)
            except (ValueError, AssertionError):
                failures += 1
                break
            s.advance(1.0)
            # Monitor each vault versus current market price, without liquidating.
            for v in s.vaults.values():
                if v.debt > 0:
                    cr = (v.collateral * p.spot) / v.debt
                    min_cr = min(min_cr, cr)
            max_price = max(max_price, p.spot)
            if s.outstanding >= GLOBAL_CEILING - 1e-6:
                ceiling_hits += 1
                break

    return {
        "paths": float(paths),
        "path_failures": float(failures),
        "negative_pool_events": float(negative_pool),
        "ceiling_hit_paths": float(ceiling_hits),
        "min_observed_collateral_ratio": min_cr,
        "max_observed_price": max_price,
    }


def main() -> None:
    rows = {
        "round_trip": scenario_round_trip(),
        "recursive_attack": scenario_recursive_attack(),
        "crash": scenario_crash(),
        "monte_carlo": monte_carlo(),
    }
    for name, vals in rows.items():
        print(f"\n[{name}]")
        for k, v in vals.items():
            print(f"{k}={v}")

if __name__ == "__main__":
    main()
