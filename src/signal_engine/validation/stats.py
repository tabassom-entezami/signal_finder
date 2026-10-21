"""Gate statistics: PSR, DSR, CSCV-PBO and CPCV (own implementations, cross-checked against purgedcv).

- PSR: Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier", eq. for PSR with
  non-excess kurtosis.
- DSR: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", expected maximum Sharpe.
- PBO: Bailey, Borwein, Lopez de Prado & Zhu (2017), CSCV.
- CPCV: Lopez de Prado (2018), *Advances in Financial Machine Learning*, ch. 12.
Moment conventions match purgedcv 0.1.10: Sharpe with the population std (ddof 0),
bias-corrected sample skewness and (non-excess) kurtosis.
"""

import datetime as dt
import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]
_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def sharpe_moments(returns: Arr) -> tuple[float, float, float]:
    """(SR, skew, kurtosis) of a return series: SR = mean / std(ddof=0); kurtosis is not excess."""
    x = np.asarray(returns, dtype=float)
    n = x.size
    if n < 4 or not np.isfinite(x).all():
        raise ValueError("need >= 4 finite returns")
    d = x - x.mean()
    m2, m3, m4 = float(np.mean(d**2)), float(np.mean(d**3)), float(np.mean(d**4))
    if m2 == 0.0:
        raise ValueError("zero variance: Sharpe ratio undefined")
    sr = float(x.mean()) / math.sqrt(m2)
    skew = m3 / m2**1.5 * math.sqrt(n * (n - 1)) / (n - 2)
    excess = m4 / m2**2 - 3.0
    kurt = ((n + 1) * excess + 6.0) * (n - 1) / ((n - 2) * (n - 3)) + 3.0
    return sr, skew, kurt


def sharpe(x: Arr) -> float:
    """Plain Sharpe (mean / sample std, ddof 1); 0 for a degenerate slice. The CSCV and CPCV metric."""
    sd = float(np.std(x, ddof=1)) if x.size > 1 else 0.0
    return float(np.mean(x)) / sd if sd > 0.0 and np.isfinite(sd) else 0.0


def psr(sr: float, n: int, skew: float, kurt: float, sr_star: float = 0.0) -> float:
    """PSR = Phi((SR - SR*) sqrt(n - 1) / sqrt(1 - skew SR + (kurt - 1)/4 SR^2)), kurt non-excess."""
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0.0 or n < 2:
        raise ValueError("PSR undefined for these moments")
    return _N.cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom))


def psr_returns(returns: Arr, sr_star: float = 0.0) -> float:
    sr, skew, kurt = sharpe_moments(returns)
    return psr(sr, len(returns), skew, kurt, sr_star)


def expected_max_z(n_trials: int) -> float:
    """(1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e)); 0 for a single trial."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    return (1.0 - EULER_GAMMA) * _N.inv_cdf(1.0 - 1.0 / n_trials) + EULER_GAMMA * _N.inv_cdf(
        1.0 - 1.0 / (n_trials * math.e)
    )


def dsr_benchmark(n_trials: int, var_sr: float) -> float:
    """SR_0 = sqrt(V) * expected_max_z(N), in the same (per-observation) units as V."""
    if var_sr < 0.0:
        raise ValueError("variance must be >= 0")
    return math.sqrt(var_sr) * expected_max_z(n_trials)


def dsr(sr: float, n: int, skew: float, kurt: float, n_trials: int, var_sr: float) -> float:
    """DSR = PSR at SR* = SR_0."""
    return psr(sr, n, skew, kurt, dsr_benchmark(n_trials, var_sr))


def sr_variance_floor(sr: float, n: int, skew: float, kurt: float) -> float:
    """(1 - skew SR + (kurt - 1)/4 SR^2) / (n - 1): the sampling variance of SR, the lower bound on V."""
    return (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / (n - 1)


@dataclass(frozen=True)
class PBOResult:
    pbo: float  # share of splits with logit <= 0 (IS-best at or below the OOS median): the gate value
    pbo_strict: float  # share with logit < 0 (purgedcv's convention), reported next to it
    n_combos: int
    logits: Arr


def _block_sharpe(sum1: Arr, sum2: Arr, n: int) -> Arr:
    mean = sum1 / n
    var = (sum2 - sum1 * sum1 / n) / (n - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out: Arr = np.where(var > 0.0, mean / np.sqrt(np.where(var > 0.0, var, 1.0)), 0.0)
    return out


def cscv_pbo(returns: Arr, n_blocks: int = 16) -> PBOResult:
    """CSCV PBO on a (T, N) matrix of per-period returns: S equal blocks, remainder dropped from the start."""
    x = np.asarray(returns, dtype=float)
    t_obs, n_cfg = x.shape
    if n_blocks % 2 or n_cfg < 2 or t_obs < 2 * n_blocks:
        raise ValueError("need an even block count, >= 2 configurations and >= 2 rows per block")
    m = t_obs // n_blocks
    blocks = x[t_obs - m * n_blocks :].reshape(n_blocks, m, n_cfg)
    s1, s2 = blocks.sum(axis=1), (blocks**2).sum(axis=1)  # (S, N)
    combos = np.array(list(itertools.combinations(range(n_blocks), n_blocks // 2)))
    mask = np.zeros((len(combos), n_blocks))
    mask[np.arange(len(combos))[:, None], combos] = 1.0
    half = m * n_blocks // 2
    is_sr = _block_sharpe(mask @ s1, mask @ s2, half)
    oos_sr = _block_sharpe((1.0 - mask) @ s1, (1.0 - mask) @ s2, half)
    best = np.argmax(is_sr, axis=1)
    pick = oos_sr[np.arange(len(combos)), best][:, None]
    rank = (oos_sr < pick).sum(axis=1) + ((oos_sr == pick).sum(axis=1) + 1) / 2.0  # average rank, 1..N
    omega = rank / (n_cfg + 1)
    logits: Arr = np.log(omega / (1.0 - omega))
    return PBOResult(float(np.mean(logits <= 0.0)), float(np.mean(logits < 0.0)), len(combos), logits)


def cpcv_splits(
    n_obs: int, n_groups: int, k_test: int, purge: int = 0, embargo: int = 0
) -> list[tuple[NDArray[np.int64], NDArray[np.int64], tuple[int, ...]]]:
    """All C(N, k) splits; `purge` rows before and `embargo` rows after each test group leave train."""
    groups = np.array_split(np.arange(n_obs), n_groups)
    out = []
    for combo in itertools.combinations(range(n_groups), k_test):
        test = np.concatenate([groups[g] for g in combo])
        keep = np.ones(n_obs, dtype=bool)
        for g in combo:
            keep[max(0, int(groups[g][0]) - purge) : int(groups[g][-1]) + 1 + embargo] = False
        out.append((np.flatnonzero(keep).astype(np.int64), test.astype(np.int64), combo))
    return out


def cpcv_n_paths(n_groups: int, k_test: int) -> int:
    """phi[N, k] = k / N * C(N, k)."""
    return math.comb(n_groups, k_test) * k_test // n_groups


def cpcv_path_sharpes(
    returns: Arr, n_groups: int = 6, k_test: int = 2, purge: int = 0, embargo: int = 0
) -> Arr:
    """Sharpe of each CPCV backtest path: per split, the train-best column is scored on its test groups."""
    x = np.asarray(returns, dtype=float)
    groups = np.array_split(np.arange(x.shape[0]), n_groups)
    seen = [0] * n_groups
    paths: list[list[Arr]] = [[np.empty(0)] * n_groups for _ in range(cpcv_n_paths(n_groups, k_test))]
    for train, _, combo in cpcv_splits(x.shape[0], n_groups, k_test, purge, embargo):
        best = int(np.argmax([sharpe(x[train, j]) for j in range(x.shape[1])]))
        for g in combo:
            paths[seen[g]][g] = x[groups[g], best]
            seen[g] += 1
    return np.array([sharpe(np.concatenate(p)) for p in paths])


# ---------------------------------------------------------------- effective number of trials
@dataclass(frozen=True)
class NEffResult:
    n_eff: int  # the gate value: max(clusters, participation ratio) on the shared rows, plus the singletons
    n_cells: int
    shared_rows: int  # rows (dates or weeks) every clustered cell has
    singletons: int  # cells counted as their own trial (too little overlap, or too few shared rows)
    clusters: int
    participation: float  # (sum lambda)^2 / sum lambda^2 of the correlation matrix


def _ceil(x: float) -> int:
    return math.ceil(x - 1e-6)  # 1 + 1e-12 is one trial, not two


def _average_linkage(dist: Arr) -> list[tuple[int, int]]:
    """Merge order of average-linkage clustering (Lance-Williams update), as pairs of surviving cluster
    ids."""
    d = dist.astype(float).copy()
    np.fill_diagonal(d, np.inf)
    size = np.ones(d.shape[0])
    alive = np.ones(d.shape[0], dtype=bool)
    merges = []
    for _ in range(d.shape[0] - 1):
        masked = np.where(alive[:, None] & alive[None, :], d, np.inf)
        i, j = divmod(int(np.argmin(masked)), d.shape[0])
        i, j = min(i, j), max(i, j)
        merges.append((i, j))
        d[i] = d[:, i] = (size[i] * d[i] + size[j] * d[j]) / (size[i] + size[j])
        d[i, i] = np.inf
        size[i] += size[j]
        alive[j] = False
    return merges


def _labels(n: int, merges: list[tuple[int, int]], k: int) -> NDArray[np.int64]:
    parent = list(range(n))

    def root(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, j in merges[: n - k]:
        parent[root(j)] = root(i)
    roots = [root(a) for a in range(n)]
    ids = {r: c for c, r in enumerate(dict.fromkeys(roots))}
    return np.array([ids[r] for r in roots], dtype=np.int64)


def _silhouette(dist: Arr, labels: NDArray[np.int64]) -> float:
    k = int(labels.max()) + 1
    onehot = np.eye(k)[labels]
    counts = onehot.sum(axis=0)
    sums = dist @ onehot  # each row: total distance to every cluster
    own = counts[labels]
    a = np.where(own > 1, sums[np.arange(labels.size), labels] / np.maximum(own - 1, 1), 0.0)
    other = np.where(onehot.astype(bool), np.inf, sums / np.maximum(counts, 1))
    b = other.min(axis=1)
    s = np.where(own > 1, (b - a) / np.maximum(np.maximum(a, b), 1e-300), 0.0)
    return float(s.mean())


def cluster_count(corr: Arr, max_k: int = 150) -> int:
    """Clusters of distance sqrt((1 - rho) / 2), average linkage, cut at the best mean silhouette
    (k = 2..max_k).

    The silhouette has no value at k = 1, so near-identical series (largest distance < 1e-3) are one cluster.
    """
    n = corr.shape[0]
    dist = np.sqrt(np.clip((1.0 - corr) / 2.0, 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    if n < 2 or float(dist.max()) < 1e-3:
        return 1
    if n == 2:
        return 2
    merges = _average_linkage(dist)
    scores = {k: _silhouette(dist, _labels(n, merges, k)) for k in range(2, min(n - 1, max_k) + 1)}
    return max(scores, key=lambda k: scores[k])


def n_eff(returns: Arr, min_shared: int) -> NEffResult:
    """Effective number of independent trials among the columns of `returns` (rows = dates or weeks,
    NaN = no value).

    Cells are dropped as their own trial, shortest history first, until the rows every remaining cell has
    number at least `min_shared`. On those rows: Pearson rho, then N_eff = max(cluster count, participation
    ratio), both rounded up; a participation ratio below 1.5 means one cluster. With fewer shared rows than
    cells the correlations cannot show fewer trials, so every cell counts.
    """
    x = np.asarray(returns, dtype=float)
    n = x.shape[1]
    keep = list(np.argsort(-np.isfinite(x).sum(axis=0), kind="stable"))
    shared = np.isfinite(x[:, keep]).all(axis=1)
    while len(keep) > 1 and shared.sum() < min_shared:
        keep.pop()
        shared = np.isfinite(x[:, keep]).all(axis=1)
    rows, m = int(shared.sum()), len(keep)
    singles = n - m
    if m < 2 or rows < max(min_shared, m):
        return NEffResult(n, n, rows, n, 0, float("nan"))
    sub = x[np.ix_(shared, keep)]
    sd = sub.std(axis=0)
    flat = sd == 0
    if flat.any():  # a column without variance carries no correlation: its own trial
        sub, singles, m = sub[:, ~flat], singles + int(flat.sum()), int((~flat).sum())
        if m < 2:
            return NEffResult(n, n, rows, n, 0, float("nan"))
    corr = np.corrcoef(sub, rowvar=False)
    pr = float(m * m / np.square(corr).sum())
    k = 1 if pr < 1.5 else cluster_count(corr)
    return NEffResult(max(k, _ceil(pr)) + singles, n, rows, singles, k, pr)


def min_btl_years(n_trials: int, sr_annual: float) -> float:
    """Minimum backtest length (Bailey, Borwein, Lopez de Prado & Zhu 2014): years after which the expected
    maximum annual Sharpe of `n_trials` skill-less trials falls to `sr_annual` (same bracket as the DSR)."""
    if sr_annual <= 0.0:
        return math.inf
    return (expected_max_z(n_trials) / sr_annual) ** 2


SEARCH_STOP_SR = 2.0  # the directional search stops once a new rule would need a higher annual Sharpe
SEARCH_STOP_DATE = dt.date(2027, 3, 31)  # outer stop; a later search needs a new owner decision


def sr_required(n_trials: int, years: float) -> float:
    """Annual Sharpe a new rule needs so that MinBTL fits in `years` of history: E[max z_N] / sqrt(years).
    The closed form sits about 1 % above a simulated E[max z], so the stop fires slightly early."""
    if years <= 0.0:
        return math.inf
    return expected_max_z(n_trials) / math.sqrt(years)


def search_stop_fires(n_trials: int, years: float, today: dt.date) -> bool:
    """True from SEARCH_STOP_DATE on, or once sr_required exceeds SEARCH_STOP_SR, whichever comes first."""
    return today >= SEARCH_STOP_DATE or sr_required(n_trials, years) > SEARCH_STOP_SR


def haircut_sharpes(sr_annual: Arr, years: Arr, m: int) -> dict[str, Arr]:
    """Harvey & Liu (2015) haircut Sharpes at m tests: t = SR sqrt(years), two-sided p, adjusted by
    Bonferroni, Holm and BHY, then back to a Sharpe. Inputs are one value per tested strategy."""
    sr = np.asarray(sr_annual, dtype=float)
    yrs = np.asarray(years, dtype=float)
    t = np.abs(sr) * np.sqrt(yrs)
    p = np.array([2.0 * (1.0 - _N.cdf(v)) for v in t])
    order = np.argsort(p)
    k = len(p)
    c = sum(1.0 / i for i in range(1, m + 1))
    adj = {"bonferroni": np.minimum(p * m, 1.0)}
    holm = np.minimum(p[order] * (m - np.arange(k)), 1.0)
    adj["holm"] = np.maximum.accumulate(holm)[np.argsort(order)]
    bhy = np.minimum(p[order] * m * c / np.arange(1, k + 1), 1.0)
    adj["bhy"] = np.minimum.accumulate(bhy[::-1])[::-1][np.argsort(order)]
    out = {}
    for name, q in adj.items():
        t_adj = np.array([_N.inv_cdf(1.0 - min(v, 1.0 - 1e-16) / 2.0) if v < 1.0 else 0.0 for v in q])
        out[name] = np.sign(sr) * t_adj / np.sqrt(yrs)
    return out
