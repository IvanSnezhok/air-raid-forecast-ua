"""Feature and label construction.

Forecast setup
--------------
At every forecast origin T (any whole minute; training uses top-of-hour
origins), for every region r and horizon h in HORIZONS we predict two targets:
  * active: region r has an alert at any moment of the hour [T+(h-1)h, T+h h);
  * onset:  a new alert starts in region r within [T, T+h h).
"active" is easy at short horizons because alerts persist; "onset" is the
harder, more useful question ("will we get a new alert soon?").

"Hours" are 60-minute blocks of the grid, which `data.build_grid` aligns so
that they end at the grid's `data_end`: for an origin at 14:20 the lags are the
blocks 13:20-14:20, 12:20-13:20, ..., hour 1 is 14:20-15:20. All features and
labels below are written in these blocks, so they mean the same thing for an
origin at :00 and at :20 (the calendar features use the block's midpoint).

No-leakage contract
-------------------
Every feature at origin T is a function of the alert record *as it was known
at T*:
  * minutes strictly before T (hourly activity, counts of alert starts);
  * whether an alert is ongoing at T and how long it has lasted so far.
The end time of an alert that is still running at T is never used.
`tests/test_leakage.py` checks this mechanically: features computed on the
full data must equal features computed on data truncated at T.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import AlertGrid

HORIZONS = (1, 2, 3, 6)
KYIV = "Europe/Kyiv"
LAGS = range(1, 25)
MIN_HISTORY_HOURS = 28 * 24


def hourly_fraction(grid: AlertGrid) -> np.ndarray:
    """Share of each hour under alert, shape [n_regions, n_hours] (full hours only)."""
    n_hours = grid.n_minutes // 60
    a = grid.active[:, : n_hours * 60].reshape(len(grid.regions), n_hours, 60)
    return a.mean(axis=2, dtype=np.float32)


def hourly_starts(grid: AlertGrid) -> np.ndarray:
    """1 if at least one (merged) alert started in that hour, shape [n_regions, n_hours]."""
    n_hours = grid.n_minutes // 60
    S = np.zeros((len(grid.regions), n_hours), np.float32)
    for i, s in enumerate(grid.starts):
        k = s // 60
        S[i, k[k < n_hours]] = 1
    return S


def _rolling_mean(H: np.ndarray, K: np.ndarray, w: int) -> np.ndarray:
    """Mean of H over hours [k-w, k) for every origin hour k in K."""
    c = np.concatenate([np.zeros((H.shape[0], 1), np.float64), np.cumsum(H, axis=1)], axis=1)
    return ((c[:, K] - c[:, K - w]) / w).astype(np.float32)


def _count_in_window(arr: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Number of values of sorted `arr` in [lo, hi)."""
    return np.searchsorted(arr, hi, "left") - np.searchsorted(arr, lo, "left")


def origin_hours(grid: AlertGrid, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    """Hour indices of origins in [start, end), keeping enough history."""
    k0 = max(grid.minute(start) // 60, MIN_HISTORY_HOURS)
    k1 = min(grid.minute(end) // 60, grid.n_minutes // 60)
    return np.arange(k0, k1, dtype=np.int64)


def build_features(grid: AlertGrid, K: np.ndarray, H: np.ndarray | None = None) -> pd.DataFrame:
    """Features for every (origin hour in K, region). Horizon-independent part."""
    if H is None:
        H = hourly_fraction(grid)
    R = len(grid.regions)
    T = K * 60  # origin in minutes
    cols: dict[str, np.ndarray] = {}

    # Ongoing alert at T: the minute just before T is under alert. This is
    # exactly what an operator sees at T.
    now = grid.active[:, T - 1]  # [R, nK]

    elapsed = np.full((R, len(K)), np.nan, np.float32)
    since_end = np.full((R, len(K)), np.nan, np.float32)
    starts_6h = np.zeros((R, len(K)), np.float32)
    starts_24h = np.zeros((R, len(K)), np.float32)
    starts_7d = np.zeros((R, len(K)), np.float32)
    for i in range(R):
        s, e = grid.starts[i], grid.ends[i]
        cur = np.searchsorted(s, T, "left") - 1  # last interval that started before T
        act = now[i]
        ok = act & (cur >= 0)
        elapsed[i, ok] = T[ok] - s[cur[ok]]
        # last interval that is fully over: the current one if idle, else the previous one
        prev = np.where(act, cur - 1, cur)
        okp = prev >= 0
        since_end[i, okp] = T[okp] - e[prev[okp]]
        starts_6h[i] = _count_in_window(s, T - 6 * 60, T)
        starts_24h[i] = _count_in_window(s, T - 24 * 60, T)
        starts_7d[i] = _count_in_window(s, T - 7 * 24 * 60, T)

    cols["active_now"] = now.astype(np.float32)
    cols["elapsed_min"] = elapsed
    cols["since_end_min"] = np.minimum(since_end, 7 * 24 * 60)
    cols["starts_6h"] = starts_6h
    cols["starts_24h"] = starts_24h
    cols["starts_7d"] = starts_7d
    for j in LAGS:
        cols[f"lag_{j}h"] = H[:, K - j]
    cols["frac_24h"] = _rolling_mean(H, K, 24)
    cols["frac_7d"] = _rolling_mean(H, K, 7 * 24)
    cols["frac_28d"] = _rolling_mean(H, K, 28 * 24)

    # National picture
    n_now = now.sum(axis=0).astype(np.float32)
    cols["nat_active_now"] = np.broadcast_to(n_now, (R, len(K)))
    all_starts = np.sort(np.concatenate(grid.starts))
    for w in (1, 3):
        c = _count_in_window(all_starts, T - w * 60, T).astype(np.float32)
        cols[f"nat_starts_{w}h"] = np.broadcast_to(c, (R, len(K)))
    cols["nat_frac_24h"] = np.broadcast_to(_rolling_mean(H, K, 24).mean(axis=0), (R, len(K)))

    # Spatial picture: state of every other region (lets the model learn which
    # regions "lead" which, instead of us hand-coding a neighbour map).
    lag1 = H[:, K - 1]
    for j, reg in enumerate(grid.regions):
        key = reg.replace(" oblast", "").replace(" ", "_")
        cols[f"now__{key}"] = np.broadcast_to(now[j].astype(np.float32), (R, len(K)))
        cols[f"lag1__{key}"] = np.broadcast_to(lag1[j], (R, len(K)))

    df = pd.DataFrame({k: np.ascontiguousarray(v).reshape(-1) for k, v in cols.items()})
    df.insert(0, "region", np.repeat(np.array(grid.regions), len(K)))
    df.insert(0, "origin_hour", np.tile(K, R))
    return df


def add_horizon_features(df: pd.DataFrame, grid: AlertGrid, H: np.ndarray, h: int, S: np.ndarray | None = None) -> pd.DataFrame:
    """Horizon-specific features: calendar of the target hour and same-hour climatology."""
    if S is None:
        S = hourly_starts(grid)
    out = df.copy()
    K = out["origin_hour"].to_numpy()
    r = pd.Categorical(out["region"], categories=grid.regions).codes
    target_hour = K + h - 1
    # Kyiv hour / weekday of the middle of the target block: for top-of-hour
    # origins this is the block's own hour; for 14:50 it is 15 (block 14:50-15:50).
    ts = grid.t0 + pd.to_timedelta(target_hour, unit="h") + pd.Timedelta(minutes=30)
    local = ts.tz_convert(KYIV)
    out["target_hour_local"] = local.hour.astype(np.int16)
    out["target_dow"] = local.dayofweek.astype(np.int16)
    # Same hour of day over the previous 7 / 28 days; all strictly before T for h <= 24.
    for days in (7, 28):
        acc = np.zeros(len(out), np.float32)
        onset = np.zeros(len(out), np.float32)
        for d in range(1, days + 1):
            acc += H[r, target_hour - 24 * d] > 0
            # did a new alert start in the same h-hour window of the day, d days ago?
            onset += S[r[:, None], (K - 24 * d)[:, None] + np.arange(h)].max(axis=1)
        out[f"hod_clim_{days}d"] = acc / days
        out[f"onset_clim_{days}d"] = onset / days
    # did an alert start in the last h hours (persistence for the onset target)
    out["onset_last_h"] = S[r[:, None], K[:, None] - 1 - np.arange(h)].max(axis=1)
    out["horizon"] = h
    return out


def labels(grid: AlertGrid, K: np.ndarray, h: int, H: np.ndarray | None = None,
           target: str = "active", S: np.ndarray | None = None) -> np.ndarray:
    """Labels per (region, origin), region-major. NaN when beyond the data.

    target="active": an alert is active at any moment of hour [T+(h-1)h, T+h h).
    target="onset":  a new alert starts within [T, T+h h).
    """
    if H is None:
        H = hourly_fraction(grid)
    n_hours = H.shape[1]
    y = np.full((len(grid.regions), len(K)), np.nan, np.float32)
    ok = K + h <= n_hours
    if target == "active":
        y[:, ok] = H[:, K[ok] + h - 1] > 0
    elif target == "onset":
        if S is None:
            S = hourly_starts(grid)
        y[:, ok] = S[:, K[ok][:, None] + np.arange(h)].max(axis=2)
    else:
        raise ValueError(target)
    return y.reshape(-1)
