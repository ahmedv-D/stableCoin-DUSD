"""S6-S10 tests: Weight, Fee accounting, AMM manipulation, Reversibility, Crash ladder."""
import importlib, math
v04 = importlib.import_module("dusd_v04_sim")
A = importlib.import_module("v04_adversarial")
OUT = []
def log(*a): OUT.append(" ".join(str(x) for x in a))

# ---------------- S6 POL WEIGHT ----------------
log("== S6. POL WEIGHT (Wall: LockedDERO*CommittedDays) ==")
cases = [(10_000,365),(5_000,180),(10_000,30),(100,1095),(100_000,30)]
for C,d in cases:
    s = A.System(A.Pool(100_000.0,1_000.0))
    s.user("w").dero = C
    r = s.mint("w", C)
    p = s.positions["w"]
    log(f"  {C} x {d}d: actual_committed={r['lock_days']:.2f} weight={p.weight:,.0f} (nominal={C*d:,.0f})")
# expiry-cliff / fee-sniper
log("  sniff-cliff test:")
s = A.System(A.Pool(100_000.0,1_000.0))
s.user("u1").dero = 10_000; s.user("u2").dero = 10_000; s.user("u3").dero = 10_000
for u in ["u1","u2","u3"]: s.mint(u, 10_000)
s.advance(40)  # all expire (lock=30)
log(f"  all active after 40d: expired in accrual? {[ ( (self.time if False else s.time) >= s.positions[u].expiry ) for u in ['u1','u2','u3']]}")
# fee event right after expiry
s2 = A.System(A.Pool(100_000.0,1_000.0))
s2.user("a").dero = 100_000; s2.user("b").dero = 10_000
s2.mint("a",100_000); s2.mint("b",10_000)
log(f"  before+after expiry fee accrual check (a=100k mint, b=10k):")
log(f"  a committed={s2.positions['a'].committed_days:.2f}, b={s2.positions['b'].committed_days:.2f}")
s2.advance(700)  # a (444d lock) expires, b (30d) expired long ago
w_before = {k: p.weight for k,p in s2.positions.items()}
# emulate a fee event via a small swap with the fee routed (all positions expired)
s2.user("dealer").dero = 200.0
s2.swap("dealer","dero_for_dusd",200.0)
share = {k: v for k,v in s2.positions.items() if v.accrued_dusd or getattr(v,'accrued_dero',0)}
log(f"  fee swap after expiry: claims={ {k: (round(v.accrued_dusd,4), round(getattr(v,'accrued_dero',0),4)) for k,v in share.items()} }")
s2.swap("dealer","dusd_for_dero", s2.user("dealer").dusd)
share2 = {k: v for k,v in s2.positions.items() if v.accrued_dusd or getattr(v,'accrued_dero',0)}
log(f"  fee swap both-directions after expiry: total claims={ {k: (round(v.accrued_dusd,4), round(getattr(v,'accrued_dero',0),4)) for k,v in share2.items()} }")
# re-lock with tiny amount right after expiry of big vault
s3 = A.System(A.Pool(100_000.0,1_000.0))
s3.user("big").dero = 1_000_000; s3.mint("big",1_000_000)
s3.advance(30)  # still active (966d)
s3.user("tiny").dero = 1.0; s3.mint("tiny",1.0)
s3.user("dealer").dero = 200_000.0
s3.swap("dealer","dero_for_dusd",200_000.0)
sh = {k: round(v.accrued_dusd + getattr(v,'accrued_dero',0),4) for k,v in s3.positions.items() if v.accrued_dusd or getattr(v,'accrued_dero',0)}
log(f"  fee 1000 split big(t1y)+tiny: weights big={s3.positions['big'].weight:,.0f} tiny={s3.positions['tiny'].weight:,.0f} claims={sh}")

# ---------------- S7 FEE ACCOUNTING ----------------
log("== S7. FEE ACCOUNTING (unit integrity) ==")
# use fixed sim route_fee (native)
for direction, amt in [("dusd_for_dero",1.0),("dusd_for_dero",1000.0),("dusd_for_dero",1e6),
                       ("dero_for_dusd",1.0),("dero_for_dusd",1000.0),("dero_for_dusd",1e6)]:
    s = A.System(A.Pool(100_000.0,1_000.0))
    s.user("t").dusd = amt if direction=="dusd_for_dero" else 0
    s.user("t").dero = amt if direction=="dero_for_dusd" else 0
    before_d, before_dd = s.pool.fees_dero, s.pool.fees_dusd
    r = s.swap("t", direction, amt)
    if "blocked" in r:
        log(f"  {direction} {amt:,.0f}: blocked"); continue
    f1, f2 = s.pool.fees_dero-before_d, s.pool.fees_dusd-before_dd
    # expected native fee
    exp_dero = amt*0.003 if direction=="dero_for_dusd" else 0
    exp_dusd = amt*0.003 if direction=="dusd_for_dero" else 0
    correct = abs(f1-exp_dero)<1e-9 and abs(f2-exp_dusd)<1e-9
    # distributed claims vs insurance backing
    dist = s.insurance_dusd + s.fees_for_backers + getattr(s,'pol_growth_dusd',0) + getattr(s,'pol_growth_dero',0)
    log(f"  {direction} {amt:,.0f}: fee_dero={f1:.4f} fee_dusd={f2:.4f} exp=({exp_dero:.4f},{exp_dusd:.4f}) natural_units={'OK' if correct else 'MISMATCH'}")
# insurance for DERO fee stays DERO?
s = A.System(A.Pool(100_000.0,1_000.0))
s.user("t").dero = 1000.0
s.swap("t","dero_for_dusd",1000.0)
log(f"  dero fee -> insurance_dero={getattr(s,'insurance_reserve_dero',0.0):.4f} insurance_dusd={s.insurance_dusd:.4f} (spec: 5% of fee, must stay native)")

# ---------------- S8/S9 AMM manipulation & reversibility ----------------
log("== S8+S9. AMM MANIPULATION & REVERSIBILITY ==")
def pump_dump(target_mult, pool_xy=(100_000.0,1_000.0), with_fees=True):
    s = A.System(A.Pool(*pool_xy))
    s.user("whale").dusd = 1e9; s.user("whale").dero = 1e9
    P0s = s.pool.spot
    # pump: pay DUSD to buy DERO till price reaches target
    cost = 0.0
    steps = 0
    while s.pool.spot < P0s*target_mult and steps < 500:
        need = (target_mult*P0s - s.pool.spot)*s.pool.spot + 1  # rough
        r = s.swap("whale","dusd_for_dero", s.user("whale").dusd*0.5)
        cost += 0
        steps += 1
        if s.pool.spot == 0: break
    # exact single-shot: solve Y needed
    X0,Y0 = s.pool.dero, s.pool.dusd
    return s, X0, Y0, cost, steps

log("  price reachability (exact closed-form):")
for mult in [5,100,1000,10000,100000]:
    # Y needed for price P = mult*P0 given K = X0*Y0
    X0,Y0 = 100_000.0, 1_000.0
    K = X0*Y0
    P_target = mult*0.01
    Y_target = math.sqrt(K*P_target)
    need = Y_target - Y0
    X_final = K/Y_target
    log(f"  target P={P_target:,.0f} (x{mult}): need DUSD inflow={need:,.2f} leftover X={X_final:,.2f}")

# ideal round trip with fee chain
log("  round trips (whale full buy/sell incl fees):")
for mult in [5,100,1000]:
    s = A.System(A.Pool(100_000.0,1_000.0))
    s.user("whale").dusd = 1e9; s.user("whale").dero = 1e9
    P0s = 0.01
    # continuous feed until price = mult*P0s
    guard = 0
    while s.pool.spot < mult*P0s and guard<2000:
        # compute exact amount to reach target
        X,Y = s.pool.dero, s.pool.dusd
        K = X*Y
        Yt = math.sqrt(K*(mult*P0s))
        amt = Yt - Y
        if amt <= 1e-9: break
        rr = s.swap("whale","dusd_for_dero", amt)
        guard += 1
    P_high = s.pool.spot
    dero_bought = s.user("whale").dero
    dusd_spent = s.user("whale").dusd  # decreased
    # sell back all bought DERO
    rr = s.swap("whale","dero_for_dusd", dero_bought)
    P_end = s.pool.spot
    log(f"  x{mult}: high={P_high:.4f} bought_dero={dero_bought:,.2f} sold_back={rr.get('out',0):,.2f} end_spot={P_end:.6f} drift={(P_end/P0s-1)*100:.2f}%")

# ---------------- S10 CRASH LADDER ----------------
log("== S10. CRASH LADDER ==")
s = A.System(A.Pool(100_000.0,1_000.0))
s.user("alice").dero = 1_000_000.0
s.mint("alice", 1_000_000.0)
debt = s.positions["alice"].debt
coll = s.positions["alice"].collateral
ins = s.insurance_dusd
ins_dero = getattr(s,'insurance_reserve_dero',0.0)
log(f"  vault: collateral={coll:,.2f} DERO debt={debt:.2f} DUSD insurance_dusd={ins:.2f} insurance_dero={ins_dero:.2f}")
log(f"  insolvency threshold: col*price==debt -> price_break={debt/coll:.6f} ({debt/coll/0.01*100:.1f}% of P0)")
for crash in [0.10,0.25,0.50,0.75,0.90,0.95,0.99]:
    price = 0.01*(1-crash)
    val = coll*price
    cr = val/debt
    unbacked = max(0.0, debt-val)
    log(f"  crash -{crash*100:>3.0f}%: price={price:.5f} collval={val:,.2f} CR={cr:.4f} unbacked={unbacked:,.2f}")


print("\n".join(OUT))
open("/home/ahmed/Downloads/DUSD-V0.4/TEST-S6-S10.txt","w").write("\n".join(OUT))