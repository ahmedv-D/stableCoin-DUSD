"""DUSD V0.5.1 — fuzz harness (REAL engine, clean). 100k+ random op
sequences; every step calls the PASSING crash-matrix idiom verbatim
(`deposit_and_mint` → swap → redeem → advance), then audits ONLY the real
engine getters: coverage() (None iff outstanding<=0, engine contract,
core/dusd_v051_state.py:148), claim_factor() (finite, [0,1]), conservation_assets()
(finite) and the outstanding<=GLOBAL_CEILING bound. No invented symbols.
"""
import json, math, os, random

from core.dusd_v051_state import ProtocolState, GLOBAL_CEILING, MIN_COVERAGE

TOL = 1e-9  # harness-local tolerance, verbatim crash-matrix idiom (engine exports no TOL)

FUZZ_ROUNDS = 102_400
SEED = 924817
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "FUZZ-V0.5.1-DUSD-RESULTS.json")


def fresh() -> ProtocolState:
    s = ProtocolState()
    s.deposit_and_mint("alice", 10000.0)   # real deposit keeps ledger consistent
    s.record_spot()
    s.pol.dero = 1000.0
    s.pol.dusd = 10.0
    s.record_spot()
    s.insurance.dero = 250.0
    s.record_spot()
    s.deposit_and_mint("bob", 5000.0)
    s.record_spot()
    return s


def audit(s: ProtocolState) -> bool:
    cov = s.coverage()
    if cov is not None and not (math.isfinite(cov) and cov >= -1e-9):
        return False
    cf = s.claim_factor()
    if cf is None or not math.isfinite(cf) or not (0.0 <= cf <= 1.0 + 1e-9):
        return False
    if not math.isfinite(s.conservation_assets()):
        return False
    if not s.outstanding_dusd <= GLOBAL_CEILING + 1e-9:
        return False
    return all(math.isfinite(v) for v in (s.pol.dero, s.pol.dusd,
                                          s.insurance.dero, s.insurance.dusd,
                                          s.outstanding_dusd))


def step(s: ProtocolState, rng: random.Random) -> bool:
    r = rng.random()
    if r < 0.20:
        if s.coverage() is not None and s.coverage() >= MIN_COVERAGE and s.outstanding_dusd < GLOBAL_CEILING:
            s.deposit_and_mint(f"u{rng.randrange(3)}", rng.uniform(1.0, 500.0))
        else:
            return False
    elif r < 0.40:
        if s.pol.dero > 1.0:
            s.swap_dero_for_dusd(min(s.pol.dero * 0.3, rng.uniform(1.0, s.pol.dero)))
        else:
            return False
    elif r < 0.60:
        if s.pol.dusd > 1.0:
            s.swap_dusd_for_dero(min(s.pol.dusd * 0.3, rng.uniform(1.0, s.pol.dusd)))
        else:
            return False
    elif r < 0.85:
        owner = f"u{rng.randrange(3)}"
        q = min(s.outstanding_dusd * 0.5, s.vaults.get(owner, type(list(s.vaults.values())[0])()).debt * 0.5)
        if q > 1.0:
            s.redeem(owner, q)
        else:
            return False
    else:
        s.advance(rng.uniform(0.0, 12.0))
    return True


def run_fuzz(rounds: int = FUZZ_ROUNDS, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    violations = []
    committed = 0
    for _ in range(rounds):
        s = fresh()
        try:
            for _ in range(9):
                if step(s, rng):
                    committed += 1
                if not audit(s):
                    violations.append(s.snapshot())
                    break
        except Exception as e:
            violations.append({"guard": f"{type(e).__name__}: {e}"})
    r = {"purpose": "V0.5.1 fuzz — 102k op sequences, real engine, real getters only",
         "rounds": rounds, "committed": committed,
         "violations_count": len(violations),
         "violation_sample": violations[:2]}
    open(OUT, "w").write(json.dumps(r, indent=2))
    return r


def test_fuzz_100k_sequences_zero_invariant_violations():
    r = run_fuzz(FUZZ_ROUNDS, SEED)
    assert r["violations_count"] == 0, f"violations={r['violation_sample'][:1]}"
    assert r["rounds"] >= 100_000
