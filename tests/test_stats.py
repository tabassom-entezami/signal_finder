import datetime
import itertools
import math

import numpy as np
import purgedcv
import pytest

from signal_engine.validation import stats


def test_psr_bailey_lopez_de_prado_2012_example() -> None:
    """Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier", J. of Risk 15(2).

    Monthly SR = 0.458 (annual ~1.59), skewness -2.448, kurtosis 10.164 (non-excess):
    PSR(0) = 0.913 over 24 months and 0.953 over 36 months (the values products.md R1 pins).
    """
    assert stats.psr(0.458, 24, -2.448, 10.164) == pytest.approx(0.913, abs=1e-3)  # 0.91336
    assert stats.psr(0.458, 36, -2.448, 10.164) == pytest.approx(0.953, abs=1e-3)  # 0.95351


def test_psr_with_excess_kurtosis_is_the_known_library_bug() -> None:
    """quantstats before v0.0.82 used excess kurtosis and printed 0.919 for the 24-month example."""
    buggy = stats.psr(0.458, 24, -2.448, 10.164 - 3.0)
    assert buggy == pytest.approx(0.919, abs=1.5e-3)
    assert buggy - stats.psr(0.458, 24, -2.448, 10.164) > 5e-3


def test_dsr_bailey_lopez_de_prado_2014_example() -> None:
    """Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", J. of Portfolio Management 40(5).

    N = 100 trials, V[SR] = 1/2 annualised, best SR = 2.5 annualised, T = 1250 daily returns,
    skewness -3, kurtosis 10: SR_0 = 0.1132 per day and DSR = 0.9004.
    """
    sr0 = stats.dsr_benchmark(100, 0.5 / 250)
    assert sr0 == pytest.approx(0.1132, abs=5e-5)
    assert stats.dsr(2.5 / math.sqrt(250), 1250, -3.0, 10.0, 100, 0.5 / 250) == pytest.approx(
        0.9004, abs=2e-4
    )


def test_dsr_with_one_trial_equals_psr() -> None:
    assert stats.dsr(0.1, 500, -0.5, 6.0, 1, 0.01) == stats.psr(0.1, 500, -0.5, 6.0)
    assert stats.expected_max_z(1) == 0.0
    with pytest.raises(ValueError):
        stats.expected_max_z(0)


def test_psr_and_dsr_agree_with_purgedcv_to_1e9() -> None:
    rng = np.random.default_rng(11)
    for _ in range(5):
        r = rng.standard_t(4, 700) * 0.01 + 0.0008
        assert stats.psr_returns(r) == pytest.approx(purgedcv.probabilistic_sharpe_ratio(r, 0.0), abs=1e-9)
        sr, sk, ku = stats.sharpe_moments(r)
        v = stats.sr_variance_floor(sr, len(r), sk, ku)
        own = stats.dsr(sr, len(r), sk, ku, 9, v)
        assert own == pytest.approx(purgedcv.deflated_sharpe_ratio(r, n_trials=9, var_sharpe=v), abs=1e-9)


def test_moments_reject_short_or_flat_series() -> None:
    with pytest.raises(ValueError):
        stats.sharpe_moments(np.ones(3))
    with pytest.raises(ValueError):
        stats.sharpe_moments(np.ones(10))
    with pytest.raises(ValueError):
        stats.psr(2.0, 10, 5.0, 3.0)


def test_pbo_on_noise_is_one_half() -> None:
    """Bailey et al. (2017): under the null (100 noise strategies) PBO ~ 0.5 (accepted: 0.5 +- 0.1)."""
    noise = np.random.default_rng(0).normal(0, 0.01, size=(1000, 100))
    assert stats.cscv_pbo(noise, 16).pbo == pytest.approx(0.5, abs=0.1)


def test_pbo_of_a_dominant_strategy_is_zero() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.01, size=(320, 5))
    x[:, 2] += 0.02
    assert stats.cscv_pbo(x, 8).pbo == 0.0


def _brute_force_pbo(x: np.ndarray, s: int) -> tuple[float, float]:
    """Independent CSCV: plain loops, statistics from numpy on concatenated rows."""
    m = len(x) // s
    blocks = [x[len(x) - m * s :][k * m : (k + 1) * m] for k in range(s)]
    le, lt, combos = 0, 0, list(itertools.combinations(range(s), s // 2))
    for c in combos:
        is_ = np.vstack([blocks[k] for k in c])
        oos = np.vstack([blocks[k] for k in range(s) if k not in c])
        best = int(np.argmax([stats.sharpe(is_[:, j]) for j in range(x.shape[1])]))
        perf = [stats.sharpe(oos[:, j]) for j in range(x.shape[1])]
        rank = sum(p < perf[best] for p in perf) + (sum(p == perf[best] for p in perf) + 1) / 2
        w = rank / (x.shape[1] + 1)
        lam = math.log(w / (1 - w))
        le += lam <= 0
        lt += lam < 0
    return le / len(combos), lt / len(combos)


def test_pbo_matches_brute_force_and_counts_median_ties() -> None:
    """S = 4, three strategies: a logit of exactly 0 (IS-best at the OOS median) counts in PBO (<= 0)."""
    x = np.random.default_rng(4).normal(0, 0.01, size=(42, 3))  # 42 % 4 = 2 rows dropped from the start
    res = stats.cscv_pbo(x, 4)
    assert (res.pbo, res.pbo_strict) == pytest.approx(_brute_force_pbo(x, 4))
    assert res.n_combos == 6
    ties = float(np.mean(res.logits == 0.0))
    assert res.pbo - res.pbo_strict == pytest.approx(ties)


def test_pbo_strict_equals_purgedcv_after_trimming_the_remainder() -> None:
    x = np.random.default_rng(5).normal(0, 0.01, size=(165, 6))
    ours = stats.cscv_pbo(x, 8)
    theirs = purgedcv.probability_of_backtest_overfitting(x[165 % 8 :].T, n_splits=8).pbo
    assert ours.pbo_strict == pytest.approx(theirs, abs=1e-12)


def test_pbo_drops_the_remainder_from_the_start() -> None:
    x = np.random.default_rng(6).normal(0, 0.01, size=(70, 4))
    poisoned = x.copy()
    poisoned[:6] = 99.0  # 70 % 8 = 6 leading rows are not used
    assert stats.cscv_pbo(poisoned, 8).pbo == stats.cscv_pbo(x, 8).pbo


def test_cpcv_counts_lopez_de_prado_n6_k2() -> None:
    """AFML ch. 12: N = 6 groups, k = 2 test groups -> C(6,2) = 15 splits and phi = 5 paths."""
    splits = stats.cpcv_splits(60, 6, 2)
    assert len(splits) == 15
    assert stats.cpcv_n_paths(6, 2) == 5
    counts = np.zeros(60, int)
    for train, test, _ in splits:
        assert not set(train) & set(test)
        counts[test] += 1
    assert (counts == 5).all()


def test_cpcv_purge_and_embargo() -> None:
    train, test, combo = stats.cpcv_splits(60, 6, 2, purge=2, embargo=3)[0]
    assert combo == (0, 1) and test.tolist() == list(range(20))
    assert train.min() == 23  # 3 embargo rows after the last test group
    tr = next(s for s in stats.cpcv_splits(60, 6, 2, purge=2, embargo=3) if s[2] == (2, 4))[0]
    assert 18 not in tr and 19 not in tr and 30 not in tr and 32 not in tr  # purge before, embargo after
    assert 17 in tr


def test_cpcv_paths_score_the_train_best() -> None:
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.01, size=(300, 3))
    x[:, 1] += 0.01
    sharpes = stats.cpcv_path_sharpes(x, 6, 2)
    assert len(sharpes) == 5
    assert sharpes == pytest.approx([stats.sharpe(x[:, 1])] * 5)


# ---------------------------------------------------------------- effective number of trials (W2.6c)
def _rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


def test_n_eff_of_near_duplicates_is_one() -> None:
    base = _rng().normal(size=(1000, 1))
    x = base + 1e-6 * _rng(1).normal(size=(1000, 100))
    r = stats.n_eff(x, 252)
    assert r.n_eff == 1 and r.singletons == 0


def test_n_eff_of_two_blocks_is_two() -> None:
    a, b = _rng(2).normal(size=(1000, 1)), _rng(3).normal(size=(1000, 1))
    x = np.hstack([a + 1e-6 * _rng(4).normal(size=(1000, 50)), b + 1e-6 * _rng(5).normal(size=(1000, 50))])
    assert stats.n_eff(x, 252).n_eff == 2


def test_n_eff_of_independent_series_stays_high() -> None:
    r = stats.n_eff(_rng(6).normal(size=(1000, 100)), 252)
    assert r.n_eff >= 85 and 89 < r.participation < 93  # theory n / (1 + n / T) = 90.9


def test_a_cell_with_too_little_overlap_is_its_own_trial() -> None:
    x = np.repeat(_rng(7).normal(size=(600, 1)), 5, axis=1) + 1e-6 * _rng(8).normal(size=(600, 5))
    x[:500, 4] = np.nan  # only 100 rows: it would cut the shared rows below 252
    r = stats.n_eff(x, 252)
    assert r.singletons == 1 and r.n_eff == 2 and r.shared_rows == 600


def test_fewer_shared_rows_than_cells_counts_every_cell() -> None:
    r = stats.n_eff(_rng(9).normal(size=(60, 80)), 52)
    assert r.n_eff == 80


def test_a_flat_cell_counts_alone() -> None:
    x = np.repeat(_rng(10).normal(size=(300, 1)), 3, axis=1)
    x[:, 2] = 0.0
    r = stats.n_eff(x, 252)
    assert r.singletons == 1 and r.n_eff == 2


def test_min_btl_uses_the_dsr_bracket() -> None:
    assert stats.min_btl_years(1, 1.0) == 0.0
    assert stats.min_btl_years(10, 1.0) == pytest.approx(stats.expected_max_z(10) ** 2)
    assert stats.min_btl_years(45, 0.0) == math.inf


# Monte Carlo E[max z] over 400,000 draws (Quant Developer checker, 2026-10-19), divided by sqrt(1.8 y)
@pytest.mark.parametrize(("n_trials", "mc_value"), [(75, 1.791), (117, 1.911)])
def test_sr_required_matches_the_simulated_max(n_trials: int, mc_value: float) -> None:
    assert stats.sr_required(n_trials, 1.8) == pytest.approx(mc_value, abs=0.03)
    assert stats.sr_required(n_trials, 1.8) >= mc_value  # the closed form errs on the early-stop side


def test_sr_required_inverts_min_btl() -> None:
    sr = stats.sr_required(75, 1.8)
    assert stats.min_btl_years(75, sr) == pytest.approx(1.8)
    assert stats.sr_required(1, 1.8) == 0.0
    assert stats.sr_required(75, 0.0) == math.inf


def test_search_stop_fires_on_the_bar_or_the_date() -> None:
    before = datetime.date(2026, 10, 31)
    assert not stats.search_stop_fires(75, 1.8, before)  # 1.81 today
    assert stats.search_stop_fires(75, 1.4, before)  # below about 1.44 y the bar passes 2
    assert stats.search_stop_fires(75, 0.0, before)
    assert stats.search_stop_fires(75, 1.8, datetime.date(2027, 3, 31))
    assert not stats.search_stop_fires(75, 1.8, datetime.date(2027, 3, 30))


def test_search_stop_bar_edge_is_strictly_above_two() -> None:
    before = datetime.date(2026, 10, 31)
    edge = (stats.expected_max_z(75) / stats.SEARCH_STOP_SR) ** 2  # history at which SR_req is exactly 2
    assert stats.sr_required(75, edge) == pytest.approx(2.0)
    assert not stats.search_stop_fires(75, edge, before)
    assert stats.search_stop_fires(75, edge - 1e-6, before)
    assert stats.SEARCH_STOP_SR == 2.0


def test_sr_required_rises_with_the_trial_count() -> None:
    values = [stats.sr_required(n, 1.8) for n in (2, 19, 75, 76, 117)]
    assert values == sorted(values) and len(set(values)) == len(values)


def test_haircut_shrinks_with_more_tests_and_holm_is_between() -> None:
    sr, yrs = np.array([1.5, 0.8, 0.3]), np.array([5.0, 5.0, 5.0])
    one = stats.haircut_sharpes(sr, yrs, 1)
    assert one["bonferroni"] == pytest.approx(sr, rel=1e-6)
    many = stats.haircut_sharpes(sr, yrs, 76)
    assert (many["bonferroni"] <= sr + 1e-12).all()
    assert (many["holm"] >= many["bonferroni"] - 1e-12).all() and many["holm"][0] < sr[0]
