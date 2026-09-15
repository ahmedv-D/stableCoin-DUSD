"""S11-S15: Pump ladder, Bank run, Insurance, TWAP, Ceiling."""
import importlib, math
v04 = importlib.import_module("dusd_v04_sim")
A = importlib.import_module("v04_adversarial")
OUT=[]
def log(*a): OUT.append(" ".join(str(x) for x in a))

# ---------------- S11 PUMP LADDER ----------------
log("== S11. PUMP LADDER (old debt must not rise; indirect new debt) ==")
def pump_to(s, target):
    guard=0
    while s.pool.spot < target and guard<500:
        X,Y=s.pool.dero,s.pool.dusd; K=X*Y
        Yt=math.sqrt(K*target)
        amt=Yt-Y
        if amt<=1e-9: break
        s.swap("whale","dusd_for_dero",amt)
        guard+=1
    return s.pool.spot

for mult in [2,10,100,1000]:
    s = A.System(A.Pool(100_000.0,1_000.0))
    s.user("alice").dero = 1_000_000.0
    s.user("whale").dusd = 1e9
    s.mint("alice",1_000_000.0)
    debt_before = s.positions["alice"].debt
    P_target = 0.01*mult
    got = pump_to(s, P_target)
    debt_after = s.positions["alice"].debt
    log(f"  pump x{mult}: spot={got:.5f} alice_debt before={debt_before:.2f} after={debt_after:.2f} fmt={'FIXED' if abs(debt_after-debt_before)<1e-9 else 'CHANGED!!!'}")

# indirect: can pumped spot create NEW debt via new collateral + pol-origin?
log("  buy->relock->mint loop under pump (fresh addr each lap):")
for laps in [1,3,5]:
    s = A.System(A.Pool(100_000.0,1_000.0))
    s.user("seed").dero=100_000.0; s.mint("seed",100_000.0)
    inflow = 100_000.0
    for i in range(laps):
        nm=f"p{i}"
        s.user(nm).dero=inflow
        r=s.mint(nm,inflow); 
        if "blocked" in r: break
        rr=s.swap(nm,"dusd_for_dero",r["user_dusd"])
        inflow=rr.get("out",0)
    log(f"  laps={laps}: pool.dero={s.pool.dero:,.2f} spot={s.pool.spot:.4f} debt_poolside={s.positions_total_debt():,.2f}")

# ---------------- S12 BANK RUN ----------------
log("== S12. BANK RUN (separate POL liquidity from protocol collateral) ==")
def bankrun(pct):
    s = A.System(A.Pool(100_000.0,1_000.0))
    # create broad base: 2 x 1M mints
    s.user("a").dero=1_000_000.0; s.user("b").dero=1_000_000.0
    s.mint("a",1_000_000.0); s.mint("b",1_000_000.0)
    outstanding = s.total_dusd_outstanding()
    # DUSD holders: pool holds pol_dusd + parties got user_dusd
    # simulate pct% of all DUSD trying to exit via swap to DERO
    target = outstanding*pct
    # holders: pool itself is part of DUSD supply; sell user-held DUSD into pool
    s.user("a").dusd  # they hold user_dusd
    pool_dusd0 = s.pool.dusd
    sell_amt = min(target, s.user("a").dusd + s.user("b").dusd)
    s.user("a").dusd = sell_amt  # transfer intent; a sells
    out = s.swap("a","dusd_for_dero", sell_amt) if sell_amt>0 else {"out":0}
    dero_out = out.get("out",0)
    final_pool_d, final_pool_dd = s.pool.dero, s.pool.dusd
    log(f"  run {pct*100:>3.0f}% (target {target:,.0f} DUSD): sold {sell_amt:,.2f} got {dero_out:,.2f} DERO; pool DERO={final_pool_d:,.2f} DUSD={final_pool_dd:,.2f} spot={s.pool.spot:.4f}")

log("  redemption capacity (exact worst-case):")
for pct in [0.10,0.25,0.50,0.75,0.90,0.99,1.0]:
    bankrun(pct)

# ---------------- S13 INSURANCE ----------------
log("== S13. INSURANCE ==")
s = A.System(A.Pool(100_000.0,1_000.0))
s.user("a").dusd = 1_000_000
for i in range(10000):
    s.swap("a","dusd_for_dero", s.user("a").dusd*0.5)
    if s.pool.dero < 100: break
log(f"  after heavy fees: insurance_dusd={s.insurance_dusd:,.2f} pol_growth_dusd={getattr(s,'pol_growth_dusd',0):,.2f} backers_claims={s.fees_for_backers:,.2f} insurance_dero={getattr(s,'insurance_reserve_dero',0):,.2f}")
log(f"  outstanding debt ~{s.positions_total_debt():,.2f} -> insurance covers {s.insurance_dusd/(s.positions_total_debt() or 1)*100:.2f}%")
log("  NOTE: no liquidation/redeem/mint-halt engine exists in V0.4 sim")

# ---------------- S14 TWAP ----------------
log("== S14. TWAP MANIPULATION ==")
def twap_attack(nblocks, amt_each, pool=(100_000.0,1_000.0)):
    s = A.System(A.Pool(*pool))
    s.user("w").dusd = 1e9
    st = s.twap
    for _ in range(nblocks):
        # each block: one block-long price nudge via swap, then advance one block
        r = s.swap("w","dusd_for_dero", amt_each)
        s.advance(18.5/86400.0)  # one block ~18.5s in days
        if r.get("blocked"): break
    return s.twap, st, s.pool.spot

for amt in [100, 1000, 5000, 20000]:   # DUSD per block
    res=[]
    for nb in [1,3,10,50]:
        tw, st, spot = twap_attack(nb, amt)
        res.append(f"{nb}blk->{tw:.5f}(+{(tw/st-1)*100:.1f}%)")
    log(f"  pump {amt:>6} DUSD/blk: " + "  ".join(res))
log("  EMA alpha=0.05 per advance(); cost to move TWAP +50% requires dozens of blocks of pushed spot; each swap also shifts spot immediately")

# ---------------- S15 CEILING ----------------
log("== S15. CEILING REACHABILITY (Phase-1 P0-only issuance) ==")
for supply in [16_779_303, 10_000_000, 20_000_000, 50_000_000, 100_000_000]:
    cap = supply*0.01*0.72
    log(f"  supply={supply:>12,} DERO -> max mintable @P0 = {cap:>13,.0f} DUSD ({cap/v04.GLOBAL_CEILING*100:5.1f}% of 250k ceiling) {'CEILING HIT' if cap>=250000 else 'unreachable'}")
log("  practical: not all supply deposited; realistic issuance far smaller")

print("\n".join(OUT))
open("/home/ahmed/Downloads/DUSD-V0.4/TEST-S11-S15.txt","w").write("\n".join(OUT))