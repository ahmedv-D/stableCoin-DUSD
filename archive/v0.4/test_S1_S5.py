"""S1-S5 tests: Genesis, POL accounting, Recursion, Anti-split, Lock curve."""
import math, importlib, sys
v04 = importlib.import_module("dusd_v04_sim")
A = importlib.import_module("v04_adversarial")

OUT = []
def log(*a): OUT.append(" ".join(str(x) for x in a))

# ---------------- S1 GENSIS ----------------
log("== S1. GENESIS CORRECTNESS ==")
pool_vals = [(100_000.0, 1_000.0)]  # balanced genesis
for C in [1,10,100,1_000,10_000,100_000,1_000_000,10_000_000,16_779_303.0]:
    s = A.System(A.Pool(*pool_vals[0]))
    s.user("w").dero = C
    r = s.mint("w", C)
    if "blocked" in r:
        log(f"C={C:,.0f}: BLOCKED {r}")
        continue
    dep = C
    vault = r["collateral_after"]
    pol_dero = r["pol_dero"]
    ok = abs(dep - (vault + pol_dero)) < 1e-6
    log(f"C={C:,.0f}: gross={r['gross']:.6f} pol_dusd={r['pol_dusd']:.6f} "
        f"pol_dero={pol_dero:.6f} vault_coll={vault:.6f} user={r['user_dusd']:.6f} "
        f"lock={r['lock_days']:.2f} dep==vault+pol {ok}")

# ceiling reachability
log("  ceiling (250k) reachable at P0-only over real supply?")
supply = 16_779_303.0
capacity = supply * P0 * LTV if 'P0' in dir() else supply * v04.P0 * v04.LTV
log(f"  max phase-1 DUSD if ALL DERO deposited = {capacity:,.0f} ({capacity/v04.GLOBAL_CEILING*100:.1f}% of ceiling)")

# ---------------- S2 POL ACCOUNTING ----------------
log("== S2. POL ACCOUNTING across pool states ==")
pool_states = [
    ("balanced",    100_000.0, 1_000.0),
    ("dero_heavy",  1_000_000.0, 1_000.0),
    ("dusd_heavy",  100_000.0, 100_000.0),
    ("tiny",        10.0, 0.1),
    ("huge",        10_000_000.0, 100_000.0),
]
for name, X, Y in pool_states:
    s = A.System(A.Pool(X, Y))
    C = 1_000_000.0
    s.user("w").dero = C
    r = s.mint("w", C)
    if "blocked" in r:
        log(f"{name}: X={X:,.0f} Y={Y:,.0f} spot={X*0+Y/X:.6f} -> BLOCKED {r.get('blocked')}")
        # test with smaller size
        s2 = A.System(A.Pool(X, Y)); s2.user("w").dero = 1.0
        r2 = s2.mint("w", 1.0)
        log(f"  retry C=1: {r2 if r2.get('blocked') else 'OK'}")
        if r2.get('blocked'): 
            log(f"  !!! even 1 DERO blocked: {r2}")
        continue
    vault = r["collateral_after"]; pol = r["pol_dero"]
    accounted = s.total_dero_accounted()
    deposited = s.pool.dero - (X - 0)  # pool grew only by pol_dero for one mint... 
    # recompute accounting properly
    pos_col = s.positions_total_collateral()
    pos_pol = sum(p.pol_dero for p in s.positions.values())
    # total DERO = pool.dero + vault collateral (vault collateral is pos.collateral)
    # original deposited = X_orig + C consumed; check pool+pos == X+C
    total_now = s.pool.dero + pos_col
    expected = X + C
    log(f"{name}: spot={s.pool.spot:.6f} pol_dero={pol:.4f} vault={vault:.2f}")
    log(f"   tot_now={total_now:,.2f} expected={expected:,.2f} match={'OK' if abs(total_now-expected)<1e-6 else 'MISMATCH'}")

# ---------------- S3 RECURSION ----------------
log("== S3. RECURSIVE POL DRAIN (sybil cooldown bypass) ==")
for iters in [1,2,5,10,50]:
    s = A.System(A.Pool(100_000.0, 1_000.0))
    # attacker with fresh outsourced DERO; uses fresh addr each lap to dodge cooldown
    incoming_dero = 100_000.0
    for i in range(iters):
        name = f"addr_{i}"
        s.user(name).dero = incoming_dero
        r = s.mint(name, incoming_dero)
        if "blocked" in r: break
        out = r["user_dusd"]
        rr = s.swap(name, "dusd_for_dero", out)
        incoming_dero = rr.get("out", 0.0)
    log(f"iters={iters}: pool.dero={s.pool.dero:,.2f} (start 101,800) pool.dusd={s.pool.dusd:,.2f} spot={s.pool.spot:.4f} debt={s.positions_total_debt():,.2f}")

# ---------------- S4 ANTI-SPLIT ----------------
log("== S4. ANTI-SPLIT ==")
C = 1_000_000.0
def split_lock(n, owner_mode="same"):
    s = A.System(A.Pool(100_000.0, 1_000.0))
    locks = []
    per = C / n
    for i in range(n):
        o = "w" if owner_mode == "same" else f"w{i}"
        s.user(o).dero = per
        r = s.mint(o, per)
        if "blocked" in r: locks.append(None); break
        locks.append(r["lock_days"])
    pos = s.positions.get("w") if owner_mode == "same" else None
    if owner_mode == "same" and pos:
        return pos.committed_days, locks
    return max(locks or [0]), locks

one = split_lock(1, "same")
log(f"one-shot: committed={one[0]:.2f}")
for n in [2,10,100,1000,10000]:
    same = split_lock(n, "same")
    log(f"same-addr n={n}: committed={same[0]:.2f} (one-shot lock={one[0]:.2f}) advantage={'YES' if same[0] < one[0]-1e-9 else 'no'}")
for n in [2,10,100]:
    syb = split_lock(n, "multi")
    log(f"multi-addr n={n}: max_lock={syb[0]:.2f} vs one-shot={one[0]:.2f} advantage={'YES (SYBIL)' if syb[0] < one[0]-1e-9 else 'no'}")

# ---------------- S5 LOCK FUNCTION ----------------
log("== S5. LOCK FUNCTION ==")
us = [0.0, 1e-6, 1e-5, 1e-4, 0.001, 0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 100, 1000, 10000]
prev = -1
mono = True
for u in us:
    t = v04.lock_days(u)
    if t < prev: mono = False
    prev = t
    log(f"  u={u:<10g} T={t:8.2f}")
log(f"  monotonic={'YES' if mono else 'NO'} bounds=[{v04.MIN_LOCK_DAYS},{v04.MAX_LOCK_DAYS}]")
whale = v04.lock_days(10000); small = v04.lock_days(1e-6)
log(f"  whale u=1e4 -> {whale:.2f}d  small u=1e-6 -> {small:.2f}d  gap={whale-small:.2f}d")

print("\n".join(OUT))
with open("/home/ahmed/Downloads/DUSD-V0.4/TEST-S1-S5.txt","w") as f: f.write("\n".join(OUT))