"""Live forecast for the next 1-6 hours for every region.

    export ALERTS_IN_UA_TOKEN=...   # optional; never commit it
    python predict_now.py           # forecast + append to reports/forecast_log.csv
    python verify.py                # score past forecasts once their horizon has passed

Learning loop: models are retrained (at most every --retrain-hours) on all
history available at the forecast origin and saved to models/; every forecast
is logged so verify.py can score it against what actually happened.

History comes from the volunteer dataset (updated about once a day). If an
alerts.in.ua token is set, currently active air raid alerts are added on top so
the "what is happening right now" features are fresh. Without a token the
forecast is made as of the last hour covered by the dataset.

Assumption (not verified against the API docs offline): /v1/alerts/active.json
returns {"alerts": [{"alert_type", "location_oblast", "started_at", ...}]}. Any
active air_raid alert anywhere in an oblast (oblast, raion or hromada level)
marks that oblast as under alert, which matches how the volunteer data is
recorded at oblast level.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import build_grid, download, load_alerts
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels
from airraid.models import GBM

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
LOG_PATH = ROOT / "reports" / "forecast_log.csv"
API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
UK_TO_EN = {
    "Черкаська область": "Cherkaska oblast", "Чернігівська область": "Chernihivska oblast",
    "Чернівецька область": "Chernivetska oblast", "Дніпропетровська область": "Dnipropetrovska oblast",
    "Донецька область": "Donetska oblast", "Івано-Франківська область": "Ivano-Frankivska oblast",
    "Харківська область": "Kharkivska oblast", "Херсонська область": "Khersonska oblast",
    "Хмельницька область": "Khmelnytska oblast", "Кіровоградська область": "Kirovohradska oblast",
    "м. Київ": "Kyiv City", "Київ": "Kyiv City", "Київська область": "Kyivska oblast",
    "Львівська область": "Lvivska oblast", "Миколаївська область": "Mykolaivska oblast",
    "Одеська область": "Odeska oblast", "Полтавська область": "Poltavska oblast",
    "Рівненська область": "Rivnenska oblast", "Сумська область": "Sumska oblast",
    "Тернопільська область": "Ternopilska oblast", "Вінницька область": "Vinnytska oblast",
    "Волинська область": "Volynska oblast", "Закарпатська область": "Zakarpatska oblast",
    "Запорізька область": "Zaporizka oblast", "Житомирська область": "Zhytomyrska oblast",
}


def active_from_api(token: str) -> pd.DataFrame:
    req = urllib.request.Request(API_URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.load(r)
    rows = []
    for a in payload.get("alerts", []):
        if a.get("alert_type") != "air_raid":
            continue
        region = UK_TO_EN.get(a.get("location_oblast") or a.get("location_title"))
        if region:
            rows.append({"region": region, "start": pd.Timestamp(a["started_at"]).tz_convert("UTC")})
    df = pd.DataFrame(rows, columns=["region", "start"])
    # one ongoing record per region: the earliest start of its active alerts
    return df.groupby("region", as_index=False)["start"].min()


def train_models(grid, H, S, base_all, K_all, hist_end, selection):
    """Fit one LightGBM per (target, horizon) on data known at the forecast origin.

    Training labels must be fully observed in the *history* file: in live mode
    the hours between the end of the history and now contain only the alerts
    that are still running (from the API), so labels there would be wrong
    (mostly false zeros). Those origins are dropped.
    """
    last_hour = grid.minute(hist_end.floor("h")) // 60
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
            models[f"{t}_h{h}"] = GBM(n_estimators=sel["best_trees"]).fit(df[m], y[m])
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
                                    "dropped, models are not cached); e.g. 2026-10-10T12:00")
    args = ap.parse_args()

    download()
    alerts = load_alerts()
    hist_end = alerts["start"].max()
    now = pd.Timestamp.now(tz="UTC").floor("h")
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    if args.as_of:
        now = pd.Timestamp(args.as_of, tz="UTC").floor("h")
        alerts = alerts[alerts["start"] < now]  # everything after the origin is unknown
        hist_end, token = now, None
    if token:
        live = active_from_api(token)
        live["end"] = now + pd.Timedelta(hours=1)  # still running: never used as a feature
        live["naive"] = False
        alerts = pd.concat([alerts, live], ignore_index=True)
        origin = now
        print(f"alerts.in.ua: {len(live)} regions under air raid alert right now")
    elif args.as_of:
        origin = now
        print(f"replay as of {origin}")
    else:
        origin = hist_end.floor("h")
        print("No ALERTS_IN_UA_TOKEN: forecasting as of the end of the dataset")
    if origin - hist_end > pd.Timedelta(hours=6):
        print(f"WARNING: history ends {hist_end}, {origin - hist_end} before the forecast origin; "
              "recent-history features will be understated")

    grid = build_grid(alerts, data_end=origin)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    k_now = grid.n_minutes // 60
    base_now = build_features(grid, np.array([k_now]), H)

    models, trained_until = (None, None) if args.as_of else load_models(origin, args.retrain_hours)
    if models is None:
        sel_path = ROOT / "reports" / "selection.json"
        selection = json.loads(sel_path.read_text()) if sel_path.exists() else {}
        K_all = np.arange(28 * 24, k_now)
        base_all = build_features(grid, K_all, H)
        models = train_models(grid, H, S, base_all, K_all, hist_end, selection)
        trained_until = hist_end
        if not args.as_of:
            save_models(models, trained_until)
        print(f"retrained {len(models)} models on data up to {trained_until}")
    else:
        print(f"reusing models trained on data up to {trained_until}")

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
                "issued_at": pd.Timestamp.now(tz="UTC").floor("s"), "live": bool(token),
                "active_now": x_now["active_now"].to_numpy().astype(int),
                "trained_until": trained_until,
            }))
    append_log(pd.concat(rows, ignore_index=True))

    local = origin.tz_convert("Europe/Kyiv")
    print(f"\nForecast made at {local:%Y-%m-%d %H:%M} Kyiv time")
    print("active_Nh: P(alert active during hour N); onset_Nh: P(new alert starts within N hours)\n")
    print(out.sort_values("active_1h", ascending=False).to_string(index=False))
    out.to_csv(ROOT / "reports" / "forecast_now.csv", index=False)
    (ROOT / "reports" / "forecast_meta.json").write_text(json.dumps({
        "origin_kyiv": f"{local:%Y-%m-%d %H:%M}",
        "origin_utc": f"{origin:%Y-%m-%d %H:%M}",
        "history_ends_utc": f"{hist_end:%Y-%m-%d %H:%M}",
        "models_trained_until_utc": f"{trained_until:%Y-%m-%d %H:%M}",
        "live_api": bool(token),
    }))


if __name__ == "__main__":
    main()
