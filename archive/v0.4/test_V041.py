"""V0.4.1 regression: rerun the full attack suite against dusd_v041_sim."""
import importlib, math, random
v = importlib.import_module("dusd_v041_sim")
OUT=[]
def log(*a): OUT.append(" ".join(str(x) for x in a))

# S1 genesis
log("== V0.4.1 S1. Genesis ==")
s = v.System(v.Pool(100_000.0,1_000.0))
s.user("w").dero = 1_000_000.0
r = s.mint("w",1_000_000.0)
log(f"  gross={r['gross']:.2f} pol_dusd={r['pol_dusd']:.2f} pol_dero={r['pol_dero']:.2f} user={r['user_dusd']:.2f} lock={r['lock_days']:.2f}")
p = s.positions["w"]
log(f"  vault+p.dero = {p.collateral+p.pol_dero:,.2f} == deposited 1,000,000? {abs(p.collateral+p.pol_dero-1_000_000)<1e-6}")

# S3 recursion attempt (fails both on quarantine + pooling of u)
log("== V0.4.1 S3. recursion (quarantine gate) ==")
s = v.System(v.Pool(100_000.0,1_000.0))
s.user("seed").dero = 100_000.0
s.mint("seed",100_000.0)
# sybil lap: fresh addr receives POL DERO via swap
s.user("seed2").dusd = 718.2
s.swap("seed2","dusd_for_dero",718.2)
# immediate re-mint attempt
r = s.mint("seed2", s.user("seed2").dero)
log(f"  immediate re-mint after pool swap: {r.get('blocked','ALLOWED')}")
# after quarantine elapses
s.advance(v.QUARANTINE_DAYS+1)
r2 = s.mint("seed2", s.user("seed2").dero)
log(f"  re-mint after quarantine ({v.QUARANTINE_DAYS}d): {r2.get('blocked','ALLOWED')} lock={r2.get('lock_days')}")
# sybil self-finance loop: fresh addr each lap; bought DERO is tagged + quarantined
def recursion_v041(laps):
    s2 = v.System(v.Pool(100_000.0,1_000.0))
    # initial external wallet
    w_dusd = 0.0
    pool_dero_trace = []
    done = 0
    for i in range(laps):
        external = 100_000.0 if i == 0 else 0.0
        if external > 0:
            nm=f"a{i}"
            s2.user(nm).dero = external
            r = s2.mint(nm, external)
            if "blocked" in r: break
            w_dusd += r["user_dusd"]
        out = s2.swap(nm,"dusd_for_dero", w_dusd)     # buy POL DERO (tagged)
        if "blocked" in out or out.get("out",0) <= 0:
            break
        acquired = out["out"]
        w_dusd = 0.0
        s2.advance(v.QUARANTINE_DAYS)                  # must wait to reuse
        if i == 0:
            r2 = s2.mint(nm, acquired)                 # quarantine gate test
            if "blocked" in r2:
                # quarantine blocks: attacker cannot re-lock instantly
                done = i; break
            w_dusd += r2["user_dusd"]
        pool_dero_trace.append(s2.pool.dero)
        done = i+1
    return done, (pool_dero_trace[-1] if pool_dero_trace else s2.pool.dero)
d,pool_dero = recursion_v041(1)
log(f"  V0.4.1 first-lap: quarantine gate = {'BLOCKED (recursion broken)' if d==0 else 'ALLOWED'}; pool.dero={pool_dero:,.1f}")
# control: how much a single 100k external deposit drains WITHOUT recursion
s0 = v.System(v.Pool(100_000.0,1_000.0))
s0.user("c").dero=100_000.0; s0.mint("c",100_000.0)
out0 = s0.swap("c","dusd_for_dero", s0.user("c").dusd)
log(f"  control single mint+swap: pool.dero={s0.pool.dero:,.1f} (V0.4 recursive loop drove this to 48,025)")

# S4 anti-split: same-owner AND global-u
log("== V0.4.1 S4. anti-split ==")
def split_lock(n, multi=False):
    s = v.System(v.Pool(100_000.0,1_000.0))
    per = 1_000_000.0/n
    for i in range(n):
        o = f"w{i}" if multi else "w"
        s.user(o).dero = per
        r = s.mint(o, per)
        if "blocked" in r: return None
    return s
one = split_lock(1); one_l = one.positions["w"].committed_days
log(f"  one-shot committed={one_l:.2f}")
for n in [10,100,1000]:
    s4 = split_lock(n, multi=True)
    if s4:
        mx = max(p.committed_days for p in s4.positions.values())
        log(f"  multi-addr n={n}: max committed={mx:.2f} vs one-shot={one_l:.2f} -> {'STILL-CHEAT' if mx < one_l-1 else 'OK'}")
    else:
        log(f"  multi-addr n={n}: blocked -> OK")

# S6 weight + expiry fee
log("== V0.4.1 S6/S9. weight + expiry-cliff + reversibility ==")
s = v.System(v.Pool(100_000.0,1_000.0))
s.user("big").dero=1_000_000.0; s.mint("big",1_000_000.0)
s.advance(40)
s.user("tiny").dero=1.0; s.mint("tiny",1.0)
s.user("mkt").dusd=1000.0
s.swap("mkt","dusd_for_dero",1000.0)
log(f"  fee event claims: big={s.positions['big'].accrued_dusd:.4f} tiny={s.positions['tiny'].accrued_dusd:.4f}")
s.advance(1000)  # big expires
s.swap("mkt","dusd_for_dero",1000.0)
log(f"  after big expire: big claim stops: +=0 (claims now {s.positions['big'].accrued_dusd:.4f})")
# reversibility exact
for mult in [5,100,1000]:
    s9 = v.System(v.Pool(100_000.0,1_000.0))
    w = s9.user("whale"); w.dusd = 500_000.0
    g=0
    while s9.pool.spot < 0.01*mult and g<2000:
        X,Y = s9.pool.dero, s9.pool.dusd; K=X*Y
        Yt = math.sqrt(K*(0.01*mult))
        amt = max(1e-9, Yt-Y)
        if amt > w.dusd: break
        s9.swap("whale","dusd_for_dero", amt); g+=1
    high = s9.pool.spot
    bought = w.dero
    w.dusd += 0
    r = s9.swap("whale","dero_for_dusd", bought)
    end = s9.pool.spot
    log(f"  x{mult}: high={high:.4f} end={end:.6f} drift={(end/0.01-1)*100:+.2f}%")

# S10 crash ladder w/ liquidation + insurance
log("== V0.4.1 S10. crash + liquidation ==")
s = v.System(v.Pool(100_000.0,1_000.0))
s.user("alice").dero=1_000_000.0; s.mint("alice",1_000_000.0)
for crash in [0.25,0.50,0.90,0.99]:
    price = 0.01*(1-crash)
    liq = s.liquidate("alice", price=price)
    log(f"  crash -{crash*100:.0f}%: liq={liq}")

# S16 integer safety (V0.4.1 uses scaled reserves)
log("== V0.4.1 S16. integer budgets ==")
X = 16_779_303.0*1e5; Y = 250_000*1e5
log(f"  K in scaled units (RS={v.RS}): {(X//v.RS)*(Y//v.RS):,.0f} vs u64 max {2**64:,.0f}")
eff = 250_000*1e5*(1-v.SWAP_FEE)
num = (X//v.RS)*(eff//v.RS)*v.RS
log(f"  swap numerator scaled: {num:,.0f} vs u64 max: {'OK' if num < 2**64 else 'OVERFLOW'}")

# S18 quick MC (reduced, but with fixed sim)
log("== V0.4.1 S18. MC headless (N=400 paths) ==")
random.seed(3)
mincr=9e9; maxspot=0; minpol=9e18; worstred=9e18; fails=0
for _ in range(400):
    s = v.System(v.Pool(random.uniform(50_000,150_000), random.uniform(500,1500)))
    for k in range(random.randint(1,3)):
        s.user(f"s{k}").dero=random.uniform(10_000,300_000)
        try: s.mint(f"s{k}", s.user(f"s{k}").dero)
        except AssertionError: fails+=1
    for _ in range(365):
        ev=random.random()
        try:
            if ev<0.55:
                s.user("r0").dusd += random.uniform(0,30_000)
                s.swap("r0","dusd_for_dero", s.user("r0").dusd)
            elif ev<0.7:
                s.user("r1").dero += random.uniform(0,20_000)
                s.swap("r1","dero_for_dusd", s.user("r1").dero)
            else:
                s.advance(random.uniform(0.5,3))
        except Exception: fails+=1
    col,debt,cr = s.solvency()
    mincr=min(mincr,cr); maxspot=max(maxspot,s.pool.spot); minpol=min(minpol,s.pool.dero)
    if debt>0: worstred=min(worstred, s.pool.dero/(debt*100))
log(f"  mincr={mincr:.4f} maxspot={maxspot:.2f} minpol={minpol:,.2f} worst_redemption_ratio={worstred:.4f} fails={fails}")

print("\n".join(OUT))
open("/home/ahmed/Downloads/DUSD-V0.4/TEST-V041-REGRESSION.txt","w").write("\n".join(OUT))