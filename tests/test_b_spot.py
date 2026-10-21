import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from signal_engine.strategy import b_spot


def test_sigma_uses_31_points_30_returns_ddof1() -> None:
    u = np.exp(np.cumsum(np.r_[0.0, np.tile([0.01, -0.02, 0.03], 12)]))  # 37 points
    sig = b_spot.sigma_daily(u)
    assert np.isnan(sig[:30]).all()
    r = np.diff(np.log(u[0:31]))
    assert len(r) == 30
    assert sig[30] == pytest.approx(np.std(r, ddof=1), rel=1e-12)


def test_tsmom_vote_needs_253_points_and_sign_zero_is_zero() -> None:
    u = np.ones(253)
    u[-1] = 2.0
    u[252 - 21] = 2.0  # u_t == u_{t-21}: that vote is 0
    v = b_spot.tsmom_vote(u)
    assert np.isnan(v[:252]).all()
    assert v[252] == pytest.approx(2 / 3)


def test_tau_is_long_flat_and_vol_capped() -> None:
    sigma = np.array([0.20 / np.sqrt(365) / 2, 0.20 / np.sqrt(365) * 2, 0.01])
    tau = b_spot._size(np.array([1.0, 1.0, -1 / 3]), sigma, 0.20)
    assert tau.tolist() == pytest.approx([1.0, 0.5, 0.0])


def test_donchian_enters_on_20_bar_high_exits_on_10_bar_low() -> None:
    u = np.r_[np.full(20, 10.0), 11.0, np.full(5, 11.0), 9.0, np.full(3, 9.5)]
    s = b_spot.donchian_state(u)
    assert np.isnan(s[:20]).all()
    assert s[20] == 1.0  # new 20-bar high
    assert s[25] == 1.0  # unchanged
    assert s[26] == 0.0  # below the 10-bar low
    assert s[29] == 0.0


def test_band_rule() -> None:
    assert b_spot.next_weight(0.55, 0.5) == 0.5  # inside the band: hold
    assert b_spot.next_weight(0.65, 0.5) == 0.65  # moved by >= 0.10: trade
    assert b_spot.next_weight(0.0, 0.05) == 0.0  # flat signal always exits


def test_unknown_variant_raises() -> None:
    with pytest.raises(ValueError):
        b_spot.signals(np.ones(10), "nope")


@settings(max_examples=40, deadline=None, database=None)
@given(
    seed=st.integers(0, 2**32 - 1),
    cut=st.integers(260, 399),
    variant=st.sampled_from([b_spot.TSMOM, b_spot.DONCHIAN]),
    holes=st.lists(st.integers(0, 399), max_size=5),
)
def test_signals_are_truncation_invariant(seed: int, cut: int, variant: str, holes: list[int]) -> None:
    """Signals at t are identical with or without later bars (look-ahead test)."""
    rng = np.random.default_rng(seed)
    u = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, 400)))
    u[holes] = np.nan
    tau_full, sig_full = b_spot.signals(u, variant)
    tau_cut, sig_cut = b_spot.signals(u[: cut + 1], variant)
    np.testing.assert_array_equal(tau_cut, tau_full[: cut + 1])
    np.testing.assert_array_equal(sig_cut, sig_full[: cut + 1])
