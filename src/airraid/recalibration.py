"""Learning from verified forecasts: a guarded recalibration layer on top of the models.

For every target x horizon a two-parameter logistic (Platt) map

    p = sigmoid(a + b * logit(p_raw))

is fitted to the forecasts verified over the last LOOKBACK days (p_raw: the model's own
probability, y: what happened), shrunk towards the identity (a = 0, b = 1) by a ridge
penalty, and served only where it helped on the most recent verified day.

Point in time
-------------
* verify.py fits it at time `now` from the forecasts verified so far: every row's outcome
  window has closed and its label is known at `now` (`fit(rows, as_of=now)` also drops rows
  verified later or whose window ends after `as_of`, so it can be refitted from the store
  as of any moment). The result is appended to the store (kind `calibration`, known-at time
  `fitted_at`).
* predict_now.py at origin T applies the newest calibration with fitted_at <= T (store
  `as_of=T`, at most MAX_AGE old); without one it serves the raw probability. Each logged
  row keeps both: `p_raw` (the model) and `p` (served), with `calibration` = "platt" or
  "none" (tests/test_recalibration.py).

Guard
-----
Forecasts are issued every 10 minutes, so consecutive rows overlap almost completely.
Rows are weighted 1 / (forecasts of that region in the same clock hour): the sample is
counted in region-hours, never in 10-minute runs. For h > 1 consecutive windows still
overlap, so the ridge grows with the horizon (RIDGE * h pseudo region-hours at the
identity). A map is served only when
  * the fit part (outcome windows closed before the check day starts) spans at least
    MIN_FIT_HOURS distinct origin hours (one full day-night cycle: alerts are strongly
    diurnal) and the check part MIN_CHECK_HOURS, each with MIN_EVENTS region-hours with
    and without an alert, and
  * on the check part (the last CHECK of verified origins, never used for that fit) it
    lowers the Brier score of the raw model by at least MIN_GAIN (relative).
Then it is refitted on all rows and served; otherwise the identity. Labels are those of
verify.py (the dataset where it covers the window, else alerts.in.ua records).

Raion forecasts derived from the oblast probability (oblast, oblast x share) use the
served oblast probability; raion models stay raw (they are not verified per raion).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import store
from .features import HORIZONS

TARGETS = ("active", "onset")
LOOKBACK = pd.Timedelta(days=7)
CHECK = pd.Timedelta(hours=24)
MIN_FIT_HOURS = 24
MIN_CHECK_HOURS = 12
MIN_EVENTS = 25.0
MIN_GAIN = 0.01
RIDGE = 20.0
MAX_AGE = pd.Timedelta(hours=6)
EPS = 1e-3
KEY = ["origin", "region", "target", "horizon"]
STATE_COLS = ["target", "horizon", "active", "reason", "a", "b", "n_rows", "n_hours", "n_eff", "n_fit_hours",
              "n_check_hours", "brier_raw", "brier_cal", "gain", "data_from", "data_to", "fitted_at"]


def logit(p) -> np.ndarray:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def transform(p_raw, a: float, b: float) -> np.ndarray:
    z = np.clip(a + b * logit(p_raw), -30, 30)
    return 1 / (1 + np.exp(-z))


def _fit_platt(x: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float) -> tuple[float, float]:
    """Weighted logistic regression of y on x = logit(p_raw), ridge towards (a, b) = (0, 1). Newton steps."""
    a, b = 0.0, 1.0
    for _ in range(100):
        q = 1 / (1 + np.exp(-np.clip(a + b * x, -30, 30)))
        r = w * (q - y)
        g = np.array([r.sum() + lam * a, (r * x).sum() + lam * (b - 1)])
        s = w * q * (1 - q)
        hess = np.array([[s.sum() + lam, (s * x).sum()], [(s * x).sum(), (s * x * x).sum() + lam]])
        step = np.linalg.solve(hess, g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-9:
            break
    return float(a), float(b)


def _brier(y, p, w) -> float:
    return float(np.sum(w * (p - y) ** 2) / np.sum(w))


def training_rows(rows: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Verified rows usable at `as_of`: verified by then (when `verified_at` is known), outcome
    window closed by then, issued in the last LOOKBACK; one row per forecast (dataset labels
    preferred over store labels). `p_raw` missing (rows logged before this layer existed, when
    the raw probability was served) is taken from `p`."""
    if rows is None or rows.empty or "y" not in rows:
        return pd.DataFrame(columns=KEY + ["p_raw", "y"])
    v = rows.copy()
    v["origin"] = pd.to_datetime(v["origin"], utc=True)
    v["p_raw"] = (pd.to_numeric(v["p_raw"], errors="coerce") if "p_raw" in v else pd.Series(np.nan, index=v.index)
                  ).fillna(pd.to_numeric(v["p"], errors="coerce"))
    v = v.dropna(subset=["y", "p_raw"])
    keep = (v["origin"] + pd.to_timedelta(v["horizon"].astype(float), unit="h") <= as_of) \
        & (v["origin"] >= as_of - LOOKBACK)
    for col in ("verified_at", "issued_at"):
        if col in v:
            keep &= pd.to_datetime(v[col], utc=True).fillna(as_of) <= as_of
    v = v[keep]
    if v.empty:
        return v.reset_index(drop=True)
    v = v.assign(_d=v["label_source"].eq("dataset") if "label_source" in v else False)
    by = ["_d"] + (["verified_at"] if "verified_at" in v else [])
    v = v.sort_values(by, kind="stable").drop_duplicates(KEY, keep="last").drop(columns="_d")
    return v.reset_index(drop=True)


def _state(t: str, h: int, **kw) -> dict:
    out = {c: None for c in STATE_COLS}
    out.update(target=t, horizon=int(h), active=False, a=0.0, b=1.0, n_rows=0, n_hours=0, n_eff=0.0,
               n_fit_hours=0, n_check_hours=0)
    out.update(kw)
    return out


def fit_one(q: pd.DataFrame, t: str, h: int) -> dict:
    """Calibration state of one target x horizon from its training rows (see the module doc)."""
    if q.empty:
        return _state(t, h, reason="collecting")
    hour = q["origin"].dt.floor("h")
    w = 1.0 / q.groupby([q["region"], hour])["y"].transform("size").to_numpy(float)
    x, y = logit(q["p_raw"].to_numpy(float)), q["y"].to_numpy(float)
    o = q["origin"]
    split = o.max() - CHECK
    fit_m = (o + pd.Timedelta(hours=h) <= split).to_numpy()
    chk_m = (o >= split).to_numpy()
    base = dict(n_rows=int(len(q)), n_hours=int(hour.nunique()), n_eff=round(float(w.sum()), 1),
                n_fit_hours=int(hour[fit_m].nunique()), n_check_hours=int(hour[chk_m].nunique()),
                data_from=o.min(), data_to=o.max())
    lam = RIDGE * h

    def enough(m, min_hours):
        return hour[m].nunique() >= min_hours and (w[m] * y[m]).sum() >= MIN_EVENTS \
            and (w[m] * (1 - y[m])).sum() >= MIN_EVENTS

    if not (enough(fit_m, MIN_FIT_HOURS) and enough(chk_m, MIN_CHECK_HOURS)):
        return _state(t, h, reason="collecting", **base)
    a, b = _fit_platt(x[fit_m], y[fit_m], w[fit_m], lam)
    p_raw = q["p_raw"].to_numpy(float)[chk_m]
    br = _brier(y[chk_m], p_raw, w[chk_m])
    bc = _brier(y[chk_m], transform(p_raw, a, b), w[chk_m])
    gain = 1 - bc / br if br > 0 else 0.0
    check = dict(brier_raw=round(br, 5), brier_cal=round(bc, 5), gain=round(gain, 4))
    if gain < MIN_GAIN:
        return _state(t, h, reason="no_gain", **base, **check)
    a, b = _fit_platt(x, y, w, lam)  # serve the map refitted on every row
    return _state(t, h, active=True, reason="ok", a=round(a, 5), b=round(b, 5), **base, **check)


def fit(rows: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Calibration state per target x horizon (one row each) from verified forecasts known at `as_of`."""
    v = training_rows(rows, as_of)
    out = []
    for t in TARGETS:
        for h in HORIZONS:
            q = v[(v["target"] == t) & (v["horizon"] == h)] if len(v) else v
            out.append(fit_one(q.reset_index(drop=True), t, h))
    df = pd.DataFrame(out, columns=STATE_COLS)
    df["fitted_at"] = as_of.floor("s")
    df["active"] = df["active"].astype(bool)
    df["reason"] = df["reason"].astype(str)
    for c in ("a", "b", "n_eff", "brier_raw", "brier_cal", "gain"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    for c in ("horizon", "n_rows", "n_hours", "n_fit_hours", "n_check_hours"):
        df[c] = df[c].astype(int)
    for c in ("data_from", "data_to"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def latest(store_root: Path | str, as_of: pd.Timestamp) -> pd.DataFrame | None:
    """The newest calibration in the store known at `as_of` (fitted_at <= as_of), at most MAX_AGE old."""
    c = store.load(store_root, "calibration", as_of=as_of, since=as_of - MAX_AGE)
    if c.empty:
        return None
    c = c[c["fitted_at"] == c["fitted_at"].max()]
    return c.reset_index(drop=True)


def apply(log: pd.DataFrame, cal: pd.DataFrame | None) -> pd.DataFrame:
    """Forecast rows with `p_raw` (the model), `p` (served), `calibration` ("platt" | "none") and
    `calibrated_at` (fitted_at of the map used). Only active maps change `p`."""
    out = log.copy()
    out["p_raw"] = out["p"].astype(float)
    out["calibration"] = "none"
    out["calibrated_at"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    if cal is None or cal.empty:
        return out
    for r in cal[cal["active"].astype(bool)].itertuples(index=False):
        m = (out["target"] == r.target) & (out["horizon"] == int(r.horizon))
        out.loc[m, "p"] = np.round(transform(out.loc[m, "p_raw"].to_numpy(float), float(r.a), float(r.b)), 4)
        out.loc[m, "calibration"] = "platt"
        out.loc[m, "calibrated_at"] = pd.Timestamp(r.fitted_at)
    return out


def describe(cal: pd.DataFrame | None) -> dict:
    """JSON-ready state for the API, status.json and reports/calibration.json."""
    if cal is None or cal.empty:
        return {"fitted_at": None, "n_active": 0, "n_total": len(TARGETS) * len(HORIZONS), "groups": [],
                "min_fit_hours": MIN_FIT_HOURS, "check_hours": int(CHECK / pd.Timedelta(hours=1))}

    def js(v):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return None
        if isinstance(v, pd.Timestamp):
            return None if pd.isna(v) else v.isoformat()
        if isinstance(v, (np.bool_, bool)):
            return bool(v)
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return None if not np.isfinite(v) else float(v)
        return v

    groups = [{c: js(r[c]) for c in STATE_COLS if c in r} for r in cal.to_dict("records")]
    for g in groups:
        g.pop("fitted_at", None)
    return {"fitted_at": js(pd.Timestamp(cal["fitted_at"].max())), "n_active": int(cal["active"].astype(bool).sum()),
            "n_total": len(TARGETS) * len(HORIZONS), "groups": groups,
            "min_fit_hours": MIN_FIT_HOURS, "check_hours": int(CHECK / pd.Timedelta(hours=1))}
