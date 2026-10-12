"""Score logged forecasts against what actually happened.

    python verify.py [--store live-data] [path/to/log]

Forecasts come from reports/forecast_log.csv (written by predict_now.py; same
columns as the web app's ForecastJournal, so a journal .parquet works too) or,
with --store, from the point-in-time store's `forecasts` issued in the last
--days days. Outcomes use exactly the training labels, in minutes from each
origin (src/airraid/verification.py; tests/test_verify.py):

  * dataset: the volunteer file, complete up to its last hour (label_source
    "dataset", the training definition itself);
  * store (with --store): after the dataset ends, oblast-level alerts rebuilt
    from alerts.in.ua history pulls and polls (live_alerts.oblast_intervals),
    up to the moment the store is complete (label_source "store"). This makes
    verification possible within hours instead of a day later, but these labels
    only approximate the volunteer ones; they are reported separately and are
    replaced by dataset labels once the dataset covers the window.

Writes reports/verification.csv and reports/verification_summary.json (Brier of
the model vs the 28-day climatology over the last 24 h, 7 days and everything
read, per target and horizon, hit rate); with --store also appends newly
verified rows to the store's `verification`. Forecast probabilities are never
recomputed: they are the ones logged when the forecast was issued. Verification
is monitoring only; it does not feed back into model weights.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid import store as pit  # noqa: E402
from airraid.data import download, load_alerts  # noqa: E402
from airraid.verification import label_grid, outcomes, summarise, verify  # noqa: E402,F401  (re-exported)

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "reports" / "forecast_log.csv"
OUT_PATH = ROOT / "reports" / "verification.csv"
SUMMARY_PATH = ROOT / "reports" / "verification_summary.json"


def read_forecasts(path: Path | None, store_root: Path | None, since: pd.Timestamp | None = None) -> pd.DataFrame:
    parts = []
    if store_root is not None:
        parts.append(pit.load(store_root, "forecasts", since=since))
    if path is not None and path.exists():
        parts.append(pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path))
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame()
    log = pd.concat(parts, ignore_index=True)
    for c in ("origin", "issued_at"):
        if c in log:
            log[c] = pd.to_datetime(log[c], utc=True)
    if since is not None:
        log = log[log["origin"] >= since]
    # the same forecast can be in both the CSV log and the store
    return log.drop_duplicates(["origin", "region", "target", "horizon"], keep="first").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description="Score logged forecasts against what actually happened.")
    ap.add_argument("log", nargs="?", type=Path, default=None, help=f"forecast log (default {LOG_PATH})")
    ap.add_argument("--store", type=Path, default=None, help="point-in-time store: forecasts + labels after the dataset")
    ap.add_argument("--days", type=float, default=8, help="with --store: forecasts issued in the last DAYS days")
    ap.add_argument("--no-download", action="store_true", default=os.environ.get("AIRRAID_DOWNLOAD") == "0",
                    help="use the local dataset copy (the pipeline downloads it once per run)")
    args = ap.parse_args()
    now = pd.Timestamp.now(tz="UTC")
    path = args.log or (LOG_PATH if args.store is None or LOG_PATH.exists() else None)
    since = now - pd.Timedelta(days=args.days) if args.store is not None else None
    log = read_forecasts(path, args.store, since)
    if log.empty:
        print(f"no forecasts in {path or ''} {args.store or ''}: run predict_now.py first")
        return
    if not args.no_download:
        download()
    v, summary = verify(log, load_alerts(), args.store, now)
    OUT_PATH.parent.mkdir(exist_ok=True)
    v.to_csv(OUT_PATH, index=False)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    if args.store is not None and len(v):
        new = pit.append(args.store, "verification", v.assign(verified_at=now.floor("s")))
        print(f"store: {sum(len(pd.read_parquet(f)) for f in new)} newly verified rows appended")
    print(f"verified {len(v)} forecasts ({summary['n_verified_by_label_source']}), {summary['pending']} still "
          f"pending (record until {summary['verified_until_utc']}, dataset until {summary['dataset_end_utc']})")
    for g in summary["groups"]:
        if g["window"] == "7d":
            print(f"  {g['target']:6s} h={g['horizon_h']}  n={g['n']:5d}  brier model {g['brier_model']:.4f}  "
                  f"clim {g['brier_clim']:.4f}  hit@0.5 {g['hit_rate_at_0.5']:.3f}")


if __name__ == "__main__":
    main()
