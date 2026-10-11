"""Features for the raion-level forecast.

Same setup and no-leakage contract as `airraid.features` (read its docstring):
at origin T (top of a UTC hour) every feature uses minutes strictly before T,
plus whether an alert is running at T and for how long. On top of the raion's
own history (the oblast feature set without the per-region columns) a raion
gets:
  * oblast context from the raion grid: share of the *other* raions of its
    oblast under alert now / in the last hour, alert starts there in the last 3 h;
  * neighbours: share of bordering raions under alert now / in the last hour;
  * the volunteer (eTryvoga) oblast state that the oblast model sees
    (`vol_*`): alert running now, elapsed, starts, recent hourly shares.

`tests/test_raion_leakage.py` rebuilds everything from data truncated at T and
checks that nothing changes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import AlertGrid, build_grid
from .features import build_features, hourly_fraction, hourly_starts, labels
from .raion_neighbours import NEIGHBOURS
from .raions import BY_ID

VOL_COLS = ("active_now", "elapsed_min", "since_end_min", "starts_6h", "starts_24h", "starts_7d",
            "frac_24h", "frac_7d", "lag_1h", "lag_2h", "lag_3h")
SHARE_WINDOW_H = 28 * 24
# Features of the "current state" raion model: everything it needs about raions is
# which raions are under alert right now and since when, which a live alerts.in.ua
# poll gives without any raion history. The oblast part comes from the volunteer history.
STATE_COLS = ["active_now", "elapsed_min", "obl_others_now", "nb_now", "nat_active_now",
              "target_hour_local", "target_dow"] + [f"vol_{c}" for c in VOL_COLS]


@dataclass
class RaionInputs:
    """Minute grids of the raion history and of the volunteer oblast history, cut at the same moment."""

    grid: AlertGrid
    H: np.ndarray
    S: np.ndarray
    vgrid: AlertGrid
    vH: np.ndarray
    vS: np.ndarray

    @property
    def offset(self) -> int:
        """Volunteer-grid hour index = raion-grid hour index + offset."""
        return int((self.grid.t0 - self.vgrid.t0) / pd.Timedelta(hours=1))

    def oblast_rows(self) -> np.ndarray:
        """For every raion, the row of its oblast in the volunteer grid (-1 if absent)."""
        idx = {r: i for i, r in enumerate(self.vgrid.regions)}
        return np.array([idx.get(BY_ID[r].oblast, -1) for r in self.grid.regions])


def make_inputs(raion_alerts: pd.DataFrame, vol_alerts: pd.DataFrame, data_end: pd.Timestamp,
                regions: list[str] | None = None, t0: pd.Timestamp | None = None) -> RaionInputs:
    grid = build_grid(raion_alerts, data_end=data_end, regions=regions, t0=t0)
    vgrid = build_grid(vol_alerts, data_end=data_end, t0=t0)
    if vgrid.t0 > grid.t0:
        raise ValueError("volunteer history must start before the raion history")
    return RaionInputs(grid, hourly_fraction(grid), hourly_starts(grid), vgrid, hourly_fraction(vgrid), hourly_starts(vgrid))


def _group_mean_excluding_self(X: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Mean of X over the other members of each row's group ([R, n] -> [R, n]); 0 for single-member groups."""
    out = np.zeros_like(X, dtype=np.float32)
    for g in np.unique(groups):
        m = groups == g
        n = m.sum()
        if n > 1:
            tot = X[m].sum(axis=0)
            out[m] = (tot[None, :] - X[m]) / (n - 1)
    return out


def build_raion_features(inp: RaionInputs, K: np.ndarray) -> pd.DataFrame:
    """Horizon-independent features for every (raion, origin hour in K), region-major like build_features."""
    g, H, S = inp.grid, inp.H, inp.S
    df = build_features(g, K, H, spatial=False)
    T = K * 60
    now = g.active[:, T - 1].astype(np.float32)
    lag1 = H[:, K - 1]
    started3 = np.maximum.reduce([S[:, K - j] for j in (1, 2, 3)])
    oblast = np.array([BY_ID[r].oblast for r in g.regions])
    cols = {
        "obl_others_now": _group_mean_excluding_self(now, oblast),
        "obl_others_lag1h": _group_mean_excluding_self(lag1, oblast),
        "obl_others_started_3h": _group_mean_excluding_self(started3, oblast),
    }
    pos = {r: i for i, r in enumerate(g.regions)}
    nb_now = np.zeros_like(now)
    nb_lag1 = np.zeros_like(lag1)
    for i, r in enumerate(g.regions):
        nb = [pos[x] for x in NEIGHBOURS.get(r, []) if x in pos]
        if nb:
            nb_now[i] = now[nb].mean(axis=0)
            nb_lag1[i] = lag1[nb].mean(axis=0)
    cols["nb_now"] = nb_now
    cols["nb_lag1h"] = nb_lag1

    vK = K + inp.offset
    vdf = build_features(inp.vgrid, vK, inp.vH, spatial=False)
    rows = inp.oblast_rows()
    for c in VOL_COLS:
        V = vdf[c].to_numpy().reshape(len(inp.vgrid.regions), len(K))
        V = np.vstack([V, np.full((1, len(K)), np.nan, np.float32)])  # row -1: oblast missing
        cols[f"vol_{c}"] = V[rows]
    for k, v in cols.items():
        df[k] = np.ascontiguousarray(v, dtype=np.float32).reshape(-1)
    return df


def oblast_labels(inp: RaionInputs, K: np.ndarray, h: int, target: str) -> np.ndarray:
    """Volunteer oblast label of each raion's oblast, [R, len(K)] (NaN beyond the data or oblast missing)."""
    y = labels(inp.vgrid, K + inp.offset, h, inp.vH, target, inp.vS).reshape(len(inp.vgrid.regions), len(K))
    y = np.vstack([y, np.full((1, len(K)), np.nan, np.float32)])
    return y[inp.oblast_rows()]


def trailing_share(y_raion: np.ndarray, y_oblast: np.ndarray, h: int, window: int = SHARE_WINDOW_H) -> np.ndarray:
    """How often the raion had the event relative to its (volunteer) oblast, from labels known at each origin.

    Inputs are [R, n] labels for n *consecutive* origin hours. The value at
    origin i uses origins i-window .. i-h, whose label windows [j, j+h) end by
    i, so it is known at i. Ratio estimator sum(y_raion) / sum(y_oblast): it
    also absorbs the definitional mismatch between the volunteer oblast alert
    and the official raion alerts, so that p_oblast * share is calibrated on
    average. Clipped to [0, 1]; 1 when the oblast had no event (then the
    oblast forecast is used as is); NaN for the first `window` origins.
    """
    yr = np.nan_to_num(y_raion.astype(np.float64))
    yo = np.nan_to_num(y_oblast.astype(np.float64))
    n = yr.shape[1]
    cr = np.concatenate([np.zeros((yr.shape[0], 1)), np.cumsum(yr, axis=1)], axis=1)
    co = np.concatenate([np.zeros((yo.shape[0], 1)), np.cumsum(yo, axis=1)], axis=1)
    out = np.full(yr.shape, np.nan)
    i = np.arange(window, n)
    num = cr[:, i - h + 1] - cr[:, i - window]
    den = co[:, i - h + 1] - co[:, i - window]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, i] = np.where(den > 0, np.minimum(num / np.where(den > 0, den, 1), 1.0), 1.0)
    return out
