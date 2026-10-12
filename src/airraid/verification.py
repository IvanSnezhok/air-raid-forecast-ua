"""Score issued forecasts against what actually happened (used by verify.py,
the static site and the web app).

The outcome of a forecast is looked up with exactly the training labels
(features.labels; tests/test_verify.py), in minutes from its origin, so origins
at any minute (the 10-minute pipeline) are scored the same way:
  * active, horizon h: an alert at any moment of [origin + (h-1) h, origin + h h);
  * onset,  horizon h: a new (merged) alert starts within [origin, origin + h h).
Labels come from
  * the volunteer dataset, complete up to its last hour (label_source "dataset",
    the training definition itself);
  * with a store, after the dataset ends: oblast-level alerts rebuilt from the
    alerts.in.ua history pulls and polls (live_alerts.oblast_intervals) up to
    where the store is complete (label_source "store"). These only approximate
    the volunteer labels; they are reported separately and are replaced by
    dataset labels once the dataset covers the window.
Forecast probabilities are never recomputed: they are the ones logged when the
forecast was issued.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import store
from .data import build_grid
from .live_alerts import LIVE_EXPIRY_HOURS, POLL_GAP, gap_alerts, store_records

WINDOWS = (("24h", pd.Timedelta(hours=24)), ("7d", pd.Timedelta(days=7)))
# A poll run that started after a gap knows nothing about alerts opened before it: a
# window is verified from such a run only if it starts this long after the run began.
LEAD_IN = pd.Timedelta(hours=LIVE_EXPIRY_HOURS)


def poll_runs(store_root: Path, as_of: pd.Timestamp, since: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Unbroken runs of polls (no gap over POLL_GAP) as (first poll, last poll)."""
    polls = store.load(store_root, "polls", as_of=as_of, since=since)
    if polls.empty:
        return []
    t = pd.Series(np.sort(pd.to_datetime(polls["observed_at"], utc=True).unique()))
    run = (t.diff() > POLL_GAP).cumsum()
    return [(r.iloc[0], r.iloc[-1]) for _, r in t.groupby(run)]


def label_grid(alerts: pd.DataFrame, store_root: Path | None, now: pd.Timestamp):
    """(grid, dataset_end, spans): the alert record labels are read from, and the spans
    (lo, hi) in which a forecast window [origin, origin + h) can be scored.

    The dataset up to its last full hour; with a store, extended by oblast-level
    alerts rebuilt from the store's records: up to where the store is complete
    since the dataset ended (history pulls, then unbroken polls), and inside any
    later unbroken run of polls after a lead-in of LIVE_EXPIRY_HOURS (polls that
    resume after an outage verify the windows they fully cover).
    """
    hist_end = alerts["start"].max()
    dataset_end = hist_end.floor("h")
    spans = [(None, dataset_end)]
    if store_root is not None:
        rec, covered = store_records(store_root, now, hist_end)
        if covered is not None and covered > dataset_end:
            spans.append((None, covered))
        spans += [(a + LEAD_IN, b) for a, b in poll_runs(store_root, now, hist_end - LEAD_IN) if b > a + LEAD_IN]
        end = max(hi for _, hi in spans).floor("min")
        if end > dataset_end:
            gap = gap_alerts(rec, end, alerts)
            return build_grid(pd.concat([alerts, gap], ignore_index=True), data_end=end), dataset_end, spans
    return build_grid(alerts, data_end=dataset_end), dataset_end, spans


def covered(origin: pd.Series, horizon: pd.Series, spans) -> np.ndarray:
    """Is the window [origin, origin + horizon h) inside one of the spans?"""
    o = pd.to_datetime(origin, utc=True)
    end = o + pd.to_timedelta(horizon, unit="h")
    ok = np.zeros(len(o), bool)
    for lo, hi in spans:
        ok |= ((end <= hi) & (True if lo is None else (o >= lo))).to_numpy()
    return ok


def outcomes(log: pd.DataFrame, grid) -> np.ndarray:
    """1/0 outcome per log row, NaN when the target window is not covered by the data yet."""
    origin = pd.to_datetime(log["origin"], utc=True)
    h = log["horizon"].to_numpy()
    o_min = ((origin - grid.t0) / pd.Timedelta(minutes=1)).to_numpy().astype(np.int64)
    region_idx = {r: i for i, r in enumerate(grid.regions)}
    y = np.full(len(log), np.nan)
    for n, (reg, t, om, hh) in enumerate(zip(log["region"], log["target"], o_min, h)):
        i = region_idx.get(reg)
        end = om + 60 * hh
        if i is None or end > grid.n_minutes or om < 0:
            continue
        if t == "active":
            y[n] = grid.active[i, end - 60 : end].any()
        else:  # onset: a new alert starts within [origin, origin + h)
            s = grid.starts[i]
            y[n] = np.any((s >= om) & (s < end))
    return y


def _brier(y, p):
    return float(np.mean((p - y) ** 2)) if len(y) else None


def summarise(v: pd.DataFrame, now: pd.Timestamp) -> dict:
    """Brier of the model vs the 28-day climatology, hit rate, per target / horizon / window."""
    out = {"verified_until_utc": str(now), "n_verified": int(len(v)), "groups": []}
    if v.empty:
        return out
    origin = pd.to_datetime(v["origin"], utc=True)
    for (t, h), q in v.groupby(["target", "horizon"]):
        o = origin[q.index]
        for label, lo in (*((w, now - d) for w, d in WINDOWS), ("all", None)):
            w = q if lo is None else q[o >= lo]
            if w.empty:
                continue
            y, p, c = w["y"].to_numpy(), w["p"].to_numpy(), w["clim"].to_numpy()
            bm, bc = _brier(y, p), _brier(y, c)
            hit = (p >= 0.5) == (y == 1)
            hi, lo_p = p >= 0.5, p < 0.2
            out["groups"].append({
                "target": t, "horizon_h": int(h), "window": label, "n": int(len(w)),
                "n_origins": int(o[w.index].nunique()),
                "base_rate": float(y.mean()), "brier_model": bm, "brier_clim": bc,
                "skill_vs_clim": None if not bc else 1 - bm / bc,
                "hit_rate_at_0.5": float(hit.mean()),
                "recall_at_0.5": float(((p >= 0.5) & (y == 1)).sum() / max(1, (y == 1).sum())),
                "false_alarm_ratio_at_0.5": float(((p >= 0.5) & (y == 0)).sum() / max(1, (p >= 0.5).sum())),
                "n_high": int(hi.sum()), "event_rate_high": float(y[hi].mean()) if hi.any() else None,
                "n_low": int(lo_p.sum()), "event_rate_low": float(y[lo_p].mean()) if lo_p.any() else None,
            })
    return out


def verify(log: pd.DataFrame, alerts: pd.DataFrame, store_root: Path | None = None,
           now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
    """Verified rows (with y, label_source) and the summary."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    grid, dataset_end, spans = label_grid(alerts, store_root, now)
    log = log.copy()
    log["y"] = np.where(covered(log["origin"], log["horizon"], spans), outcomes(log, grid), np.nan)
    window_end = pd.to_datetime(log["origin"], utc=True) + pd.to_timedelta(log["horizon"], unit="h")
    log["label_source"] = np.where(window_end <= dataset_end, "dataset", "store")
    v = log.dropna(subset=["y"]).reset_index(drop=True)
    v["hit_at_0.5"] = ((v["p"] >= 0.5) == (v["y"] == 1)).astype(int)
    v["brier_model"] = (v["p"] - v["y"]) ** 2
    v["brier_clim"] = (v["clim"] - v["y"]) ** 2
    record_end = grid.t0 + pd.Timedelta(minutes=grid.n_minutes)
    summary = summarise(v, record_end)
    summary["pending"] = int(log["y"].isna().sum())
    summary["n_issued"] = int(len(log))
    summary["n_origins_issued"] = int(pd.to_datetime(log["origin"], utc=True).nunique()) if len(log) else 0
    summary["journal_since_utc"] = str(pd.to_datetime(log["origin"], utc=True).min()) if len(log) else None
    summary["dataset_end_utc"] = str(dataset_end)
    summary["n_verified_by_label_source"] = {k: int(n) for k, n in v["label_source"].value_counts().items()}
    return v, summary


def _f(v, d: int = 5):
    return None if v is None or not np.isfinite(v) else round(float(v), d)


def _score_rows(J: pd.DataFrame) -> list[dict]:
    out = []
    for (t, h), q in J.groupby(["target", "horizon"]):
        y, p, c = q["y"].to_numpy(float), q["p"].to_numpy(float), q["clim"].to_numpy(float)
        bm, bc = float(np.mean((p - y) ** 2)), float(np.mean((c - y) ** 2))
        hi, lo = p >= 0.5, p < 0.2
        out.append({
            "target": t, "horizon": int(h), "n": int(len(q)), "n_origins": int(q["origin"].nunique()),
            "base_rate": _f(y.mean()), "brier": _f(bm), "brier_clim": _f(bc),
            "bss_vs_clim": _f(1 - bm / bc) if bc > 0 else None,
            "hit_rate": _f(((p >= 0.5) == (y == 1)).mean()),
            "n_high": int(hi.sum()), "hit_rate_high": _f(y[hi].mean()) if hi.any() else None,
            "n_low": int(lo.sum()), "event_rate_low": _f(y[lo].mean()) if lo.any() else None,
        })
    return out


def site_summary(J: pd.DataFrame, known: pd.Timestamp, region: str | None = None, recent: int = 24) -> dict:
    """/api/verification: issued forecasts `J` (origin, region, target, horizon, p, clim, y; y NaN
    while pending) scored over the last 24 h and 7 days of origins before `known` (the end of
    the alert record), and for `region` the recent forecast/outcome strip (one origin per hour)."""
    out = {"journal_since": None, "known_until": None if known is None else known.isoformat(),
           "n_issued": 0, "n_verified": 0, "n_forecasts_verified": 0, "scores": [], "scores_24h": [],
           "label_sources": {}, "recent": {}}
    if J is None or J.empty:
        return out
    J = J.assign(origin=pd.to_datetime(J["origin"], utc=True))
    V = J.dropna(subset=["y"])
    out.update(journal_since=J["origin"].min().isoformat(), n_issued=int(J["origin"].nunique()),
               n_verified=int(V["origin"].nunique()), n_forecasts_verified=int(len(V)))
    if "label_source" in V and len(V):
        out["label_sources"] = {str(k): int(n) for k, n in V["label_source"].value_counts().items()}
    ref = known if known is not None else V["origin"].max()
    for key, (_, d) in zip(("scores_24h", "scores"), WINDOWS):
        out[key] = _score_rows(V[V["origin"] >= ref - d]) if len(V) else []
    if region is not None:
        for t, h in (("active", 1), ("onset", 3)):
            q = J[(J["region"] == region) & (J["target"] == t) & (J["horizon"] == h)]
            # one forecast per hour: the latest verified one, else the latest
            q = q.assign(_v=q["y"].notna()).sort_values(["_v", "origin"])
            q = q.groupby(q["origin"].dt.floor("h")).tail(1).sort_values("origin").tail(recent)
            out["recent"][f"{t}_h{h}"] = [
                {"origin": o.isoformat(), "p": round(float(p), 3), "y": None if np.isnan(y) else int(y)}
                for o, p, y in zip(q["origin"], q["p"], q["y"].astype(float))
            ]
    return out
