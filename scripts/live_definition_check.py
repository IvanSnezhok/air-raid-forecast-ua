"""Which live alert records reproduce the training data's "alert in the oblast"?

    python scripts/live_definition_check.py                       # official file, Dec 2025 - Sep 2026
    python scripts/live_definition_check.py --model --start 2026-01-01 --end 2026-05-01
    python scripts/live_definition_check.py --model --store live-data --start 2026-09-12 --end 2026-10-11T04:00

The model is trained on the volunteer (eTryvoga) file, one record per oblast.
Live, alerts.in.ua reports records per oblast, raion, hromada and city. The
official raion-level file from the same upstream repo (the state alert system
alerts.in.ua also reports; ends 2026-09-07), or the alerts.in.ua history kept
in the point-in-time store (--store), stands in for the live feed.

1. Events: how often a volunteer start / end coincides (within 3 min) with an
   official activation / all-clear in the same oblast, at each level.
2. Rules, rebuilt from the per-location records and compared with the
   volunteer file: records per day, mean length, share of hourly origins with
   an alert running, Cohen's kappa of that state.
3. --model: the decisive check. A LightGBM per target/horizon is trained on the
   volunteer file before --start; on the hours of [--start + 8 d, --end) its
   inputs are rebuilt with each rule instead of the volunteer file, either the
   current state only (alert running, elapsed, national/spatial "now") or all
   recent history, and scored against the volunteer labels (Brier).

Rules: oblast_only (records with level == oblast), any_union (every record,
capped at 12 h, merged: the old live merge plus the cap), and
latest_until_clear (live_alerts.oblast_intervals) with several expiries.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from airraid.data import MAX_ALERT_HOURS, RAW_PATH, _merge, build_grid, load_alerts  # noqa: E402
from airraid.features import (  # noqa: E402
    HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels,
)
from airraid.live_alerts import oblast_intervals  # noqa: E402

OFFICIAL_URL = ("https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/"
                "main/datasets/official_data_en.csv")
OFFICIAL_PATH = RAW_PATH.parent / "official_data_en.csv"
FOCUS = ["Kharkivska oblast", "Dnipropetrovska oblast", "Zaporizka oblast", "Donetska oblast", "Sumska oblast",
         "Kyiv City", "Cherkaska oblast"]
EXPIRIES = (12, 6, 3, 2, 1)


def official() -> pd.DataFrame:
    if not OFFICIAL_PATH.exists():
        urllib.request.urlretrieve(OFFICIAL_URL, OFFICIAL_PATH)
    return pd.read_csv(OFFICIAL_PATH, parse_dates=["started_at", "finished_at"])


def from_store(root: Path, now: pd.Timestamp) -> pd.DataFrame:
    from airraid import store

    h = store.load(root, "alerts_history")
    h = h[h["alert_type"] == "air_raid"].copy()
    h["finished_at"] = h["finished_at"].fillna(now)  # still running when pulled
    return h


def near(a: np.ndarray, b: np.ndarray, tol: float = 3.0) -> np.ndarray:
    """For each value of a, is there a value of b within tol (minutes)?"""
    b = np.sort(b)
    i = np.searchsorted(b, a)
    d = np.full(len(a), np.inf)
    ok = i < len(b)
    d[ok] = np.abs(b[i[ok]] - a[ok])
    ok = i > 0
    d[ok] = np.minimum(d[ok], np.abs(b[i[ok] - 1] - a[ok]))
    return d <= tol


def minutes(s, t0) -> np.ndarray:
    return ((pd.to_datetime(pd.Series(s), utc=True) - t0) / pd.Timedelta(minutes=1)).to_numpy(np.float64)


def rules(o: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Oblast-level intervals (region, start, end, naive) under each rule."""
    def union(q):
        return pd.DataFrame({"region": q["oblast"], "start": q["started_at"], "naive": False,
                             "end": np.minimum(q["finished_at"], q["started_at"] + pd.Timedelta(hours=MAX_ALERT_HOURS))})
    out = {"oblast_only": union(o[o["level"] == "oblast"]), "any_union": union(o)}
    for cap in EXPIRIES:
        out[f"latest_until_clear_{cap}h"] = oblast_intervals(o, as_of, cap_hours=cap).assign(naive=False)[
            ["region", "start", "end", "naive"]]
    return out


def events(o: pd.DataFrame, v: pd.DataFrame, t0) -> pd.DataFrame:
    rows = []
    for region in sorted(v["region"].unique()):
        vv, oo = v[v["region"] == region], o[o["oblast"] == region]
        vs, ve = minutes(vv["start"], t0), minutes(vv["end"], t0)
        row = {"region": region, "volunteer": len(vv)}
        for lvl in ("oblast", "raion", "hromada", "any"):
            q = oo if lvl == "any" else oo[oo["level"] == lvl]
            row[f"n_{lvl}"] = len(q)
            row[f"start~{lvl}"] = near(vs, minutes(q["started_at"], t0)).mean() if len(q) else 0.0
        row["end~any"] = near(ve, minutes(oo["finished_at"], t0)).mean() if len(oo) else 0.0
        rows.append(row)
    return pd.DataFrame(rows).set_index("region")


def state(iv: pd.DataFrame, regions, T: pd.DatetimeIndex) -> np.ndarray:
    """[region, origin]: an interval covers the minute before the origin."""
    out = np.zeros((len(regions), len(T)), bool)
    t0 = T[0] - pd.Timedelta(days=30)
    t = minutes(T, t0) - 1
    for i, r in enumerate(regions):
        q = iv[iv["region"] == r]
        if len(q):
            s, e = _merge(np.floor(minutes(q["start"], t0)).astype(np.int64),
                          np.ceil(minutes(q["end"], t0)).astype(np.int64))
            j = np.searchsorted(s, t, "right") - 1
            out[i] = (j >= 0) & (e[np.maximum(j, 0)] > t)
    return out


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    po, pa, pb = (a == b).mean(), a.mean(), b.mean()
    pe = pa * pb + (1 - pa) * (1 - pb)
    return float((po - pe) / (1 - pe)) if pe < 1 else float("nan")


def model_check(v: pd.DataFrame, R: dict[str, pd.DataFrame], lo, hi, threads: int) -> pd.DataFrame:
    from airraid.models import GBM

    vg = build_grid(v[v["start"] < hi], data_end=hi)
    H, S = hourly_fraction(vg), hourly_starts(vg)
    K_tr = np.arange(vg.minute(lo - pd.DateOffset(months=12)) // 60, vg.minute(lo) // 60 - max(HORIZONS))
    K_te = np.arange(vg.minute(lo + pd.Timedelta(days=8)) // 60, vg.minute(hi) // 60 - max(HORIZONS))
    base_tr = build_features(vg, K_tr, H)
    models = {}
    for h in HORIZONS:
        df = add_horizon_features(base_tr, vg, H, h, S)
        for t in ("active", "onset"):
            y = labels(vg, K_tr, h, H, t, S)
            m = ~np.isnan(y)
            models[(t, h)] = GBM(n_estimators=300, params={"num_threads": threads}).fit(df[m], y[m])
    del base_tr
    Y = {(t, h): labels(vg, K_te, h, H, t, S) for h in HORIZONS for t in ("active", "onset")}
    base_v = build_features(vg, K_te, H)
    hist = v[v["start"] < lo]
    res = []
    for name, iv in {"volunteer": v[(v["start"] >= lo) & (v["start"] < hi)], **R}.items():
        g = build_grid(pd.concat([hist, iv[iv["start"] >= lo]], ignore_index=True), data_end=hi)
        assert g.regions == vg.regions and g.t0 == vg.t0
        Hg = hourly_fraction(g)
        base_g = build_features(g, K_te, Hg)
        now_cols = [c for c in base_g.columns if c in ("active_now", "elapsed_min", "nat_active_now")
                    or c.startswith("now__")]
        mixed = base_v.copy()
        mixed[now_cols] = base_g[now_cols].to_numpy()
        for variant, base, gx, Hx in (("state_only", mixed, vg, H), ("all_recent", base_g, g, Hg)):
            Sx = S if variant == "state_only" else hourly_starts(g)
            for h in HORIZONS:
                x = add_horizon_features(base, gx, Hx, h, Sx)
                for t in ("active", "onset"):
                    p, y = models[(t, h)].predict(x), Y[(t, h)]
                    res.append({"variant": variant, "rule": name, "target": t, "h": h,
                                "brier": float(np.mean((p - y) ** 2))})
    return pd.DataFrame(res)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2025-12-01")
    ap.add_argument("--end", default="2026-09-06")
    ap.add_argument("--store", type=Path, help="use the alerts.in.ua history in this store instead of the official file")
    ap.add_argument("--model", action="store_true", help="also run the model-based check (a few minutes)")
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()
    lo, hi = pd.Timestamp(args.start, tz="UTC"), pd.Timestamp(args.end, tz="UTC")
    pd.set_option("display.width", 220)

    v = load_alerts()  # same cleaning and 12 h cap as training
    o = from_store(args.store, hi) if args.store else official()
    o = o[o["oblast"].isin(set(v["region"])) & (o["started_at"] < hi)
          & (o["finished_at"] > lo - pd.Timedelta(hours=MAX_ALERT_HOURS))]
    vv = v[(v["start"] >= lo) & (v["start"] < hi)]
    print(f"Source: {args.store or 'official file'}; {lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M}")
    print("records by level:", o["level"].value_counts().to_dict(), "| volunteer records:", len(vv), "\n")

    t0 = lo - pd.Timedelta(days=2)
    ev = events(o[o["started_at"] >= lo], vv, t0)
    w = ev["volunteer"]
    print("== 1. volunteer starts/ends matched (3 min) by records of the same oblast ==")
    print(ev.loc[[r for r in FOCUS if r in ev.index]].round(2).to_string())
    print("weighted by volunteer records:",
          {c: round(float((ev[c] * w).sum() / w.sum()), 3) for c in ev.columns if "~" in c}, "\n")

    R = rules(o, hi)
    T = pd.date_range(lo, hi, freq="h", inclusive="left")
    regions = sorted(v["region"].unique())
    vs = state(v[(v["end"] > lo - pd.Timedelta(hours=MAX_ALERT_HOURS)) & (v["start"] < hi)], regions, T)
    days = (hi - lo) / pd.Timedelta(days=1)
    rows = [{"rule": "volunteer", "records_per_day": len(vv) / days,
             "mean_hours": ((vv["end"] - vv["start"]) / pd.Timedelta(hours=1)).mean(), "share_active": vs.mean(),
             "kappa": 1.0}]
    for name, iv in R.items():
        st = state(iv, regions, T)
        iv = iv[(iv["start"] >= lo) & (iv["start"] < hi)]
        rows.append({"rule": name, "records_per_day": len(iv) / days,
                     "mean_hours": ((iv["end"] - iv["start"]) / pd.Timedelta(hours=1)).mean(),
                     "share_active": st.mean(), "kappa": float(np.nanmean([kappa(a, b) for a, b in zip(st, vs)]))})
    print("== 2. rules vs the volunteer file (hourly origins; kappa averaged over oblasts) ==")
    print(pd.DataFrame(rows).set_index("rule").round(3).to_string(), "\n")

    if args.model:
        r = model_check(v, R, lo, hi, args.threads)
        print("== 3. Brier of the volunteer-trained model with inputs rebuilt by each rule ==")
        print(r.pivot_table(index=["variant", "rule"], columns=["target", "h"], values="brier").round(4).to_string())
        print(r.groupby(["variant", "rule"])["brier"].mean().unstack(0).round(4).to_string())


if __name__ == "__main__":
    main()
