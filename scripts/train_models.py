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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("AIRRAID_MODEL_DIR") or ROOT / "var" / "models"))
    ap.add_argument("--threads", type=int, default=int(os.environ.get("AIRRAID_LGB_THREADS", 4)))
    ap.add_argument("--holdout-days", type=int, default=7)
    ap.add_argument("--no-download", action="store_true", default=os.environ.get("AIRRAID_DOWNLOAD") == "0")
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
    lgb = [r for r in meta["holdout_scores"] if r["model"] == "lightgbm"]
    print(json.dumps({k: meta[k] for k in ("data_end", "trained_at", "code_version", "train_seconds", "plans")}, indent=1))
    for r in lgb:
        print(f"  holdout {r['target']:6s} h={r['horizon']}  Brier {r['brier']:.4f}  skill vs clim {r['bss_vs_clim']:+.3f}")
    print(f"saved to {args.out} in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
