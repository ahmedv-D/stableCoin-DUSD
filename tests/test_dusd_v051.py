import random
import math
import pytest
from core.dusd_v051_state import *


def seeded_state():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 10.0
    s.record_spot()
    s.insurance.dusd = 250.0
    s.vaults["alice"] = Vault(collateral=10000.0)
    s.total_deposited_dero = 10000.0
    return s


def test_genesis_mint_and_coverage_gate():
    s = seeded_state()
    before = s.conservation_assets()
    r = s.deposit_and_mint("bob", 100.0)
    assert r["gross"] == pytest.approx(0.72)
    assert r["user_dusd"] < r["gross"]
    assert s.total_pol_dero_contributed == pytest.approx(r["pol_dero"])
    assert s.conservation_assets() == pytest.approx(before + 100.0)
    assert s.coverage() >= MIN_COVERAGE


def test_no_reprice_of_existing_debt_on_price_move():
    s = seeded_state()
    s.vaults["alice"].debt = 100.0
    d0 = s.vaults["alice"].debt
    s.swap_dusd_for_dero(5.0)
    assert s.vaults["alice"].debt == pytest.approx(d0)


def test_dynamic_lock_curve_and_global_split_pressure():
    s = seeded_state()
    a = s.pressure_ratio(10)
    one = lock_days(a)
    vals = []
    for x in [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]:
        vals.append(lock_days(s.pressure_ratio(x)))
        s.rolling_mints.append((s.now, x))
    split = vals[-1]
    assert split >= lock_days(a * 0.05)
    assert one >= 30 and one <= 1095


def test_fee_split_native_units():
    s = seeded_state()
    d0 = s.pol.dero
    r = s.swap_dero_for_dusd(10.0)
    fee = r["fee"]
    assert s.fee_pol_growth_dero + s.fee_backer_pool_dero + s.fee_insurance_dero == pytest.approx(fee * 0.30)
    assert s.insurance.dero == pytest.approx(fee * 0.05)
    assert s.pol.dero > d0


def test_pari_passu_redemption_and_no_overpay():
    s = seeded_state()
    s.outstanding_dusd = 100.0
    s.insurance.dusd = 20.0
    s.vaults["alice"].debt = 80.0
    before = s.backing_nav()
    r1 = s.redeem("r1", 20.0)
    r2 = s.redeem("r2", 20.0)
    assert r1["claim_factor"] == pytest.approx(r2["claim_factor"])
    assert r1["paid_dusd"] + r1["paid_dero"] * r1["risk_price"] <= r1["target_value"] + 1e-8
    assert s.backing_nav() <= before + 1e-8
    assert s.outstanding_dusd == pytest.approx(60.0)


def test_under_collateralized_haircut_is_explicit():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 1.0
    s.insurance.dusd = 0.0
    s.vaults["a"] = Vault(collateral=100.0)
    s.outstanding_dusd = 1000.0
    s.record_spot()
    r = s.redeem("a", 100.0)
    assert r["claim_factor"] < 1.0
    assert r["unpaid_value"] >= 0.0


def test_provenance_exclusion_blocks_fresh_collateral_capacity():
    s = seeded_state()
    r = s.deposit_and_mint("bob", 100.0)
    s.set_provenance_exclusion(r["pol_dero"])
    assert s.eligible_collateral() < sum(v.collateral for v in s.vaults.values())


def test_failed_mint_is_atomic():
    s = ProtocolState()
    s.pol.dero = 10.0
    s.pol.dusd = 0.1
    s.vaults["a"] = Vault(collateral=1.0)
    s.outstanding_dusd = 249999.99
    snap = s.snapshot()
    with pytest.raises(ValueError):
        s.deposit_and_mint("a", 1000.0)
    assert s.snapshot() == snap


def test_property_like_random_ops_conserve_dero_and_bound_supply():
    random.seed(20260915)
    for _ in range(250):
        s = seeded_state()
        initial = s.conservation_assets()
        for _step in range(200):
            op = random.choice(["mint", "swapd", "swapx", "advance"])
            try:
                if op == "mint":
                    s.deposit_and_mint(random.choice(["u1", "u2", "u3"]), random.choice([0.01, 0.1, 1.0, 5.0]))
                elif op == "swapd":
                    if s.pol.dusd > 0.001 and s.pol.dero > 0.001:
                        s.swap_dusd_for_dero(min(1.0, s.pol.dusd * 0.1))
                elif op == "swapx":
                    if s.pol.dusd > 0.001 and s.pol.dero > 0.001:
                        s.swap_dero_for_dusd(min(1.0, s.pol.dero * 0.1))
                else:
                    s.advance(random.choice([0.0, 0.1, 1.0, 5.0]))
            except ValueError:
                pass
            assert s.outstanding_dusd <= GLOBAL_CEILING + 1e-8
            assert s.conservation_assets() >= -1e-8
        # swap fees are protocol-controlled value; no negative inventory.
        assert s.pol.dero >= -1e-8 and s.pol.dusd >= -1e-8
        assert s.insurance.dero >= -1e-8 and s.insurance.dusd >= -1e-8
        assert s.conservation_assets() <= initial + sum(v.collateral for v in s.vaults.values()) + 1e-6


def test_twap_is_not_spot_after_manipulation():
    s = seeded_state()
    p0 = s.spot()
    s.advance(1)
    s.swap_dusd_for_dero(5.0)
    p1 = s.spot()
    assert p1 != pytest.approx(p0)
    assert s.twap() != pytest.approx(p1)


def test_redemption_does_not_burn_more_than_outstanding():
    s = seeded_state()
    s.outstanding_dusd = 50.0
    r = s.redeem("x", 50.0)
    assert s.outstanding_dusd == pytest.approx(0.0)
    assert r["requested"] == 50.0
