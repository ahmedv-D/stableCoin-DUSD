import os
import sys
import random, math, statistics

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dusd_v051_state import ProtocolState, Vault, GLOBAL_CEILING


def base():
    s=ProtocolState()
    s.pol.dero=1000.0
    s.pol.dusd=10.0
    s.insurance.dusd=0.0
    s.vaults['a']=Vault(collateral=10000.0, debt=90.0)
    s.outstanding_dusd=90.0
    s.record_spot()
    return s


def fuzz(n=100_000, seed=0xD051):
    rng=random.Random(seed)
    violations=0
    attempts=0
    for i in range(n):
        s=base()
        for _ in range(3):
            op=rng.randrange(4)
            try:
                if op==0:
                    s.deposit_and_mint(rng.choice(['a','b','c']), rng.choice([0.001,0.01,0.1,1,10]))
                elif op==1 and s.pol.dusd>1e-6:
                    s.swap_dusd_for_dero(min(s.pol.dusd*0.49, rng.uniform(1e-8,2)))
                elif op==2 and s.pol.dero>1e-6:
                    s.swap_dero_for_dusd(min(s.pol.dero*0.49, rng.uniform(1e-8,20)))
                else:
                    s.advance(rng.uniform(0,1200))
            except Exception:
                attempts += 1
                continue
            attempts += 1
            if not (s.outstanding_dusd <= GLOBAL_CEILING+1e-8 and
                    s.pol.dero >= -1e-8 and s.pol.dusd >= -1e-8 and
                    s.insurance.dero >= -1e-8 and s.insurance.dusd >= -1e-8 and
                    s.eligible_collateral() >= -1e-8):
                violations += 1
                break
    return attempts, violations


def crash_matrix():
    out=[]
    for drop in [0.25,0.50,0.75,0.90,0.95,0.99]:
        s=base()
        p=s.spot()
        risk=p*(1-drop)
        cf=s.claim_factor(risk)
        out.append((drop, risk, s.backing_nav(risk), s.outstanding_dusd, cf, cf*100))
    return out


def monte_carlo(n=50_000, seed=0xD052):
    rng=random.Random(seed)
    floors=[]
    worst=(10, None)
    for _ in range(n):
        s=base()
        # Heavy-tail multiplicative price shock in log space.
        drop=min(0.9999, 1-math.exp(-abs(rng.gauss(0,1.5))))
        if rng.random()<0.15:
            drop=min(0.9999, drop+0.8*rng.random())
        risk=s.spot()*(1-drop)
        cf=s.claim_factor(risk)
        floors.append(cf)
        if cf < worst[0]: worst=(cf, drop)
    return min(floors), statistics.mean(floors), worst[1]

if __name__=='__main__':
    a,v=fuzz()
    print(f'FUZZ attempts={a} violations={v}')
    for row in crash_matrix():
        print('CRASH', row)
    mc=monte_carlo()
    print('MC min_claim_factor, mean_claim_factor, worst_drop=',mc)
