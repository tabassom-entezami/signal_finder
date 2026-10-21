"""synthetic_market: the null/power generator (martingale null, planted AR(1) / mu control, shocks)."""

import hashlib

import numpy as np
import pandas as pd
import pytest

from signal_engine.backtest.synthetic import GAP_MAX, MARKET_VOL, MU_LOOKBACK, WICK_DEPTH, synthetic_market


def market_sha(b: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for k in sorted(b):
        h.update(k.encode())
        h.update(pd.util.hash_pandas_object(b[k], index=True).to_numpy().tobytes())
    return h.hexdigest()


def drift_t(b: dict[str, pd.DataFrame]) -> float:
    """t-stat of the cross-coin mean daily simple return (correlation-safe pooled martingale test)."""
    m = b["close"].pct_change().iloc[1:].mean(axis=1).to_numpy()
    return float(m.mean() / (m.std(ddof=1) / np.sqrt(m.size)))


def test_shape_and_dates_before_oos() -> None:
    b = synthetic_market(1)
    assert list(b) == ["open", "high", "low", "close", "volume"]
    assert b["close"].shape == (1105, 12)
    assert b["close"].index[-1] < pd.Timestamp("2019-08-17")
    assert b["high"].ge(b["open"]).all().all() and b["high"].ge(b["close"]).all().all()
    assert b["low"].le(b["open"]).all().all()
    assert b["low"].le(b["close"]).all().all() and (b["volume"] > 0).all().all()


def test_index_past_oos_start_is_refused() -> None:
    with pytest.raises(ValueError, match="out-of-sample"):
        synthetic_market(1, n_coins=1, n_days=7000)


def test_same_seed_same_sha_other_seed_differs() -> None:
    np.random.seed(0)
    a = market_sha(synthetic_market(7, n_coins=3, n_days=300))
    np.random.seed(99)  # global RNG state has no effect
    assert market_sha(synthetic_market(7, n_coins=3, n_days=300)) == a
    assert market_sha(synthetic_market(8, n_coins=3, n_days=300)) != a


def test_default_drift_is_null_and_old_drift_goes_red() -> None:
    kw = {"n_coins": 30, "n_days": 6000, "rho": 0.0}
    assert abs(drift_t(synthetic_market(3, **kw))) < 3.0  # type: ignore[arg-type]
    assert drift_t(synthetic_market(3, drift=0.0006, **kw)) > 3.0  # type: ignore[arg-type]


def test_planted_phi_is_recovered_only_on_the_planted_coin() -> None:
    ac = np.zeros(2)
    for seed in range(5):  # one 6000-bar estimate has sd ~0.017 under GARCH-t: average five
        b = synthetic_market(seed, n_coins=2, n_days=6000, phi=0.1, planted=0)
        r = np.log(b["close"]).diff().iloc[1:]
        ac += [float(np.corrcoef(r[c].to_numpy()[1:], r[c].to_numpy()[:-1])[0, 1]) / 5 for c in r.columns]
    assert abs(ac[0] - 0.1) < 0.03 and abs(ac[1]) < 0.03


def test_gaps_and_wicks_within_measured_bounds() -> None:
    b = synthetic_market(11, n_coins=12, n_days=3000)
    gap = (b["open"] / b["close"].shift(1) - 1.0).iloc[1:].to_numpy()
    assert np.abs(gap).max() <= GAP_MAX + 1e-12
    assert 0.002 < (np.abs(gap) > 1e-12).mean() < 0.006  # gap_p 0.4 %
    body = np.minimum(b["open"], b["close"]).to_numpy()
    depth = 1.0 - b["low"].to_numpy() / body
    assert 0.02 < (depth >= WICK_DEPTH[0]).mean() < 0.04  # jump_p 3 %
    assert depth.max() <= WICK_DEPTH[1] + 0.3 * 0.05 * 6  # wick plus at most a 6-sd normal spread
    r = np.log(b["close"]).diff().iloc[1:].to_numpy()
    excess = float(((r - r.mean()) ** 4).mean() / r.var() ** 2 - 3.0)
    assert 1.0 < excess < 15.0  # fat tails, same order as the measured 1.9-2.9


def test_null_is_a_martingale_not_zero_log_drift() -> None:
    kw = {"n_coins": 30, "n_days": 6000, "rho": 0.0}
    b = synthetic_market(4, **kw)  # type: ignore[arg-type]
    assert abs(drift_t(b)) < 2.0  # zero-mean simple returns
    log_mean = float(np.log(b["close"]).diff().iloc[1:].to_numpy().mean())
    assert log_mean < -0.5 * MARKET_VOL**2 / 2  # the log drift is ~ -vol^2 / 2, not 0
    r = b["close"].pct_change().iloc[1:].to_numpy()
    assert r.min() >= -0.95 - 1e-12


def test_mu_control_drifts_only_with_the_lagged_signal() -> None:
    b = synthetic_market(5, n_coins=2, n_days=6000, mu=0.01, planted=0)
    c = b["close"]
    r = c.pct_change()
    s = (c.shift(1) > c.shift(1 + MU_LOOKBACK)).iloc[MU_LOOKBACK + 2:]
    r = r.iloc[MU_LOOKBACK + 2:]
    planted = r.iloc[:, 0][s.iloc[:, 0]].mean() - r.iloc[:, 0][~s.iloc[:, 0]].mean()
    other = r.iloc[:, 1][s.iloc[:, 1]].mean() - r.iloc[:, 1][~s.iloc[:, 1]].mean()
    assert planted == pytest.approx(0.02, abs=0.004)  # +mu when the signal is on, -mu when off
    assert abs(other) < 0.004
