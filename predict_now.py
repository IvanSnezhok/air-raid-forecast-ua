"""Live forecast for the next 1-6 hours for every region.

    export ALERTS_IN_UA_TOKEN=...   # optional; never commit it
    python predict_now.py

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

import json
import os
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import build_grid, download, load_alerts
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels
from airraid.models import GBM

ROOT = Path(__file__).resolve().parent
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


def main():
    download()
    alerts = load_alerts()
    hist_end = alerts["start"].max()
    now = pd.Timestamp.now(tz="UTC").floor("h")
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    if token:
        live = active_from_api(token)
        live["end"] = now + pd.Timedelta(hours=1)  # still running: never used as a feature
        live["naive"] = False
        alerts = pd.concat([alerts, live], ignore_index=True)
        origin = now
        print(f"alerts.in.ua: {len(live)} regions under air raid alert right now")
    else:
        origin = alerts["start"].max().floor("h")
        print("No ALERTS_IN_UA_TOKEN: forecasting as of the end of the dataset")
    if origin - hist_end > pd.Timedelta(hours=6):
        print(f"WARNING: history ends {hist_end}, {origin - hist_end} before the forecast origin; "
              "recent-history features will be understated")

    grid = build_grid(alerts, data_end=origin)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    K_all = pd.RangeIndex(28 * 24, grid.n_minutes // 60).to_numpy()
    summary_path = ROOT / "reports" / "run_summary.json"
    selection = json.loads(summary_path.read_text())["selection"] if summary_path.exists() else {}

    base_all = build_features(grid, K_all, H)
    k_now = grid.n_minutes // 60
    base_now = build_features(grid, pd.Index([k_now]).to_numpy(), H)
    out = pd.DataFrame({"region": grid.regions})
    for h in HORIZONS:
        df = add_horizon_features(base_all, grid, H, h, S)
        x_now = add_horizon_features(base_now, grid, H, h, S)
        for t in ("active", "onset"):
            sel = selection.get(f"{t}_h{h}", {"best_months": 12, "best_trees": 300})
            y = labels(grid, K_all, h, H, t, S)
            m = ~pd.isna(y)
            lo = 0 if sel["best_months"] is None else (grid.minute(origin - pd.DateOffset(months=sel["best_months"])) // 60)
            m &= df["origin_hour"].to_numpy() >= lo
            model = GBM(n_estimators=sel["best_trees"]).fit(df[m], y[m])
            out[f"{t}_{h}h"] = model.predict(x_now).round(3)
    local = origin.tz_convert("Europe/Kyiv")
    print(f"\nForecast made at {local:%Y-%m-%d %H:%M} Kyiv time")
    print("active_Nh: P(alert active during hour N); onset_Nh: P(new alert starts within N hours)\n")
    print(out.sort_values("active_1h", ascending=False).to_string(index=False))
    out.to_csv(ROOT / "reports" / "forecast_now.csv", index=False)
    (ROOT / "reports" / "forecast_meta.json").write_text(json.dumps({
        "origin_kyiv": f"{local:%Y-%m-%d %H:%M}",
        "origin_utc": f"{origin:%Y-%m-%d %H:%M}",
        "history_ends_utc": f"{hist_end:%Y-%m-%d %H:%M}",
        "live_api": bool(token),
    }))


if __name__ == "__main__":
    main()
