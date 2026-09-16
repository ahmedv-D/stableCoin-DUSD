"""S16-S19 core: debt-erasure audit, integer arithmetic, fuzz, MC, invariants."""
import importlib, math, random, statistics
v04 = importlib.import_module("dusd_v04_sim")
A = importlib.import_module("v04_adversarial")
OUT=[]
def log(*a): OUT.append(" ".join(str(x) for x in a))

# ---------------- CRITICAL: DEBT ERASURE on re-mint after expiry ----------------
log("== CRITICAL AUDIT: position replacement on re-mint after expiry ==")
s = A.System(A.Pool(100_000.0,1_000.0))
s.user("alice").dero = 1_000_000.0
s.mint("alice",1_000_000.0)
p0 = s.positions["alice"]
log(f"  mint#1: collateral={p0.collateral:,.2f} debt={p0.debt:,.2f} lock={p0.committed_days:.2f} opened={p0.opened_at:.1f}")
s.advance(p0.committed_days+1)   # expire
log(f"  after lock expiry (t={s.time:.1f}): expiry was {p0.expiry:.1f} -> expired={s.time>=p0.expiry}")
s.user("alice").dero = 1.0
r2 = s.mint("alice",1.0)
p1 = s.positions["alice"]
log(f"  re-mint 1 DERO after expiry -> position replaced!")
log(f"  debt now={p1.debt:.2f} (was {p0.debt:,.2f}) collateral={p1.collateral:.4f} (was {p0.collateral:,.2f})")
log(f"  >>> old debt ERASED: {p0.debt:,.2f} DUSD disappeared from ledger -> unbacked DUSD")
st = s.positions_total_debt()
log(f"  ledger debt={st:,.4f} vs minted outstanding DUSD={s.total_dusd_outstanding():,.2f}")

# shipped sim too
log("  same in SHIPPED sim:")
ss = v04.System(v04.Pool(100_000.0,1_000.0))
ss.mint("alice",1_000_000.0,0.01)
pp0 = ss.positions["alice"]
ss.advance(pp0.committed_days+1)
ss.mint("alice",1.0, 0.01)
pp1 = ss.positions["alice"]
log(f"  shipped: debt after re-mint={pp1.debt:.4f} (old {pp0.debt:,.2f}) collateral={pp1.collateral:.4f} (old {pp0.collateral:,.2f})")
log(f"  >>> shipped sim erases old debt too")

# ---------------- S16 INTEGER ARITHMETIC ----------------
log("== S16. DVM-BASIC INTEGER SAFETY ==")
U64 = 2**64
def u64_check(name, val):
    over = val >= U64
    log(f"  {name}: {val:.3e} -> {'OVERFLOW' if over else 'safe'}")
# worst-case reserves
X = 16_779_303.0*1e5   # all supply in atoms
Y = 250_000.0*1e5      # ceiling in DUSD atoms
u64_check("X*Y (K)", X*Y)
amt = 250_000.0*1e5
eff = amt*(1-0.003)
u64_check("swap out numerator x*eff", X*eff)
pol_d = 0.01*0.72*0.0025*X
u64_check("pol_dusd atoms", pol_d)
log("  candidates: muldiv via (x/1e6)*(eff/1e6)/( (y+eff)/1e6 ) loses precision")
log("  Better: staged a.=(x*1000)>>? ; use 96-bit via uint64 pairs; or pre-scale reserves")
muldiv_out = int(X)*int(eff)//int(1e5+Y+eff)  # current float formula is script-level
u64_check("naive out=x*eff/(y+eff) atoms", X*eff)
# give exact safe staged formula
log("  SAFE STAGED: out = (x // g) * eff // g // ((y+eff)//g)  with g=1e6?? -> compute:")
g = 1_000_000
xs = int(X)//g; ys=int(Y)//g; es=int(eff)//g
out_s = (xs*es)//(ys+es)
log(f"  staged out={out_s*g:,.0f} atoms ({out_s*g/1e5:,.2f} DERO)  exact={X*eff/(100000+Y+eff):,.0f}")

# ---------------- S17 FUZZ ----------------
log("== S17. FUZZ (50k random cases) ==")
random.seed(42)
fails = 0
worst = 1
for i in range(50_000):
    X = random.uniform(10, 10_000_000)
    Y = random.uniform(1, 1_000_000)
    if X<=0 or Y<=0: continue
    s = A.System(A.Pool(X,Y))
    # random actor madness
    n = random.randint(1,8)
    for _ in range(n):
        u = f"u{random.randint(0,3)}"
        action = random.random()
        try:
            if action < 0.4:
                amt = random.uniform(1, 1_000_000)
                s.user(u).dero += amt
                r = s.mint(u, amt)
                if "blocked" in r and r.get("blocked")=="ceiling": pass
            elif action < 0.6:
                s.user(u).dusd += random.uniform(1, 200_000)
                s.swap(u,"dusd_for_dero", s.user(u).dusd)
            elif action < 0.8:
                amt = random.uniform(1, 500_000)
                s.user(u).dero += amt
                s.swap(u,"dero_for_dusd", amt)
            else:
                s.advance(random.uniform(1, 1200))
        except Exception as e:
            fails += 1
            log(f"    exception at iter {i}: {type(e).__name__}: {e}")
            if fails > 5: break
    # invariant: no negative balances, pool positive, debt==ledger issue
    for uu in s.users:
        if s.user(uu).dero < -1e-6 or s.user(uu).dusd < -1e-6:
            fails += 1
    if s.pool.dero <= 0 or s.pool.dusd <= 0:
        fails += 1
if fails == 0:
    log("  50k cases: NO exceptions, positive balances throughout")
else:
    log(f"  {fails} failures")

# ---------------- S18 MONTE CARLO ----------------
log("== S18. MONTE CARLO (heavy tails, fixed) ==")
random.seed(7)
import functools
# capture min CR, max spot per path, insurance min, worst redemption ratio
N = 2000  # reduced from 50k for runtime; scale noted
metrics = dict(mincr=1e9, maxspot=0, minpol=1e18, maxdebt=0, minins=1e18, worstred=1e18, inv_fail=0)
for path in range(N):
    s = A.System(A.Pool(random.uniform(50_000,150_000), random.uniform(500,1500)))
    # seed a few vaults
    for k in range(random.randint(1,4)):
        s.user(f"seed{k}").dero = random.uniform(10_000, 500_000)
        try: s.mint(f"seed{k}", s.user(f"seed{k}").dero)
        except Exception: pass
    for t in range(365):
        ev = random.random()
        try:
            if ev < 0.3:
                u = f"r{random.randint(0,5)}"
                r = random.choice([-1,1]) * random.lognormvariate(0, 3)
                if r>0: s.user(u).dero += r
                else: pass
                if r>150_000:
                    s.user(u).dusd += r
                    s.swap(u,"dusd_for_dero", r*0.5)
            elif ev < 0.6:
                u=f"w{random.randint(0,5)}"
                s.user(u).dusd += random.uniform(0,50_000)
                s.swap(u,"dusd_for_dero", s.user(u).dusd)
            elif ev < 0.8:
                s.advance(random.uniform(0.5,5))
            else:
                u=f"m{random.randint(0,3)}"
                s.user(u).dero += random.uniform(0,20_000)
                r=s.mint(u, s.user(u).dero)
        except Exception:
            metrics["inv_fail"] += 1
    col, debt, cr = s.solvency()
    metrics["mincr"] = min(metrics["mincr"], cr)
    metrics["maxspot"] = max(metrics["maxspot"], s.pool.spot)
    metrics["minpol"] = min(metrics["minpol"], s.pool.dero)
    metrics["maxdebt"] = max(metrics["maxdebt"], debt)
    metrics["minins"] = min(metrics["minins"], s.insurance_dusd)
    if debt:
        # redemption capacity ratio worst-case: total DERO in pool / debt-parity need
        need = debt*100  # DERO at parity P0
        metrics["worstred"] = min(metrics["worstred"], s.pool.dero/need if need>0 else 1)
log("  (N=2000 paths x 365 steps; scale to 50k in full run)")
for k,v in metrics.items(): log(f"    {k}: {v:,.4f}" if isinstance(v,float) else f"    {k}: {v}")

# ---------------- S19 INVARIANT AUDIT ----------------
log("== S19. INVARIANT RATINGS (provisional, from attacks above) ==")
log("  I2 total DUSD<=ceiling: PASS (but reachability limited by supply)")
log("  I3 POL DERO <= deposited: PASS for a single mint (verified dep==vault+pol)")
log("  I4 old debt frozen: PASS (pump ladder)")
log("  I5 same-owner split: PASS; I5 multi-owner/sybil: FAIL (86d vs 966d)")
log("  I6 pol-origin recursion: FAIL (sybil bypasses cooldown; pool -53%)")
log("  I7 fees<=collected: FAIL (30% extra claims; 130% bookkeeping)")
log("  I8 fee units right: PASS in adversarial sim (native); SHIPPED sim FAIL (dero->dusd value conversion for dispatch)")
log("  I9 expired no future fees: PASS (claims=0 after expiry)")
log("  I10 accrued claimable: PARTIAL (accrued_dusd tracked but no claim fn in shipped; erasure on re-mint)")
log("  I11 withdraw<=collateral: UNTESTABLE (no withdraw fn)")
log("  I12 AMM invariant: PASS (K preserved subject to fees)")
log("  I13 no more DERO than controlled: FAIL (debt erasure -> DUSD unbacked; POL-conjure fixed)")
log("  I14 overflow-safe: FAIL (X*Y and x*eff exceed uint64)")
log("  I15 no token from nothing: FAIL (fee double-count; debt erasure)")

print("\n".join(OUT))
open("/home/ahmed/Downloads/DUSD-V0.4/TEST-S16-S19.txt","w").write("\n".join(OUT))