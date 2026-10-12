"""The "History" tab: the record of issued forecasts against what happened, aggregated
server-side so the page downloads a few hundred kB (airraid_app.engine.history, /api/history
and /api/history/timeline).

Input: the issued forecasts `J` (origin, region, target, horizon, p, clim, active_now, y with
NaN while pending, optionally label_source, p_raw, calibration, persist), as the engine builds
them from the store (verify.py's outcomes) or from the server's journal. Every verified
forecast counts once (as in verify.py's summary and /api/verification).

Baselines, both known at the origin:
  * climatology (`clim`): the share of the last 28 days with an alert in the same window;
  * persistence (`persist`): "what is now will be": an alert on now (target "active"), an
    alert started in the last h hours (target "onset"), as in training (models.Persistence).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import HORIZONS, KYIV
from .recalibration import TARGETS
from .verification import WINDOWS, raw_p

BLOCK_H = 6  # skill over time: 6-hour blocks of origins (Kyiv 00-06, 06-12, ...)
REL_BINS = 10
REL_MIN = 20  # a reliability bin is shown with at least this many verified forecasts
TIMELINE_DAYS = 7


def _f(v, d: int = 4):
    return None if v is None or not np.isfinite(v) else round(float(v), d)


def persistence(J: pd.DataFrame, grid=None) -> np.ndarray:
    """0/1 persistence forecast per row: active_now for "active"; for "onset" an alert started
    within [origin - h, origin) in `grid` (an AlertGrid covering the origins), else NaN."""
    out = np.full(len(J), np.nan)
    tg = J["target"].to_numpy()
    if "active_now" in J:
        a = tg == "active"
        out[a] = pd.to_numeric(J["active_now"], errors="coerce").to_numpy(float)[a]
    if grid is not None and grid.n_minutes:
        origin = pd.to_datetime(J["origin"], utc=True)
        om = ((origin - grid.t0) / pd.Timedelta(minutes=1)).to_numpy().astype(np.int64)
        lo = om - 60 * J["horizon"].to_numpy().astype(np.int64)
        idx = {r: i for i, r in enumerate(grid.regions)}
        reg = J["region"].to_numpy()
        for r, i in idx.items():
            m = (tg == "onset") & (reg == r) & (om <= grid.n_minutes) & (lo >= 0)
            if m.any():
                s = np.sort(grid.starts[i])
                out[m] = (np.searchsorted(s, om[m], "left") - np.searchsorted(s, lo[m], "left") > 0).astype(float)
    return out


def _scores(q: pd.DataFrame) -> dict:
    y = q["y"].to_numpy(float)
    p, pr, c = q["p"].to_numpy(float), raw_p(q).to_numpy(float), q["clim"].to_numpy(float)
    bm, br, bc = (float(np.mean((x - y) ** 2)) for x in (p, pr, c))
    out = {"n": int(len(q)), "n_runs": int(q["origin"].nunique()), "base_rate": _f(y.mean(), 3),
           "brier": _f(bm), "brier_raw": _f(br), "brier_clim": _f(bc),
           "bss_vs_clim": _f(1 - bm / bc, 3) if bc > 0 else None,
           "bss_raw_vs_clim": _f(1 - br / bc, 3) if bc > 0 else None,
           "n_calibrated": int((q["calibration"] == "platt").sum()) if "calibration" in q else 0}
    if "persist" in q and q["persist"].notna().all():
        bp = float(np.mean((q["persist"].to_numpy(float) - y) ** 2))
        out.update(brier_pers=_f(bp), bss_vs_pers=_f(1 - bm / bp, 3) if bp > 0 else None)
    else:
        out.update(brier_pers=None, bss_vs_pers=None)
    return out


def _reliability(y: np.ndarray, p: np.ndarray) -> list:
    idx = np.clip((p * REL_BINS).astype(int), 0, REL_BINS - 1)
    out = []
    for b in range(REL_BINS):
        m = idx == b
        if m.sum() >= REL_MIN:
            out.append([round(float(p[m].mean()), 3), round(float(y[m].mean()), 3), int(m.sum())])
    return out


def summary(J: pd.DataFrame, known: pd.Timestamp | None, calibration: dict | None = None) -> dict:
    """/api/history: counts, scores over 24 h / 7 days, skill per 6-hour block, reliability,
    the state of the recalibration layer."""
    out = {"known_until": None if known is None else known.isoformat(), "journal_since": None,
           "n_runs": 0, "n_runs_verified": 0, "n_verified": 0, "n_pending": 0, "label_sources": {},
           "windows": {}, "blocks": {"hours": BLOCK_H, "start": [], "series": {}}, "reliability": {},
           "calibration": calibration}
    if J is None or J.empty:
        return out
    J = J.assign(origin=pd.to_datetime(J["origin"], utc=True))
    V = J.dropna(subset=["y"])
    out.update(journal_since=J["origin"].min().isoformat(), n_runs=int(J["origin"].nunique()),
               n_runs_verified=int(V["origin"].nunique()), n_verified=int(len(V)), n_pending=int(len(J) - len(V)))
    if "label_source" in V and len(V):
        out["label_sources"] = {str(k): int(n) for k, n in V["label_source"].value_counts().items()}
    if V.empty:
        return out
    ref = known if known is not None else V["origin"].max()
    for name, d in WINDOWS:
        w = V[V["origin"] >= ref - d]
        out["windows"][name] = [{"target": t, "horizon": int(h), **_scores(q)}
                                for (t, h), q in w.groupby(["target", "horizon"])]
    # skill over time: blocks of BLOCK_H hours of Kyiv time
    local = V["origin"].dt.tz_convert(KYIV).dt.tz_localize(None).dt.floor(f"{BLOCK_H}h")
    blocks = sorted(local.unique())
    out["blocks"]["start"] = [pd.Timestamp(b).tz_localize(KYIV, ambiguous=True, nonexistent="shift_forward")
                              .tz_convert("UTC").isoformat() for b in blocks]
    pos = {b: i for i, b in enumerate(blocks)}
    for (t, h), q in V.groupby(["target", "horizon"]):
        ser = {k: [None] * len(blocks) for k in ("n", "brier", "brier_raw", "brier_clim", "brier_pers")}
        for b, qq in q.groupby(local[q.index]):
            s, i = _scores(qq), pos[b]
            for k in ser:
                ser[k][i] = s[k]
        out["blocks"]["series"][f"{t}_h{int(h)}"] = ser
        y = q["y"].to_numpy(float)
        rel = {"served": _reliability(y, q["p"].to_numpy(float))}
        if "calibration" in q and (q["calibration"] == "platt").any():
            rel["raw"] = _reliability(y, raw_p(q).to_numpy(float))
        out["reliability"][f"{t}_h{int(h)}"] = rel
    return out


def timeline(J: pd.DataFrame, known: pd.Timestamp | None, days: int = TIMELINE_DAYS) -> dict:
    """/api/history/timeline: for every target x horizon one forecast per hour (the latest verified
    one issued in that hour, else the latest) over the last `days` days: its origin, and per oblast
    the probability issued and whether the alert happened (null while pending)."""
    out = {"known_until": None if known is None else known.isoformat(), "start": None, "n_hours": 0,
           "regions": [], "series": {}}
    if J is None or J.empty:
        return out
    J = J.assign(origin=pd.to_datetime(J["origin"], utc=True))
    end = J["origin"].max().floor("h")
    start = max(J["origin"].min().floor("h"), end - pd.Timedelta(days=days) + pd.Timedelta(hours=1))
    J = J[J["origin"] >= start]
    n = int((end - start) / pd.Timedelta(hours=1)) + 1
    regions = sorted(J["region"].unique())
    out.update(start=start.isoformat(), n_hours=n, regions=regions)
    for t in TARGETS:
        for h in HORIZONS:
            q = J[(J["target"] == t) & (J["horizon"] == h)]
            if q.empty:
                continue
            runs = q.groupby("origin")["y"].apply(lambda s: s.notna().any()).rename("v").reset_index()
            runs["hour"] = runs["origin"].dt.floor("h")
            pick = runs.sort_values(["v", "origin"]).groupby("hour").tail(1)
            q = q[q["origin"].isin(pick["origin"])]
            k = ((q["origin"].dt.floor("h") - start) / pd.Timedelta(hours=1)).astype(int).to_numpy()
            ri = {r: i for i, r in enumerate(regions)}
            P = np.full((len(regions), n), np.nan)
            Y = np.full((len(regions), n), np.nan)
            r_idx = q["region"].map(ri).to_numpy()
            P[r_idx, k] = q["p"].to_numpy(float)
            Y[r_idx, k] = q["y"].to_numpy(float)
            om = [None] * n
            for o in pick["origin"]:
                om[int((o.floor("h") - start) / pd.Timedelta(hours=1))] = int(o.minute)
            out["series"][f"{t}_h{h}"] = {
                "minute": om,
                "p": [[None if np.isnan(x) else round(float(x), 3) for x in row] for row in P],
                "y": [[None if np.isnan(x) else int(x) for x in row] for row in Y],
            }
    return out
