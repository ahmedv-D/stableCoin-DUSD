"""S18 heavy Monte Carlo for V0.4.1 (runs to completion, writes file)."""
import sys, random
sys.path.insert(0, "/home/ahmed/Downloads/DUSD-V0.4")
import dusd_v041_sim as v

random.seed(1234)
N = 50_000
m = {'mincr':9e9,'maxspot':0,'minpol':9e18,'maxdebt':0,'minins':9e18,'worstred':9e18,'fail':0}
for p in range(N):
    s = v.System(v.Pool(random.uniform(50_000,150_000), random.uniform(500,1500)))
    for k in range(random.randint(0,3)):
        s.user(f's{k}').dero = random.uniform(10_000,300_000)
        try: s.mint(f's{k}', s.user(f's{k}').dero)
        except Exception: m['fail'] += 1
    for t in range(60):
        ev = random.random()
        try:
            if ev < 0.45:
                s.user('r0').dusd += random.uniform(0,50_000)
                s.swap('r0','dusd_for_dero', s.user('r0').dusd)
            elif ev < 0.6:
                s.user('r1').dero += random.uniform(0,30_000)
                s.swap('r1','dero_for_dusd', s.user('r1').dero)
            elif ev < 0.8:
                s.advance(random.uniform(0.5,5))
            else:
                u = f'm{random.randint(0,2)}'
                s.user(u).dero += random.uniform(0,20_000)
                s.mint(u, s.user(u).dero)
        except Exception:
            m['fail'] += 1
    col, debt, cr = s.solvency()
    m['mincr']  = min(m['mincr'], cr)
    m['maxspot']= max(m['maxspot'], s.pool.spot)
    m['minpol'] = min(m['minpol'], s.pool.dero)
    m['maxdebt']= max(m['maxdebt'], debt)
    m['minins'] = min(m['minins'], s.pool.ins_dusd)
    if debt > 0:
        m['worstred'] = min(m['worstred'], s.pool.dero/(debt*100))
with open("/home/ahmed/Downloads/DUSD-V0.4/MC-V041.txt","w") as f:
    for k, vv in m.items():
        f.write(f"{k}: {vv:,.6g}\n")
print("done", m)