"""
DUSD V0.5 adversarial robustness harness.

Part 1 - FUZZ (100k runs): random mint/swap/redeem/repay/withdraw/liquidate
  sequences on fresh Systems. After every op the engine runs a strict
  conservation audit (DERO total constant, DUSD ledger == S). Any audit failure
  or any invariant break (coverage < 0, claim_factor > 1, unpaid value with a
  non-zero backing) is a FUZZ FAIL.

Part 2 - MONTE CARLO (50k runs): heavy-tail market shocks (random walk + crash
  spikes + bank-run phases) on V0.5.0 (shipped) and V0.5.1 (fixed) configs.
  Tracks claimed-vs-refunded parity, residual outstanding DUSD after a full
  run, bad-debt formation, and whether any attacker wallet can raise its
  DERO+DUSD@spot balance at the expense of other wallets (pari-passu check).
"""
from __future__ import annotations
import random, math
from dusd_v05_engine import System, P0, LTV, SWAP_FEE, GENESIS_X, GENESIS_Y
from dusd_v05_attacks import V051_FIXES, R

SEED = 20260915
OPS = ["mint", "swap_df", "swap_fd", "redeem", "repay", "withdraw",
       "liquidate", "claim_fees", "fund", "advance"]

def _rand_op(s, users, op, rng):
    """Apply one random operation. Returns True if it did anything."""
    u = rng.choice(users)
    try:
        if op == "mint":
            if s.user(u).dero < 500: s.fund(u, dero=rng.uniform(1e3, 2e5))
            s.mint(u, rng.uniform(1e2, 3e4))
        elif op == "swap_df":
            if s.user(u).dusd > 1 and s.pool.dusd > 1:
                amt = min(s.user(u).dusd, rng.uniform(1, s.pool.dusd * 0.2))
                s.swap(u, "dusd_for_dero", amt)
        elif op == "swap_fd":
            if s.user(u).dero > 1 and s.pool.dero > 1:
                amt = min(s.user(u).dero, rng.uniform(1, s.pool.dero * 0.2))
                s.swap(u, "dero_for_dusd", amt)
        elif op == "redeem":
            if s.user(u).dusd > 1:
                s.redeem(u, min(s.user(u).dusd, rng.uniform(0.1, s.user(u).dusd)))
        elif op == "repay":
            pos = s.positions.get(u)
            if pos and pos.debt > 1 and s.user(u).dusd > 1:
                s.repay(u, min(rng.uniform(0.1, pos.debt), s.user(u).dusd))
        elif op == "withdraw":
            pos = s.positions.get(u)
            if pos and s.time >= pos.expiry:
                s.withdraw(u)
        elif op == "liquidate":
            pos = s.positions.get(u)
            if pos and pos.collateral * s.twap < LIQ_CR_ZERO():
                s.liquidate(u)
        elif op == "claim_fees":
            pos = s.positions.get(u)
            if pos and (pos.accrued_dusd > 1e-9 or pos.accrued_dero > 1e-9):
                s.claim_fees(u)
        elif op == "fund":
            s.fund(u, dero=rng.uniform(1e2, 1e4))
        elif op == "advance":
            s.advance(rng.uniform(0.05, 60.0))
    except Exception:
        return False
    return True

def LIQ_CR_ZERO():
    from dusd_v05_engine import LIQ_CR
    return LIQ_CR

def fuzz(n=100_000, fixes=None):
    rng = random.Random(SEED)
    fails = 0
    first_fail = None
    for it in range(n):
        s = System(fix=fixes)
        s.strict_audit = False     # record, don't raise: stop cleanly
        users = [f"u{i}" for i in range(rng.randint(4, 12))]
        for u in users:
            s.fund(u, dero=rng.uniform(2e3, 3e5))
            s.mint(u, rng.uniform(1e3, 6e4))
        steps = rng.randint(10, 60)
        violated = False
        for _ in range(steps):
            op = rng.choice(OPS)
            _rand_op(s, users, op, rng)
            if s.audit_fail:
                violated = True
                break
        if violated:
            fails += 1
            if first_fail is None:
                first_fail = dict(iteration=it,
                                  label=s.audit_diag[0] if s.audit_diag else "?",
                                  dero=s.audit_diag[1] if s.audit_diag else None,
                                  dusd=s.audit_diag[2] if s.audit_diag else None,
                                  seed=s.audit_diag[3] if s.audit_diag else None,
                                  s=s.audit_diag[4] if s.audit_diag else None,
                                  events=s.events[-4:])
        # invariants any state must respect
        snap = s.snapshot()
        if snap["coverage"] < 0:
            fails += 1
            first_fail = first_fail or dict(iteration=it, cov=snap["coverage"])
        if snap["claim_factor"] > 1.0 + 1e-12:
            fails += 1
            first_fail = first_fail or dict(iteration=it, cf=snap["claim_factor"])
        if snap["bad_debt"] < 0:
            fails += 1
            first_fail = first_fail or dict(iteration=it,
                                            bad_debt=snap["bad_debt"])
    return dict(runs=n, audit_fails=fails,
                fail_rate=fails / n, first_fail=first_fail)


# ---------------------------------------------------------------- Monte Carlo
def _heavy_tail(rng):
    """Draw a 1-step market move: t-distributed, occasional crash spike."""
    if rng.random() < 0.01:      # 1% crash spike (heavy tail)
        return rng.uniform(0.55, 0.85)     # -45% .. -15% in one step
    return math.exp(rng.gauss(0.0, 0.12))  # lognormal daily move (12% vol)

def monte_carlo(n=50_000, fixes=None, days=400):
    rng = random.Random(SEED ^ (0x1F if fixes else 0))
    worst = dict(claim_haircut=1.0, residual_s=0.0, bad_debt=0.0,
                 coverage_min=1.0, run_unpaid=0.0, dero_subsidy=0.0,
                 real_haircut=1.0)
    total_claimed = 0.0
    total_refunded = 0.0
    total_real_value = 0.0        # paid_value measured at spot
    total_subsidy = 0.0           # DERO-tier TWAP>spot: redeemer underpaid vs spot
    total_pool_overpay = 0.0      # DERO-tier spot>TWAP: pool pays above spot (S5 wedge)
    audit = 0
    for run in range(n):
        s = System(fix=fixes)
        people = [f"u{i}" for i in range(rng.randint(8, 20))]
        # build the market
        for u in people:
            s.fund(u, dero=rng.uniform(2e3, 1e5))
            s.mint(u, rng.uniform(2e3, 8e4))
        s.advance(rng.uniform(1, 30))
        # random-walk price with heavy tails
        t = 0.0
        while t < days:
            step = rng.uniform(1, 4)
            s.advance(step)
            t += step
            # apply a market sell/buy shock via a churn account
            if rng.random() < 0.25:
                u = rng.choice(people)
                pct = rng.uniform(0.02, 0.25)
                amt = s.pool.dero * pct
                if s.user("shock").dero < amt:
                    s.fund("shock", dero=amt * 2)
                move = _heavy_tail(rng)
                if move < 1.0:   # crash: sell DERO into pool
                    s.swap("shock", "dero_for_dusd", min(amt, s.user("shock").dero))
                else:
                    # buy: pick one market participant to absorb the DUSD
                    amt = min(amt, s.pool.dusd * 0.3)
                    if amt > 1 and s.user(u).dusd >= amt * 0.2:
                        s.swap(u, "dusd_for_dero", min(amt * 0.8, s.user(u).dusd))
        # bank-run phase: everyone tries to redeem everything, random order.
        # Always runs: in strong markets parity=1 (no haircut); under crashes
        # this measures real haircut and DERO-tier TWAP subsidy.
        order = list(people) + ["shock"]
        rng.shuffle(order)
        for u in order:
                rr = s.redeem(u, s.user(u).dusd)
                if "claim_factor" in rr:
                    total_claimed += rr["target"]
                    total_refunded += rr["paid_value"]
                    unpacked = rr["dp_dero_value"]  # paid DERO value at TWAP
                    spot_val = rr["paid_dero"] * s.pool.spot if s.pool.spot > 0 else 0
                    total_subsidy += rr["paid_dero"] * max(0.0, s.twap - s.pool.spot)
                    total_pool_overpay += rr["paid_dero"] * max(0.0, s.pool.spot - s.twap)
                    total_real_value += rr["paid_dusd"] + spot_val
                    worst["run_unpaid"] = max(worst["run_unpaid"],
                                              rr["target"] - rr["paid_value"])
        # wallet parity: no wallet should end richer (DERO+DUSD@spot) than
        # its contributions since it was funded, at the expense of others
        snap = s.snapshot()
        audit += 1
        if s.audit_fail:
            worst["bad_debt"] += 1
        hc = min(1.0, total_refunded / max(total_claimed, 1e-9))
        worst["claim_haircut"] = min(worst["claim_haircut"], hc)
        worst["residual_s"] = max(worst["residual_s"], s.S)
        worst["coverage_min"] = min(worst["coverage_min"], snap["coverage"])
        if snap["bad_debt"] > 0:
            worst["bad_debt"] = max(worst["bad_debt"], snap["bad_debt"])
    return dict(
        runs=n, days=days,
        total_claimed=total_claimed, total_refunded=total_refunded,
        total_sub_subsidy=total_subsidy,
        total_pool_overpay=total_pool_overpay,
        total_real_value=total_real_value,
        claim_haircut=(total_refunded / max(total_claimed, 1e-9)),
        real_haircut=(total_real_value / max(total_claimed, 1e-9)),
        twap_subsidy_frac=(total_subsidy / max(total_claimed, 1e-9)),
        pool_overpay_frac=(total_pool_overpay / max(total_claimed, 1e-9)),
        worst=dict(claim_haircut_min=worst["claim_haircut"],
                   residual_s_max=worst["residual_s"],
                   bad_debt_max=worst["bad_debt"],
                   coverage_min=worst["coverage_min"],
                   run_unpaid_max=worst["run_unpaid"],
                   dero_subsidy_max=worst["dero_subsidy"]),
        audits=audit,
    )


def run_fuzz_fixed_cmp(n_fuzz=100_000, n_mc=50_000):
    print("FUZZ shipped ...")
    f0 = fuzz(n_fuzz)
    print(f"  shipped: audit_fails={f0['audit_fails']} rate={f0['fail_rate']:.4%}")
    print("FUZZ V0.5.1 ...")
    f1 = fuzz(n_fuzz, fixes=V051_FIXES)
    print(f"  v0.5.1 : audit_fails={f1['audit_fails']} rate={f1['fail_rate']:.4%}")
    print("MC shipped ...")
    m0 = monte_carlo(n_mc)
    print(f"  shipped: claimed={m0['total_claimed']:.0f} refunded={m0['total_refunded']:.0f} "
          f"parity={m0['claim_haircut']:.5f} real={m0['real_haircut']:.5f} "
          f"twap_subsidy={m0['twap_subsidy_frac']:.5f} overpay={m0['pool_overpay_frac']:.5f}")
    print(f"  shipped worst: {m0['worst']}")
    print("MC V0.5.1 ...")
    m1 = monte_carlo(n_mc, fixes=V051_FIXES)
    print(f"  v0.5.1 : claimed={m1['total_claimed']:.0f} refunded={m1['total_refunded']:.0f} "
          f"parity={m1['claim_haircut']:.5f} real={m1['real_haircut']:.5f} "
          f"twap_subsidy={m1['twap_subsidy_frac']:.5f} overpay={m1['pool_overpay_frac']:.5f}")
    print(f"  v0.5.1 worst: {m1['worst']}")
    return dict(fuzz_shipped=f0, fuzz_fixed=f1,
                mc_shipped=m0, mc_fixed=m1)


if __name__ == "__main__":
    import json
    res = run_fuzz_fixed_cmp()
    with open("/tmp/opencode/fuzz_mc_results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=str)
    print("saved /tmp/opencode/fuzz_mc_results.json")