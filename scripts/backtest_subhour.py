"""Do models trained at top-of-hour origins stay calibrated at :10 ... :50 origins?

    python scripts/backtest_subhour.py [--days 28] [--minutes 0,20,40] [--threads 2]

The served models are trained only at origins on the hour; the 10-minute
pipeline forecasts from any minute (src/airraid/nowcast.py), with the hour
blocks aligned to end at the origin. This script trains the served model set
(airraid.training, same plans) on the dataset before the last `--days` days,
then scores every origin of those days at each minute offset: Brier, Brier
skill vs the 28-day climatology, ROC AUC, mean forecast vs event rate and the
expected calibration error (10 equal-width bins). Nothing after an origin is
used for its features (tests/test_leakage.py, sub-hour tests); the models never
see the test days.

Writes reports/subhour_backtest.json and reports/subhour_backtest.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airraid import training  # noqa: E402
from airraid.data import build_grid, load_alerts  # noqa: E402
from airraid.evaluate import scores  # noqa: E402
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels, origin_hours  # noqa: E402,E501


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean()
                     for b in range(bins) if (idx == b).any()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--minutes", default="0,20,40")
    ap.add_argument("--threads", type=int, default=2)
    args = ap.parse_args()
    minutes = [int(m) for m in args.minutes.split(",")]

    alerts = load_alerts()
    data_end = alerts["start"].max().floor("h")
    test_start = data_end - pd.Timedelta(days=args.days)
    plan, source = training.plans(ROOT / "reports")
    t = time.time()
    models, meta, _ = training.train(alerts[alerts["start"] < test_start], plan, threads=args.threads,
                                     holdout=False, selection_source=source)
    print(f"trained on data up to {meta['data_end']} in {time.time() - t:.0f}s", flush=True)

    rows = []
    for minute in minutes:
        end = data_end if minute == 0 else data_end - pd.Timedelta(minutes=60 - minute)
        grid = build_grid(alerts, data_end=end)
        H, S = hourly_fraction(grid), hourly_starts(grid)
        K = origin_hours(grid, test_start, end)
        base = build_features(grid, K, H)
        for h in HORIZONS:
            x = add_horizon_features(base, grid, H, h, S)
            for tg in training.TARGETS:
                y = labels(grid, K, h, H, tg, S)
                m = ~np.isnan(y)
                p = models[(tg, h)].predict(x[m])
                clim = x.loc[m, training.CLIM_COL[tg]].to_numpy()
                s = scores(y[m], p, clim)
                rows.append({"minute": minute, "target": tg, "horizon": h, "n": int(m.sum()),
                             "brier": s["brier"], "bss_vs_clim": s["bss_vs_clim"], "roc_auc": s["roc_auc"],
                             "mean_p": float(p.mean()), "event_rate": float(y[m].mean()), "ece": ece(y[m], p)})
        print(f"minute :{minute:02d} done ({len(K)} origins)", flush=True)
    df = pd.DataFrame(rows)
    out = {"test": [str(test_start), str(data_end)], "trained_until": meta["data_end"], "plans": meta["plans"],
           "rows": df.round(5).to_dict("records")}
    (ROOT / "reports" / "subhour_backtest.json").write_text(json.dumps(out, indent=1))
    piv = df.pivot_table(index=["target", "horizon"], columns="minute", values=["brier", "bss_vs_clim", "ece"])
    lines = [f"# Sub-hour origins: models trained at :00, scored at :{'/:'.join(f'{m:02d}' for m in minutes)}",
             "", f"Test: {test_start:%Y-%m-%d %H:%M} - {data_end:%Y-%m-%d %H:%M} UTC (every origin at each offset); "
             f"models trained on data up to {meta['data_end']}.", ""]
    head = "| target | h | " + " | ".join(f"Brier :{m:02d}" for m in minutes) + " | " + \
        " | ".join(f"BSS :{m:02d}" for m in minutes) + " | " + " | ".join(f"ECE :{m:02d}" for m in minutes) + " |"
    lines += [head, "|" + "---|" * (2 + 3 * len(minutes))]
    for (tg, h), r in piv.iterrows():
        lines.append(f"| {tg} | {h} | " + " | ".join(f"{r[('brier', m)]:.4f}" for m in minutes) + " | "
                     + " | ".join(f"{r[('bss_vs_clim', m)]:+.3f}" for m in minutes) + " | "
                     + " | ".join(f"{r[('ece', m)]:.4f}" for m in minutes) + " |")
    (ROOT / "reports" / "subhour_backtest.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
