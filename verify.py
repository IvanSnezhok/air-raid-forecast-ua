"""Score logged forecasts against what actually happened.

    python verify.py

Reads reports/forecast_log.csv (written by predict_now.py; same columns as the web
app's ForecastJournal, so `python verify.py path/to/journal.parquet` also works), refreshes the alert
history and, for every forecast whose target window is fully covered by the
history, records the outcome. Writes:

  reports/verification.csv           one row per verified forecast (+ outcome)
  reports/verification_summary.json  Brier of the model vs the 28-day climatology
                                     over the last 24 h, 7 days and all time,
                                     per target and horizon

Forecasts are never re-scored with information they did not have: the model
probability was fixed when it was logged; only the outcome is looked up now.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import build_grid, download, load_alerts

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "reports" / "forecast_log.csv"
OUT_PATH = ROOT / "reports" / "verification.csv"
SUMMARY_PATH = ROOT / "reports" / "verification_summary.json"


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


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else LOG_PATH
    if not path.exists():
        print(f"no forecast log at {path}: run predict_now.py first")
        return
    download()
    alerts = load_alerts()
    grid = build_grid(alerts)  # complete up to the last hour in the history file
    log = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    log["y"] = outcomes(log, grid)
    v = log.dropna(subset=["y"]).reset_index(drop=True)
    v["hit_at_0.5"] = ((v["p"] >= 0.5) == (v["y"] == 1)).astype(int)
    v["brier_model"] = (v["p"] - v["y"]) ** 2
    v["brier_clim"] = (v["clim"] - v["y"]) ** 2
    v.to_csv(OUT_PATH, index=False)
    summary = summarise(v, grid.data_end)
    summary["pending"] = int(log["y"].isna().sum())
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    print(f"verified {len(v)} forecasts, {summary['pending']} still pending (history ends {grid.data_end})")
    for g in summary["groups"]:
        if g["window"] == "7d":
            print(f"  {g['target']:6s} h={g['horizon_h']}  n={g['n']:5d}  brier model {g['brier_model']:.4f}  "
                  f"clim {g['brier_clim']:.4f}  hit@0.5 {g['hit_rate_at_0.5']:.3f}")


if __name__ == "__main__":
    main()
