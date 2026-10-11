"""Raion forecast on the latest 4 weeks, with raion labels from the alerts.in.ua history in the live store.

    python raion_latest.py --store live-data      # a checkout of the live-data branch
    python raion_latest.py --store live-data --threads 2

raion_run.py evaluates every method on the official file, which stopped on
2026-09-07. The store holds alerts.in.ua raion-level history from 2026-09-12
on, so the methods that need no 28-day raion history can be scored on the same
latest weeks as the oblast model (origins up to 2026-10-10 23:00 UTC):

  climatology_avail   the raion's same-hour share over the days of the last 28
                      with raion data (reference for skill; 7-12 Sep is a gap)
  persistence         0/1 from the raion's current state
  oblast              the oblast model, refit weekly exactly as served
  oblast_x_share      oblast probability x the raion's share of oblast events over
                      the available days of the last 28
  lightgbm_state      the current-state raion model (plan from reports/raions/
                      selection.json), refit weekly on all raion data known
                      before the fold (official file + store), 6 h embargo

The full raion model is left out: before 2026-10-10 the 28 days before an
origin always contain the gap.

Point in time: store rows are taken as known at each fold start
(`store.load(..., as_of)` semantics through airraid.raion_data), running alerts
are cut at the origin, and nothing from after an origin enters its features.
The history was pulled on 2026-10-11, so pulled records are "known" by their
event times, the same convention as the volunteer file.

Outputs reports/raions/latest_metrics.csv, latest_metrics.md, latest_by_oblast.csv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import build_grid, load_alerts
from airraid.evaluate import scores
from airraid.features import (
    HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels, origin_hours,
)
from airraid.models import GBM
from airraid.raion_data import FORECAST_RAIONS, alerts_as_of, load_history
from airraid.raion_features import STATE_COLS, build_raion_features, make_inputs, oblast_labels
from airraid.raions import BY_ID
from airraid_app.regions import uk_name
from raion_run import CACHE, EMBARGO_H, KEEP, REPORTS, TARGETS, folds, log, oblast_plans

TEST_DAYS = 27  # the store starts on 2026-09-12 20:18; the first origin needs 12 h of raion state before it
METHODS = ["climatology_avail", "persistence", "oblast", "oblast_x_share", "lightgbm_state"]
GRID_SINCE = pd.Timestamp("2025-08-01", tz="UTC")


def covered_hours(hist, t0: pd.Timestamp, n: int) -> np.ndarray:
    """1 for every hour of the grid that lies inside a covered span of the raion history."""
    ts = t0 + pd.to_timedelta(np.arange(n), unit="h")
    ok = np.zeros(n, bool)
    for a, b in hist.spans:
        ok |= (ts >= a) & (ts + pd.Timedelta(hours=1) <= b)
    return ok


def avail_climatology(H, S, avail, K, h, target):
    """Same-hour climatology over the available days of the last 28 ([R, len(K)])."""
    acc = np.zeros((H.shape[0], len(K)))
    cnt = np.zeros(len(K))
    for d in range(1, 29):
        if target == "active":
            idx = K + h - 1 - 24 * d
            a = avail[idx]
            acc += (H[:, idx] > 0) * a
        else:
            win = (K - 24 * d)[:, None] + np.arange(h)
            a = avail[win].all(axis=1)
            acc += S[:, win].max(axis=2) * a
        cnt += a
    with np.errstate(invalid="ignore", divide="ignore"):
        return acc / np.where(cnt > 0, cnt, np.nan)


def avail_share(y, yo, ok, K, h, window=28 * 24):
    """Ratio of raion to oblast events over origins of the last 28 days whose label window is covered and known."""
    yr = np.nan_to_num(y) * ok
    yob = np.nan_to_num(yo) * ok
    cr = np.concatenate([np.zeros((yr.shape[0], 1)), np.cumsum(yr, axis=1)], axis=1)
    co = np.concatenate([np.zeros((yob.shape[0], 1)), np.cumsum(yob, axis=1)], axis=1)
    out = np.full(yr.shape, np.nan)
    i = np.arange(window, len(K))
    num = cr[:, i - h + 1] - cr[:, i - window]
    den = co[:, i - h + 1] - co[:, i - window]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, i] = np.where(den > 0, np.minimum(num / np.where(den > 0, den, 1), 1.0), 1.0)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", type=Path, required=True, help="checkout of the live-data branch")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--reuse-oblast", action="store_true")
    args = ap.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    vol = load_alerts()
    hist = load_history(store_root=args.store, download=True)
    log(f"raion history spans: {hist.spans}")
    store_end = hist.known_until()
    data_end = min(store_end, vol["start"].max()).floor("h")
    test_end = data_end - pd.Timedelta(hours=max(HORIZONS) - 1)
    test_start = test_end - pd.Timedelta(days=TEST_DAYS)
    log(f"data_end {data_end}; test {test_start} .. {test_end}")

    ra = hist.alerts[hist.alerts["end"] > GRID_SINCE]
    regions = [r for r in FORECAST_RAIONS if BY_ID[r].oblast in set(vol["region"])]
    inp = make_inputs(alerts_as_of(ra, data_end), alerts_as_of(vol, data_end), data_end, regions=regions, t0=GRID_SINCE)
    g, H, S = inp.grid, inp.H, inp.S
    R = len(g.regions)
    avail = covered_hours(hist, g.t0, H.shape[1])
    hour = lambda ts: g.minute(ts) // 60  # noqa: E731

    # oblast model, weekly walk-forward exactly as served (24-month window)
    cache = CACHE / "latest_oblast_preds.parquet"
    if args.reuse_oblast and cache.exists():
        OP = pd.read_parquet(cache)
    else:
        vg = build_grid(alerts_as_of(vol, data_end), data_end=data_end)  # full history, as the served model
        vH, vS = hourly_fraction(vg), hourly_starts(vg)
        vhour = lambda ts: vg.minute(ts) // 60  # noqa: E731
        plans = oblast_plans()
        Kv = origin_hours(vg, test_start - pd.DateOffset(months=24), test_end)
        base = build_features(vg, Kv, vH)
        out = []
        for h in HORIZONS:
            df = add_horizon_features(base, vg, vH, h, vS)
            origin = df["origin_hour"].to_numpy()
            for t in TARGETS:
                months, trees = plans[f"{t}_h{h}"]
                y = labels(vg, Kv, h, vH, t, vS)
                for fs, fe in folds(test_start, test_end):
                    tr = ~np.isnan(y) & (origin >= vhour(fs - pd.DateOffset(months=months))) & (origin < vhour(fs) - EMBARGO_H)
                    te = (origin >= vhour(fs)) & (origin < vhour(fe))
                    gbm = GBM(n_estimators=trees, params={"num_threads": args.threads}).fit(df[tr], y[tr])
                    out.append(pd.DataFrame({"origin": vg.t0 + pd.to_timedelta(origin[te], unit="h"),
                                             "oblast": df.loc[te, "region"].to_numpy(), "target": t, "horizon": h,
                                             "p": gbm.predict(df[te])}))
                    log(f"oblast {t} h={h} fold {fs:%m-%d}")
            del df
        OP = pd.concat(out, ignore_index=True)
        OP.to_parquet(cache)

    sel = json.loads((REPORTS / "selection.json").read_text())
    first = test_start - pd.DateOffset(months=9)
    K = origin_hours(g, first - pd.Timedelta(days=29), test_end)
    base = build_raion_features(inp, K)
    origin = base["origin_hour"].to_numpy()
    ts = g.t0 + pd.to_timedelta(K, unit="h")
    # an origin is usable for the state model when the 13 h before it and its label window are covered
    ok_state = np.array([avail[k - 13:k + max(HORIZONS)].all() for k in K])
    oblast_of = np.array([BY_ID[r].oblast for r in g.regions])
    preds = []
    for h in HORIZONS:
        df = add_horizon_features(base, g, H, h, S)
        for t, (_, pers_col) in TARGETS.items():
            y = labels(g, K, h, H, t, S)
            ok_lab = np.array([avail[k:k + h].all() for k in K])
            yo = oblast_labels(inp, K, h, t)
            share = avail_share(y.reshape(R, len(K)), yo, ok_lab[None, :], K, h).reshape(-1)
            clim = avail_climatology(H, S, avail, K, h, t).reshape(-1)
            q = OP[(OP["target"] == t) & (OP["horizon"] == h)].pivot(index="origin", columns="oblast", values="p").reindex(ts)
            p_obl = np.vstack([q[o].to_numpy() if o in q else np.full(len(K), np.nan) for o in oblast_of]).reshape(-1)
            plan = sel[f"{t}_h{h}"]["lightgbm_state"]
            okr = np.tile(ok_state, R)
            for i, (fs, fe) in enumerate(folds(test_start, test_end)):
                te = (origin >= hour(fs)) & (origin < hour(fe)) & okr
                tr = okr & ~np.isnan(y) & (origin >= hour(fs - pd.DateOffset(months=plan["best_months"]))) \
                    & (origin < hour(fs) - EMBARGO_H)
                gbm = GBM(n_estimators=plan["best_trees"], params={"num_threads": args.threads})
                gbm.fit(df.loc[tr, KEEP + STATE_COLS], y[tr])
                preds.append(pd.DataFrame({
                    "origin": g.t0 + pd.to_timedelta(origin[te], unit="h"), "raion": df.loc[te, "region"].to_numpy(),
                    "target": t, "horizon": h, "fold": i, "y": y[te], "climatology_avail": clim[te],
                    "persistence": df.loc[te, pers_col].to_numpy(), "oblast": p_obl[te],
                    "oblast_x_share": p_obl[te] * share[te], "lightgbm_state": gbm.predict(df.loc[te, KEEP + STATE_COLS]),
                }))
                log(f"state model {t} h={h} fold {i}: {tr.sum():,} train rows, {te.sum():,} test")
        del df
    P = pd.concat(preds, ignore_index=True)
    P["oblast_name"] = P["raion"].map(lambda r: BY_ID[r].oblast)
    P.to_parquet(CACHE / "latest_predictions.parquet")
    if P[METHODS].isna().any().any():
        raise RuntimeError(f"missing predictions: {P[METHODS].isna().sum().to_dict()}")
    rows, by_obl = [], []
    for (t, h), q in P.groupby(["target", "horizon"]):
        for m in METHODS:
            rows.append({"target": t, "horizon_h": h, "model": m,
                         **scores(q["y"].to_numpy(), q[m].to_numpy(), q["climatology_avail"].to_numpy())})
    for (t, h, o), q in P.groupby(["target", "horizon", "oblast_name"]):
        if (t, h) in (("active", 1), ("onset", 3)):
            y, ref = q["y"].to_numpy(), q["climatology_avail"].to_numpy()
            row = {"target": t, "horizon_h": h, "oblast": o, "name_uk": uk_name(o), "base_rate": float(y.mean())}
            for m in ("oblast", "oblast_x_share", "lightgbm_state"):
                s = scores(y, q[m].to_numpy(), ref)
                row[f"brier_{m}"], row[f"bss_{m}"] = s["brier"], s["bss_vs_clim"]
            by_obl.append(row)
    M = pd.DataFrame(rows)
    M.to_csv(REPORTS / "latest_metrics.csv", index=False)
    pd.DataFrame(by_obl).round(4).to_csv(REPORTS / "latest_by_oblast.csv", index=False)
    with open(REPORTS / "latest_metrics.md", "w") as f:
        f.write(f"Raion-level test on the latest weeks (labels: alerts.in.ua history in the live store), "
                f"origins {test_start} .. {test_end} UTC, weekly refit, {P['raion'].nunique()} units, "
                f"{len(P) // (2 * len(HORIZONS)):,} forecasts per target and horizon.\n")
        for t in TARGETS:
            for metric in ("brier", "bss_vs_clim", "roc_auc"):
                tab = M[M["target"] == t].pivot(index="model", columns="horizon_h", values=metric).loc[METHODS].round(4)
                f.write(f"\n### target = {t}, {metric}\n\n" + tab.to_markdown() + "\n")
                log(f"{t} {metric}\n{tab.to_string()}")
        f.write("\nBase rates: " + ", ".join(
            f"{t} h={h}: {q['base_rate'].iloc[0]:.3f}" for (t, h), q in M.groupby(["target", "horizon_h"])) + "\n")
    log("done")


if __name__ == "__main__":
    main()
