"""Fixtures for the web app: a small synthetic alert history, so tests run offline in seconds."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from airraid.raions import forecastable, oblast_raions
from airraid_app.api import create_app
from airraid_app.config import Settings
from airraid_app.engine import ForecastEngine

REGIONS = ["Kyiv City", "Kharkivska oblast", "Lvivska oblast", "Odeska oblast"]


def synthetic_alerts(days: int = 75, seed: int = 0) -> pd.DataFrame:
    """Alerts with a night-time peak, more frequent in the east, and Kyiv following Kharkiv."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-07-01", tz="UTC")
    rate = {"Kyiv City": 0.08, "Kharkivska oblast": 0.15, "Lvivska oblast": 0.03, "Odeska oblast": 0.07}
    rows = []
    for hour in range(days * 24):
        t = start + pd.Timedelta(hours=hour)
        night = 2.0 if t.hour in (22, 23, 0, 1, 2, 3) else 1.0
        kharkiv = False
        for region in REGIONS:
            p = rate[region] * night + (0.3 if region == "Kyiv City" and kharkiv else 0)
            if rng.random() < p:
                s = t + pd.Timedelta(minutes=int(rng.integers(0, 60)))
                rows.append((region, s, s + pd.Timedelta(minutes=int(rng.integers(10, 150))), False))
                kharkiv |= region == "Kharkivska oblast"
    return pd.DataFrame(rows, columns=["region", "started_at", "finished_at", "naive"])


def synthetic_official(alerts: pd.DataFrame, end: pd.Timestamp | None = None, seed: int = 1) -> pd.DataFrame:
    """Official-file-like raion records: each oblast alert lights up a random subset of its raions.

    The first raion of each oblast is hit far more often than the last, a few
    records are hromada-level, and some raion alerts have no oblast alert at all
    (the two real files disagree too). `end` cuts the file like the real one,
    which stopped updating.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for a in alerts.itertuples(index=False):
        if end is not None and a.started_at >= end:
            continue
        if a.region == "Kyiv City":
            rows.append(("Kyiv City", None, None, "oblast", a.started_at, a.finished_at))
            continue
        raions = [r for r in oblast_raions(a.region) if forecastable(r)]
        weights = np.linspace(0.9, 0.15, len(raions))
        for r, w in zip(raions, weights):
            if rng.random() < w:
                s = a.started_at + pd.Timedelta(minutes=int(rng.integers(0, 4)))
                level = "hromada" if rng.random() < 0.1 else "raion"
                rows.append((a.region, r.name, "Test hromada" if level == "hromada" else None, level, s, a.finished_at))
        if rng.random() < 0.1:  # a raion alert the volunteer file does not have
            s = a.started_at + pd.Timedelta(hours=5)
            rows.append((a.region, raions[0].name, None, "raion", s, s + pd.Timedelta(minutes=30)))
    df = pd.DataFrame(rows, columns=["oblast", "raion", "hromada", "level", "started_at", "finished_at"])
    df["source"] = "official"
    return df


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("app")
    path = d / "alerts.csv"
    alerts = synthetic_alerts()
    alerts.to_csv(path, index=False)
    official = d / "official.csv"
    # stops 10 days before the volunteer history ends, like the real official file
    synthetic_official(alerts, end=alerts["started_at"].max() - pd.Timedelta(days=10)).to_csv(official, index=False)
    types = d / "type_forecast_now.csv"
    rows = []
    for r in REGIONS[:3]:  # one region without a type forecast on purpose
        for h in (1, 3):
            rows.append({"region": r, "horizon_h": h, "p_drone": 0.6, "p_massed_drone": 0.1, "p_kab": 0.05,
                         "p_missile": 0.15, "p_ballistic": 0.1, "p_red": 0.4, "level": "yellow",
                         "most_likely": "drone", "p_onset_drone": 0.3})
    pd.DataFrame(rows).to_csv(types, index=False)
    (d / "type_forecast_meta.json").write_text('{"origin_utc": "2026-09-13 10:00"}')
    return Settings(
        data_path=path, official_path=official, model_dir=d / "models", state_dir=d / "state", reports_dir=d / "reports",
        download=False,
        train_months=1, n_trees=20, type_forecast=str(types), holdout_days=3, background=False, lgb_threads=2,
    )


@pytest.fixture(scope="session")
def engine(settings) -> ForecastEngine:
    e = ForecastEngine(settings)
    e.bootstrap()
    assert e.status == "ready", e.message
    return e


@pytest.fixture(scope="session")
def client(settings, engine) -> TestClient:
    with TestClient(create_app(settings, engine)) as c:
        yield c
