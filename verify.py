"""Score logged forecasts against what actually happened.

    python verify.py [--store live-data] [path/to/log]

Forecasts come from reports/forecast_log.csv (written by predict_now.py; same
columns as the web app's ForecastJournal, so a journal .parquet works too) or,
with --store, from the point-in-time store's `forecasts`. For every forecast
whose target window is covered by the alert record, the outcome is looked up
with exactly the training labels (features.labels; tests/test_verify.py):

  * dataset: the volunteer file, complete up to its last hour (label_source
    "dataset", the training definition itself);
  * store (with --store): after the dataset ends, oblast-level alerts rebuilt
    from alerts.in.ua history pulls and polls (live_alerts.oblast_intervals),
    up to the moment the store is complete (label_source "store"). This makes
    verification possible within hours instead of a day later, but these labels
    only approximate the volunteer ones; they are reported separately and are
    replaced by dataset labels once the dataset covers the window.

Writes reports/verification.csv and reports/verification_summary.json (Brier of
the model vs the 28-day climatology over the last 24 h, 7 days and all time,
per target and horizon); with --store also appends newly verified rows to the
store's `verification`. Forecast probabilities are never recomputed: they are
the ones logged when the forecast was issued. Verification is monitoring only;
it does not feed back into model weights (models are refit periodically on the
dataset by predict_now.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid import store as pit
from airraid.data import build_grid, download, load_alerts
from airraid.live_alerts import gap_alerts, store_records

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "reports" / "forecast_log.csv"
OUT_PATH = ROOT / "reports" / "verification.csv"
SUMMARY_PATH = ROOT / "reports" / "verification_summary.json"


def label_grid(alerts: pd.DataFrame, store_root: Path | None, now: pd.Timestamp):
    """(grid, dataset_end): the alert record labels are read from.

    The dataset up to its last full hour; with a store, extended by oblast-level
    alerts rebuilt from the store's records up to where the store is complete.
    """
    hist_end = alerts["start"].max()
    dataset_end = hist_end.floor("h")
    if store_root is not None:
        rec, covered = store_records(store_root, now, hist_end)
        if covered is not None and covered.floor("h") > dataset_end:
            gap = gap_alerts(rec, covered, alerts)
            return build_grid(pd.concat([alerts, gap], ignore_index=True), data_end=covered.floor("h")), dataset_end
    return build_grid(alerts, data_end=dataset_end), dataset_end


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
        if i is None or end > grid.n_minutes:
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
    out = {"verified_until_utc": str(now), "n_verified": int(len(v)), "groups": []}
    origin = pd.to_datetime(v["origin"], utc=True)
    for (t, h), q in v.groupby(["target", "horizon"]):
        o = origin[q.index]
        for label, lo in (("24h", now - pd.Timedelta(hours=24)), ("7d", now - pd.Timedelta(days=7)), ("all", None)):
            w = q if lo is None else q[o >= lo]
            if w.empty:
                continue
            y, p, c = w["y"].to_numpy(), w["p"].to_numpy(), w["clim"].to_numpy()
            bm, bc = _brier(y, p), _brier(y, c)
            hit = (p >= 0.5) == (y == 1)
            out["groups"].append({
                "target": t, "horizon_h": int(h), "window": label, "n": int(len(w)),
                "base_rate": float(y.mean()), "brier_model": bm, "brier_clim": bc,
                "skill_vs_clim": None if not bc else 1 - bm / bc,
                "hit_rate_at_0.5": float(hit.mean()),
                "recall_at_0.5": float(((p >= 0.5) & (y == 1)).sum() / max(1, (y == 1).sum())),
                "false_alarm_ratio_at_0.5": float(((p >= 0.5) & (y == 0)).sum() / max(1, (p >= 0.5).sum())),
            })
    return out


def verify(log: pd.DataFrame, alerts: pd.DataFrame, store_root: Path | None = None,
           now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
    """Verified rows (with y, label_source) and the summary."""
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    grid, dataset_end = label_grid(alerts, store_root, now)
    log = log.copy()
    log["y"] = outcomes(log, grid)
    window_end = pd.to_datetime(log["origin"], utc=True) + pd.to_timedelta(log["horizon"], unit="h")
    log["label_source"] = np.where(window_end <= dataset_end, "dataset", "store")
    v = log.dropna(subset=["y"]).reset_index(drop=True)
    v["hit_at_0.5"] = ((v["p"] >= 0.5) == (v["y"] == 1)).astype(int)
    v["brier_model"] = (v["p"] - v["y"]) ** 2
    v["brier_clim"] = (v["clim"] - v["y"]) ** 2
    record_end = grid.t0 + pd.Timedelta(minutes=grid.n_minutes)
    summary = summarise(v, record_end)
    summary["pending"] = int(log["y"].isna().sum())
    summary["dataset_end_utc"] = str(dataset_end)
    summary["n_verified_by_label_source"] = v["label_source"].value_counts().to_dict()
    return v, summary


def read_forecasts(path: Path | None, store_root: Path | None) -> pd.DataFrame:
    parts = []
    if store_root is not None:
        parts.append(pit.load(store_root, "forecasts"))
    if path is not None and path.exists():
        parts.append(pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path))
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame()
    log = pd.concat(parts, ignore_index=True)
    for c in ("origin", "issued_at"):
        if c in log:
            log[c] = pd.to_datetime(log[c], utc=True)
    # the same forecast can be in both the CSV log and the store
    return log.drop_duplicates(["origin", "region", "target", "horizon"], keep="first").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description="Score logged forecasts against what actually happened.")
    ap.add_argument("log", nargs="?", type=Path, default=None, help=f"forecast log (default {LOG_PATH})")
    ap.add_argument("--store", type=Path, default=None, help="point-in-time store: forecasts + labels after the dataset")
    args = ap.parse_args()
    path = args.log or (LOG_PATH if args.store is None or LOG_PATH.exists() else None)
    log = read_forecasts(path, args.store)
    if log.empty:
        print(f"no forecasts in {path or ''} {args.store or ''}: run predict_now.py first")
        return
    download()
    now = pd.Timestamp.now(tz="UTC")
    v, summary = verify(log, load_alerts(), args.store, now)
    OUT_PATH.parent.mkdir(exist_ok=True)
    v.to_csv(OUT_PATH, index=False)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    if args.store is not None and len(v):
        pit.append(args.store, "verification", v.assign(verified_at=now.floor("s")))
    print(f"verified {len(v)} forecasts ({summary['n_verified_by_label_source']}), {summary['pending']} still "
          f"pending (record until {summary['verified_until_utc']}, dataset until {summary['dataset_end_utc']})")
    for g in summary["groups"]:
        if g["window"] == "7d":
            print(f"  {g['target']:6s} h={g['horizon_h']}  n={g['n']:5d}  brier model {g['brier_model']:.4f}  "
                  f"clim {g['brier_clim']:.4f}  hit@0.5 {g['hit_rate_at_0.5']:.3f}")


if __name__ == "__main__":
    main()
