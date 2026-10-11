"""Fixtures for the web app: a small synthetic alert history, so tests run offline in seconds."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("app")
    path = d / "alerts.csv"
    synthetic_alerts().to_csv(path, index=False)
    return Settings(
        data_path=path, model_dir=d / "models", reports_dir=d / "reports", download=False,
        train_months=1, n_trees=20, holdout_days=3, background=False, lgb_threads=2,
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
