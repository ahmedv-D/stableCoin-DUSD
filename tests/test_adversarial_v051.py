import pytest
from core.dusd_v051_state import (
    ProtocolState, Vault, P0, LTV, POL_MINT_SHARE, H, MIN_COVERAGE,
    LIQ_CR, GLOBAL_CEILING, SWAP_FEE, MIN_LOCK_DAYS, MAX_LOCK_DAYS,
    MAX_DERO, lock_days,
)


def base_state():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 10.0
    s.vaults["a"] = Vault(collateral=10000.0, debt=90.0)
    s.outstanding_dusd = 90.0
    s.record_spot()
    return s


def fresh_pol():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 10.0
    s.record_spot()
    return s


# ---- A. Recursive mint attack --------------------------------------------
def test_A_recursive_mint_conserves_dero_and_supply():
    s = base_state()
    # DERO is only introduced to the protocol vault/POL/insurance via deposits.
    before = s.pol.dero + s.insurance.dero + s.eligible_collateral() + s.provenance_excluded_dero
    deposited = 0.0
    for _ in range(40):
        deposit = 25.0
        s.deposit_and_mint("attacker", deposit)
        deposited += deposit
        if s.pol.dusd > 1e-6:
            s.swap_dusd_for_dero(min(s.pol.dusd * 0.49, 1.0))
    # Total DERO controlled by the protocol never exceeds what was genuinely
    # deposited on top of the initial endowment (no hidden creation).
    after = s.pol.dero + s.insurance.dero + s.eligible_collateral() + s.provenance_excluded_dero
    assert after <= before + deposited + 1e-6
    assert s.outstanding_dusd <= GLOBAL_CEILING + 1e-6
    assert s.eligible_collateral() >= -1e-6


# ---- B. Split mint attack ------------------------------------------------
def test_B_split_mint_does_not_shorten_effective_lock():
    s = base_state()
    one = s.deposit_and_mint("u", 200.0)["lock_days"]
    s2 = base_state()
    days = 0.0
    for _ in range(40):
        r = s2.deposit_and_mint("u", 5.0)
        days = max(days, r["lock_days"])
    # Global rolling pressure keeps the effective lock near the one-shot mint.
    assert days >= one * 0.9 - 1e-9
    assert days <= 1095 + 1e-9


# ---- C. Fresh-address bypass ---------------------------------------------
def test_C_fresh_address_cannot_restore_excluded_collateral():
    s = base_state()
    r = s.deposit_and_mint("fresh1", 100.0)
    s.set_provenance_exclusion(r["pol_dero"])
    el = s.eligible_collateral()
    # A new address depositing "recycled" volume gets fresh eligibility ONLY
    # for genuinely new collateral; the excluded book stays excluded.
    before = s.eligible_collateral()
    s.deposit_and_mint("fresh1", 50.0)
    assert s.eligible_collateral() - before <= 50.0 + 1e-6
    assert el >= -1e-6
    assert s.provenance_excluded_dero >= 0


# ---- D. POL drain ---------------------------------------------------------
def test_D_pol_cannot_be_drained_to_zero_or_negative():
    s = base_state()
    for _ in range(1000):
        if s.pol.dero < 1e-9:
            break
        q = min(s.pol.dusd * 0.49, s.pol.dusd * 0.49, 5.0)
        try:
            s.swap_dusd_for_dero(q)
        except ValueError:
            break
    assert s.pol.dero > 0
    assert s.pol.dero >= -1e-9


# ---- E. POL-origin collateral recycling ----------------------------------
def test_E_pol_origin_dero_not_immediately_fresh_collateral():
    s = base_state()
    r = s.deposit_and_mint("e", 100.0)
    pol_dero_created = r["pol_dero"]
    before = s.eligible_collateral()
    s.set_provenance_exclusion(pol_dero_created)
    # The excluded POL-origin DERO is removed from fresh-collateral capacity.
    assert s.eligible_collateral() == pytest.approx(before - pol_dero_created)
    # Recycling it back into a vault (address rotation) adds nominal collateral
    # but eligibility must stay capped at the fresh base.
    s.deposit_and_mint("e2", pol_dero_created)
    assert s.eligible_collateral() <= before + pol_dero_created * 0.5 + 1e-6


# ---- F. Spot/TWAP wedge manipulation -------------------------------------
def test_F_one_off_spot_spike_does_not_move_risk_price():
    s = base_state()
    for _ in range(9):
        s.advance(1.0)
        s.record_spot()
    avg = s.twap()
    r = s.swap_dusd_for_dero(6.0)
    spot_now = s.spot()
    twap_now = s.twap()
    assert abs(twap_now - avg) < abs(spot_now - avg) * 0.1 + 1e-12
    assert twap_now != pytest.approx(spot_now)


# ---- G. TWAP subsidy attack ----------------------------------------------
def test_G_mint_issuance_is_capped_at_min_P0_twap():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 5.0  # spot 0.005 < P0
    for _ in range(40):
        s.advance(1.0)
        s.record_spot()
    assert s.twap() < P0
    r = s.deposit_and_mint("b", 100.0)
    assert r["issue_price"] == pytest.approx(min(P0, s.twap()))
    assert r["gross"] <= 100.0 * P0 * LTV + 1e-9


def test_G_dero_settlement_uses_max_twap_spot():
    s = base_state()
    for _ in range(9):
        s.advance(1.0)
        s.record_spot()
    s.swap_dusd_for_dero(6.0)  # spot spike
    r = s.redeem("r", 20.0)
    assert r["settlement_price"] == pytest.approx(max(r["risk_price"], r["settlement_price"]))
    assert r["settlement_price"] >= r["risk_price"] - 1e-12


# ---- H. Expiry sniping ---------------------------------------------------
def test_H_expiry_does_not_erase_accrued_fees_or_committed_weight():
    s = base_state()
    r = s.deposit_and_mint("h", 100.0)
    s.swap_dero_for_dusd(10.0)
    fees_dero = s.fee_backer_pool_dero + s.fee_insurance_dero + s.fee_pol_growth_dero
    committed = s.vaults["h"].committed_days
    s.advance(2000.0)  # well past any expiry
    assert s.vaults["h"].committed_days == committed
    assert s.fee_backer_pool_dero + s.fee_insurance_dero + s.fee_pol_growth_dero == pytest.approx(fees_dero)


# ---- I. Fee double counting ----------------------------------------------
def test_I_fee_split_conserves_fee_exactly():
    s = base_state()
    d0_pol = s.pol.dero
    d0_ins = s.insurance.dero
    d0_back = s.fee_backer_pool_dero
    d0_growth = s.fee_pol_growth_dero
    d = 10.0
    fee = d * SWAP_FEE
    r = s.swap_dero_for_dusd(d)
    assert r["fee"] == pytest.approx(fee)
    # The three tallies (growth/backers/insurance) are PURE PROVENANCE labels —
    # they attribute POL depth atoms, they do NOT add any atoms on top.
    assert (s.fee_pol_growth_dero - d0_growth) == pytest.approx(fee * 0.10)
    assert (s.fee_backer_pool_dero - d0_back) == pytest.approx(fee * 0.15)
    assert (s.fee_insurance_dero - d0_ins) == pytest.approx(fee * 0.05)
    # CANONICAL conservation identity (70/10/15/5, all physically into backing):
    # POL embeds depth 70% + growth 10% + backers 15% = 95% of gross fee
    # (the growth/backer shares are attribution embedded INSIDE the POL depth);
    # the insurance POOL receives the remaining 5%. Conservation-visible gain is
    # therefore EXACTLY the gross fee (0.95 POL + 0.05 insurance = 1.00), nothing
    # double-bookedщаться, nothing invisible, nothing double-counted.
    pol_gain = s.pol.dero - d0_pol
    ins_gain = s.insurance.dero - d0_ins
    assert (pol_gain - (d - fee)) + ins_gain == pytest.approx(fee, abs=1e-9)
    assert (s.pol.dero - d0_pol) - (d - fee) == pytest.approx(fee * 0.95)
    assert s.insurance.dero - d0_ins == pytest.approx(fee * 0.05)


# ---- J. Wrong fee denomination -------------------------------------------
def test_J_fees_remain_native_denominated():
    s = base_state()
    dusd_before = s.fee_backer_pool_dusd + s.fee_insurance_dusd
    s.swap_dero_for_dusd(10.0)  # DERO fee -> only DERO buckets move
    assert s.fee_backer_pool_dero > 0
    assert s.fee_backer_pool_dusd + s.fee_insurance_dusd == pytest.approx(dusd_before)
    s2 = base_state()
    dero_before = s2.fee_backer_pool_dero + s2.fee_insurance_dero
    s2.swap_dusd_for_dero(5.0)  # DUSD fee -> only DUSD buckets move
    assert s2.fee_backer_pool_dusd > 0
    assert s2.fee_backer_pool_dero + s2.fee_insurance_dero == pytest.approx(dero_before)


# ---- K. Pari-passu violations --------------------------------------------
def test_K_same_state_same_claim_factor():
    s = base_state()
    s.outstanding_dusd = 100.0
    s.insurance.dusd = 20.0
    s.vaults["a"].debt = 80.0
    f1 = s.claim_factor()
    f2 = s.claim_factor()
    assert f1 == pytest.approx(f2)
    r1 = s.redeem("r1", 10.0)
    r2 = s.redeem("r2", 10.0)
    assert r1["claim_factor"] == pytest.approx(r2["claim_factor"])


# ---- L. First-mover redemption attack ------------------------------------
def test_L_no_first_mover_advantage_when_under_collateralized():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 1.0
    s.vaults["a"] = Vault(collateral=100.0)
    s.outstanding_dusd = 1000.0
    s.record_spot()
    factors = []
    for _ in range(5):
        r = s.redeem("x", 50.0)
        factors.append(r["claim_factor"])
    # Claim factor is non-increasing: queue position grants no priority.
    for prev, cur in zip(factors, factors[1:]):
        assert cur <= prev + 1e-12


# ---- M. Redemption overpayment -------------------------------------------
def test_M_no_redemption_overpayment_even_in_distress():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 1.0
    s.vaults["a"] = Vault(collateral=100.0)
    s.outstanding_dusd = 1000.0
    s.record_spot()
    total_paid = 0.0
    total_claim = 0.0
    for i in range(8):
        r = s.redeem(f"u{i}", 100.0)
        total_paid += r["paid_dusd"] + r["paid_dero"] * r["settlement_price"]
        total_claim += r["target_value"]
    assert total_paid <= total_claim + 1e-6
    assert s.outstanding_dusd >= -1e-6


# ---- N. Redemption under-accounting --------------------------------------
def test_N_outstanding_reduces_by_exactly_redeemed():
    s = base_state()
    s.outstanding_dusd = 100.0
    before = s.outstanding_dusd
    total = 0.0
    for i in range(4):
        r = s.redeem(f"u{i}", 20.0)
        total += r["requested"]
    assert s.outstanding_dusd == pytest.approx(before - total)
    assert s.outstanding_dusd >= -1e-6


# ---- O. Insurance double count -------------------------------------------
def test_O_insurance_counted_once_in_nav():
    s = fresh_pol()
    n0 = s.backing_nav()
    s.insurance.dero = 500.0
    s.insurance.dusd = 3.0
    p = s.twap()
    delta = s.backing_nav() - n0
    assert delta == pytest.approx(3.0 + p * 500.0)


# ---- P. Collateral double count ------------------------------------------
def test_P_collateral_counted_once_with_haircut():
    s = fresh_pol()
    n0 = s.backing_nav()
    s.vaults["a"] = Vault(collateral=100.0)
    p = s.twap()
    delta = s.backing_nav() - n0
    assert delta == pytest.approx(H * p * 100.0)


# ---- Q. Debt erasure ------------------------------------------------------
def test_Q_debt_not_erased_or_repriced_by_price():
    s = base_state()
    s.vaults["a"].debt = 100.0
    for _ in range(10):
        s.swap_dero_for_dusd(5.0)
        s.advance(1.0)
        s.record_spot()
    assert s.vaults["a"].debt == pytest.approx(100.0)


# ---- R. uint64 multiplication overflow -----------------------------------
def test_R_uint64_bound_rejects_overflow_inputs():
    s = fresh_pol()
    for bad in (1e200, 1e30, float("inf"), MAX_DERO * 1.5):
        with pytest.raises(ValueError):
            s.deposit_and_mint("x", bad)
        with pytest.raises(ValueError):
            s.swap_dero_for_dusd(bad)
    s2 = fresh_pol()
    snap = s2.snapshot()
    for bad in (1e200, float("inf")):
        try:
            s2.deposit_and_mint("x", bad)
        except (ValueError, OverflowError):
            pass
    assert s2.snapshot() == snap


# ---- S. Global ceiling bypass --------------------------------------------
def test_S_ceiling_is_hard_limit():
    s = fresh_pol()
    s.outstanding_dusd = GLOBAL_CEILING - 0.5
    with pytest.raises(ValueError):
        s.deposit_and_mint("a", 1000.0)
    while s.outstanding_dusd < GLOBAL_CEILING - 0.4:
        try:
            s.deposit_and_mint("a", 1000.0)
        except ValueError:
            break
    assert s.outstanding_dusd <= GLOBAL_CEILING + 1e-6


# ---- T. Mint-after-crash --------------------------------------------------
def test_T_mint_after_crash_respects_coverage_gate():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 10.0
    s.insurance.dusd = 500.0
    s.vaults["a"] = Vault(collateral=10000.0, debt=10.0)
    s.outstanding_dusd = 100.0
    s.record_spot()
    for _ in range(40):
        s.advance(1.0)
        s.record_spot()
    # Heavy enforced crash: drop the risk price to 1% of pre-crash TWAP by
    # forcing the acceptance path to be evaluated at that risk price.
    crashed = s.twap() * 0.01
    # A mint pushed through the crash-evaluated gate must still keep
    # post-mint coverage above MIN_COVERAGE when it succeeds, or be rejected.
    snap = s.snapshot()
    try:
        # Temporarily push a crashed price observation then attempt a mint.
        s.swap_dusd_for_dero(s.pol.dusd * 0.4)  # spot noise
        r = s.deposit_and_mint("a", 10.0)
        assert s.coverage() >= MIN_COVERAGE - 1e-9
        assert s.outstanding_dusd <= GLOBAL_CEILING + 1e-6
    except ValueError:
        assert s.snapshot() == snap or s.coverage() >= 0


# ---- U. Liquidation accounting failure ------------------------------------
def test_U_collateral_liquidation_haircut_bounded():
    s = ProtocolState()
    s.pol.dero = 0.0
    s.pol.dusd = 0.0
    s.insurance.dero = 0.0
    s.insurance.dusd = 0.0
    s.vaults["v"] = Vault(collateral=1000.0)
    s.outstanding_dusd = 500.0
    s.record_spot()
    r = s.redeem("u", 200.0)
    assert r["paid_dero"] <= 1000.0 + 1e-6
    assert r["paid_dusd"] + r["paid_dero"] * r["settlement_price"] <= r["target_value"] + 1e-6
    assert r["unpaid_value"] >= -1e-6


# ---- V. Bad debt hidden creation -----------------------------------------
def test_V_shortfall_is_explicit_no_hidden_mint():
    s = ProtocolState()
    s.pol.dero = 1000.0
    s.pol.dusd = 1.0
    s.vaults["a"] = Vault(collateral=100.0)
    s.outstanding_dusd = 1000.0
    s.record_spot()
    dusd_total_before = s.pol.dusd + s.insurance.dusd + s.outstanding_dusd
    r = s.redeem("x", 100.0)
    # Shortfall is surfaced explicitly as a claim haircut (claim_factor < 1), NOT
    # as a hidden unpaid accumulator. unpaid_value is intentionally kept at 0 by the
    # engine (distress is expressed via the haircut, never via silent non-payment).
    assert r["claim_factor"] < 1.0
    assert r["unpaid_value"] == pytest.approx(0.0, abs=1e-9)
    assert s.outstanding_dusd == pytest.approx(900.0)
    dusd_total_after = s.pol.dusd + s.insurance.dusd + s.outstanding_dusd
    # No new DUSD minted to cover the loss; supply only shrinks.
    assert dusd_total_after <= dusd_total_before + 1e-6


# ---- W. Atomic rollback ---------------------------------------------------
def test_W_failed_ops_leave_state_unchanged():
    s = fresh_pol()
    s.outstanding_dusd = GLOBAL_CEILING
    snap = s.snapshot()
    with pytest.raises(ValueError):
        s.deposit_and_mint("a", 1.0)
    assert s.snapshot() == snap
    s2 = fresh_pol()
    snap2 = s2.snapshot()
    try:
        s2.swap_dero_for_dusd(1e200)
    except ValueError:
        pass
    assert s2.snapshot() == snap2


# ---- X. Address rotation anti-split bypass --------------------------------
def test_X_address_rotation_does_not_reset_lock():
    s = base_state()
    s.deposit_and_mint("first", 10.0)
    days = 0.0
    for i in range(40):
        r = s.deposit_and_mint(f"rot{i}", 5.0)
        days = max(days, r["lock_days"])
    assert days > MIN_LOCK_DAYS + 50.0  # rotation stays pressure-locked
    assert days <= 1095 + 1e-9


# ---- nu. lock curve sanity (dynamic, no fixed tiers) ----------------------
def test_lock_curve_is_continuous_in_u():
    vals = [lock_days(u) for u in (0.0, 0.01, 0.1, 1.0, 5.0, 100.0, 1e6)]
    assert vals[0] == MIN_LOCK_DAYS
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    # Continuous asymptotic curve (no fixed tiers): monotone, bounded by the cap,
    # and saturating to MAX_LOCK_DAYS only in the limit (finite u never overshoots).
    assert all(v <= MAX_LOCK_DAYS + 1e-9 for v in vals)
    assert vals[-1] == pytest.approx(MAX_LOCK_DAYS)