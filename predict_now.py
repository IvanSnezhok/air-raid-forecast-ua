"""Live forecast for the next 1-6 hours for every region.

    export ALERTS_IN_UA_TOKEN=...   # optional; never commit it
    python predict_now.py [--store live-data]   # forecast + forecast log
    python verify.py [--store live-data]        # score past forecasts once their horizon has passed

Models are refit (at most every --retrain-hours) on the volunteer dataset only,
on origins whose label window it fully covers, and saved to models/. Every
forecast is appended to reports/forecast_log.csv and, with --store, to the
point-in-time store (src/airraid/store.py) so verify.py can score it.

Prediction features (never training rows) can additionally use, all as known
at the forecast origin:
  * the store's alerts.in.ua history pulls and polls, to fill the hours
    between the end of the dataset (updated about once a day) and the origin;
  * a fresh alerts.in.ua poll when ALERTS_IN_UA_TOKEN is set (records that
    started before the origin; their later disappearance is not used).
Live records are turned into oblast-level alerts with the same definition and
12 h cap as the training data (airraid.live_alerts.oblast_intervals) and can
never change anything before the end of the dataset.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid import store
from airraid.data import build_grid, download, load_alerts
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels
from airraid.live_alerts import OBLAST_EN as UK_TO_EN  # noqa: F401  (old import name)
from airraid.live_alerts import (
    KEY, LIVE_EXPIRY_HOURS, fetch_active, gap_alerts, parse_active, store_records, to_frame,
)
from airraid.models import GBM

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
LOG_PATH = ROOT / "reports" / "forecast_log.csv"


def live_records(token: str, origin: pd.Timestamp) -> pd.DataFrame:
    """Records of a fresh active.json poll that started before the origin.

    They were running at the poll, so also at the origin. Records that ended
    between the origin and the poll are simply missing: their end is after the
    origin and must not be used.
    """
    df = to_frame(parse_active(fetch_active(token)))
    df = df[df["started_at"] < origin]
    return df.assign(finished_at=pd.NaT)


def git_sha() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def grids(alerts: pd.DataFrame, hist_end: pd.Timestamp, origin: pd.Timestamp, records: pd.DataFrame | None):
    """(training grid, feature grid, gap alerts).

    Training: the dataset only, up to its last full hour. Live records never
    enter it: between the end of the dataset and the origin they only describe
    part of what happened (alerts that started and ended between polls are
    missing), so labels there would be wrong.
    Features: the dataset plus oblast-level alerts rebuilt from the live records
    known at `origin`, only after the end of the dataset (live_alerts.gap_alerts).
    """
    tgrid = build_grid(alerts, data_end=hist_end.floor("h"))
    gap = gap_alerts(records, origin, alerts)
    fgrid = build_grid(pd.concat([alerts, gap], ignore_index=True) if len(gap) else alerts, data_end=origin)
    assert fgrid.regions == tgrid.regions and fgrid.t0 == tgrid.t0
    return tgrid, fgrid, gap


def train_models(grid, H, S, base_all, K_all, hist_end, selection, threads: int = 4):
    """Fit one LightGBM per (target, horizon) on the training grid (dataset only).

    Only origins whose label window lies inside the dataset are used.
    """
    last_hour = grid.n_minutes // 60
    models = {}
    for h in HORIZONS:
        df = add_horizon_features(base_all, grid, H, h, S)
        for t in ("active", "onset"):
            sel = selection.get(f"{t}_h{h}", {"best_months": 12, "best_trees": 300})
            y = labels(grid, K_all, h, H, t, S)
            k = df["origin_hour"].to_numpy()
            m = ~np.isnan(y) & (k + h <= last_hour)
            if sel["best_months"] is not None:
                m &= k >= grid.minute(hist_end - pd.DateOffset(months=sel["best_months"])) // 60
            models[f"{t}_h{h}"] = GBM(n_estimators=sel["best_trees"], params={"num_threads": threads}).fit(df[m], y[m])
    return models


def save_models(models, trained_until):
    MODELS_DIR.mkdir(exist_ok=True)
    meta = {"trained_until_utc": str(trained_until), "models": {}}
    for key, m in models.items():
        m.model.save_model(str(MODELS_DIR / f"{key}.txt"), num_iteration=m.n_estimators)
        meta["models"][key] = {"regions": m.regions, "trees": m.n_estimators}
    (MODELS_DIR / "meta.json").write_text(json.dumps(meta, indent=2))


def load_models(origin, max_age_h):
    """Reuse saved models if they are fresh and were trained on data before `origin`."""
    path = MODELS_DIR / "meta.json"
    if not path.exists():
        return None, None
    meta = json.loads(path.read_text())
    trained_until = pd.Timestamp(meta["trained_until_utc"])
    if not (trained_until <= origin and origin - trained_until <= pd.Timedelta(hours=max_age_h)):
        return None, None
    models = {}
    for key, info in meta["models"].items():
        m = GBM(n_estimators=info["trees"])
        m.regions = info["regions"]
        m.model = lgb.Booster(model_file=str(MODELS_DIR / f"{key}.txt"))
        models[key] = m
    return models, trained_until


def append_log(out_long: pd.DataFrame):
    """Append this run's forecasts to the forecast log (one row per region/target/horizon)."""
    if LOG_PATH.exists():
        old = pd.read_csv(LOG_PATH, parse_dates=["origin", "issued_at", "trained_until"])
        out_long = pd.concat([old, out_long], ignore_index=True)
    key = ["origin", "region", "target", "horizon"]
    out_long = out_long.drop_duplicates(key, keep="last").sort_values(key)
    out_long.to_csv(LOG_PATH, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrain-hours", type=float, default=24,
                    help="retrain when saved models are older than this (they never see data after the origin)")
    ap.add_argument("--as-of", help="replay: forecast as if it were this UTC hour (data after it is "
                                    "dropped, store rows are used as known then, models are not cached); "
                                    "e.g. 2026-10-10T12:00")
    ap.add_argument("--store", type=Path, default=os.environ.get("AIRRAID_STORE") or None,
                    help="point-in-time store (live-data branch checkout): fills the gap after the dataset "
                         "and receives the forecasts")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("AIRRAID_LGB_THREADS", 4)))
    args = ap.parse_args()

    download()
    alerts = load_alerts()
    hist_end = alerts["start"].max()
    now = pd.Timestamp.now(tz="UTC")
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    if args.as_of:
        now = pd.Timestamp(args.as_of, tz="UTC").floor("h")
        alerts = alerts[alerts["start"] < now]  # everything after the origin is unknown
        # Before the dataset end the file is taken as complete up to the origin; after it,
        # the gap is filled from the store as it was known at the origin.
        hist_end, token = min(now, hist_end), None

    # Origin: now when something live is known (a token, or store polls up to now),
    # else the last hour of the dataset.
    origin = now.floor("h")
    records, covered, last_poll = None, None, None
    if args.store is not None:
        records, covered = store_records(args.store, origin, hist_end)
        polls = store.load(args.store, "polls", as_of=origin, since=origin - pd.Timedelta(days=1))
        last_poll = polls["observed_at"].max() if len(polls) else None
    if token:
        fresh = live_records(token, origin)
        records = fresh if records is None else (
            pd.concat([records, fresh], ignore_index=True)  # a known end beats "still running"
            .sort_values("finished_at", na_position="last").drop_duplicates(KEY))
        print(f"alerts.in.ua: {len(fresh)} active records that started before {origin:%H:%M} UTC")
    elif args.as_of:
        print(f"replay as of {origin}")
    elif last_poll is None or last_poll < origin - pd.Timedelta(hours=LIVE_EXPIRY_HOURS):
        origin = hist_end.floor("h")
        records = covered = None
        print("No ALERTS_IN_UA_TOKEN and no store poll in the last hour: forecasting as of the end of the dataset")
    known_until = max(hist_end, covered) if covered is not None else hist_end
    if origin - known_until > pd.Timedelta(hours=6):
        print(f"WARNING: alert record complete only up to {known_until}, {origin - known_until} before the "
              "forecast origin; recent-history features will be understated")

    # Training: the dataset only. Features: dataset + live records known at the origin.
    tgrid, grid, gap = grids(alerts, hist_end, origin, records)
    if len(gap):
        print(f"gap after the dataset: {len(gap)} oblast-level alerts rebuilt from live records")
    H, S = hourly_fraction(grid), hourly_starts(grid)
    k_now = grid.n_minutes // 60
    base_now = build_features(grid, np.array([k_now]), H)

    models, trained_until = (None, None) if args.as_of else load_models(origin, args.retrain_hours)
    if models is None:
        sel_path = ROOT / "reports" / "selection.json"
        selection = json.loads(sel_path.read_text()) if sel_path.exists() else {}
        Ht, St = hourly_fraction(tgrid), hourly_starts(tgrid)
        K_all = np.arange(28 * 24, tgrid.n_minutes // 60)
        base_all = build_features(tgrid, K_all, Ht)
        models = train_models(tgrid, Ht, St, base_all, K_all, hist_end, selection, args.threads)
        trained_until = tgrid.t0 + pd.Timedelta(minutes=tgrid.n_minutes)  # last moment of data used
        del base_all
        if not args.as_of:
            save_models(models, trained_until)
        print(f"retrained {len(models)} models on data up to {trained_until}")
    else:
        print(f"reusing models trained on data up to {trained_until}")

    issued_at = pd.Timestamp.now(tz="UTC").floor("s")
    sha = git_sha()
    out = pd.DataFrame({"region": grid.regions})
    rows = []
    for h in HORIZONS:
        x_now = add_horizon_features(base_now, grid, H, h, S)
        for t, clim_col in (("active", "hod_clim_28d"), ("onset", "onset_clim_28d")):
            p = models[f"{t}_h{h}"].predict(x_now)
            out[f"{t}_{h}h"] = p.round(3)
            # same columns as the web app's ForecastJournal (src/airraid_app/journal.py)
            rows.append(pd.DataFrame({
                "origin": origin, "region": grid.regions, "target": t, "horizon": h,
                "p": p.round(4), "clim": x_now[clim_col].to_numpy().round(4),
                "issued_at": issued_at, "live": bool(token) or len(gap) > 0,
                "active_now": x_now["active_now"].to_numpy().astype(int),
                "trained_until": trained_until, "hist_end": hist_end, "git_sha": sha,
            }))
    log = pd.concat(rows, ignore_index=True)
    append_log(log)
    if args.store is not None and not args.as_of:
        store.append(args.store, "forecasts", log)

    local = origin.tz_convert("Europe/Kyiv")
    print(f"\nForecast made at {local:%Y-%m-%d %H:%M} Kyiv time")
    print("active_Nh: P(alert active during hour N); onset_Nh: P(new alert starts within N hours)\n")
    print(out.sort_values("active_1h", ascending=False).to_string(index=False))
    out.to_csv(ROOT / "reports" / "forecast_now.csv", index=False)
    (ROOT / "reports" / "forecast_meta.json").write_text(json.dumps({
        "origin_kyiv": f"{local:%Y-%m-%d %H:%M}",
        "origin_utc": f"{origin:%Y-%m-%d %H:%M}",
        "history_ends_utc": f"{hist_end:%Y-%m-%d %H:%M}",
        "record_complete_until_utc": f"{known_until:%Y-%m-%d %H:%M}",
        "gap_alerts": int(len(gap)),
        "models_trained_until_utc": f"{trained_until:%Y-%m-%d %H:%M}",
        "live_api": bool(token),
    }))


if __name__ == "__main__":
    main()
