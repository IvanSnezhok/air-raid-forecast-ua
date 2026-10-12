"""Train the served model set and save it (the train job, .github/workflows/train.yml).

    python scripts/train_models.py --out var/models [--threads 4] [--no-download]

Downloads the volunteer dataset, trains one LightGBM per (target, horizon) with
the holdout evaluation (airraid.training) and saves the bundle the pipeline, the
static site and the web app load. Prints what was trained.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airraid import training  # noqa: E402
from airraid.data import download, load_alerts  # noqa: E402


def train_raions(alerts, out: Path, threads: int) -> None:
    """Train the raion models here, out of the 10-minute run, and save them to <models>/raions,
    which is cached and restored with the oblast bundle; the pipeline's site build only
    loads them (RaionForecaster.load_saved, AIRRAID_RAION_TRAIN=0).

    They learn from the official raion file and the store's raion history
    (AIRRAID_OFFICIAL_PATH, AIRRAID_STORE), never from the forecast moment's live data.
    """
    import pandas as pd

    from airraid_app.config import Settings
    from airraid_app.raion_engine import RaionForecaster

    t = time.time()
    rf = RaionForecaster(Settings.from_env())
    rf.load(True)
    rf.prepare(alerts, pd.Timestamp.now(tz="UTC"), threads)
    rf.save(out)
    kinds = sorted({k for k, _, _ in rf.models})
    print(f"raion models {kinds or 'none (not enough covered raion history)'}, shares for "
          f"{len(rf.shares)} target/horizons, saved to {out} in {time.time() - t:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("AIRRAID_MODEL_DIR") or ROOT / "var" / "models"))
    ap.add_argument("--threads", type=int, default=int(os.environ.get("AIRRAID_LGB_THREADS", 4)))
    ap.add_argument("--holdout-days", type=int, default=7)
    ap.add_argument("--no-download", action="store_true", default=os.environ.get("AIRRAID_DOWNLOAD") == "0")
    ap.add_argument("--raions", action="store_true", help="also train the raion models into OUT/raions")
    args = ap.parse_args()
    t = time.time()
    if not args.no_download:
        download()
    alerts = load_alerts()
    plan, source = training.plans(ROOT / "reports")
    models, meta, holdout = training.train(
        alerts, plan, args.holdout_days, args.threads, selection_source=source,
        progress=lambda n, total: print(f"  {n}/{total} models ({time.time() - t:.0f}s)", flush=True))
    training.save(args.out, models, meta, holdout)
    if args.raions:
        try:  # the oblast bundle is published even when the raion layer fails
            train_raions(alerts, args.out / "raions", args.threads)
        except Exception as e:  # noqa: BLE001
            print(f"::warning::raion models not trained: {type(e).__name__}: {e}")
    lgb = [r for r in meta["holdout_scores"] if r["model"] == "lightgbm"]
    print(json.dumps({k: meta[k] for k in ("data_end", "trained_at", "code_version", "train_seconds", "plans")}, indent=1))
    for r in lgb:
        print(f"  holdout {r['target']:6s} h={r['horizon']}  Brier {r['brier']:.4f}  skill vs clim {r['bss_vs_clim']:+.3f}")
    print(f"saved to {args.out} in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
