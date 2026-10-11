"""Live forecast from the served models: one place for the origin rule, the
feature grid and the prediction, shared by predict_now.py (the pipeline) and
the web app / static site (airraid_app.engine).

Origin
------
The origin is the first whole minute at or after the newest live observation
(an alerts.in.ua poll). Everything used as an input was known at the origin:
store rows with a known-at time <= origin, live records that started before it.
With the 10-minute pipeline this gives a fresh forecast every run (origins such
as 14:04, 14:13, 14:23), each "for the next 60 minutes from HH:MM". The grid's
hour blocks are aligned to end at the origin (data.grid_start), so the models,
trained at top-of-hour origins, see the same feature definitions at any minute.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import AlertGrid, build_grid
from .features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts
from .live_alerts import gap_alerts
from .models import feature_columns
from .training import CLIM_COL, TARGETS


def origin_after(seen: pd.Timestamp) -> pd.Timestamp:
    """Forecast origin for data observed at `seen`: the next whole minute (or `seen` itself)."""
    return seen.tz_convert("UTC").ceil("min")


def feature_grid(alerts: pd.DataFrame, origin: pd.Timestamp, records: pd.DataFrame | None = None,
                 ) -> tuple[AlertGrid, pd.DataFrame]:
    """(grid ending at `origin`, gap alerts): the dataset plus oblast-level alerts
    rebuilt from live `records` known at `origin`, only after the dataset ends."""
    gap = gap_alerts(records, origin, alerts)
    hist = pd.concat([alerts, gap], ignore_index=True) if len(gap) else alerts
    return build_grid(hist[hist["start"] < origin], data_end=origin), gap


def predict(models: dict, grid: AlertGrid) -> pd.DataFrame:
    """Forecast at the grid's end (the origin) for every region, target and horizon.

    Long frame: origin, region, target, horizon, p, clim (28-day climatology of
    the same window, the reference forecast), active_now.
    """
    H, S = hourly_fraction(grid), hourly_starts(grid)
    k_now = grid.n_minutes // 60
    assert k_now * 60 == grid.n_minutes, "grid must end on a block boundary (build_grid aligns it)"
    origin = grid.t0 + pd.Timedelta(minutes=grid.n_minutes)
    base = build_features(grid, np.array([k_now]), H)
    rows = []
    for h in HORIZONS:
        x = add_horizon_features(base, grid, H, h, S)
        for t in TARGETS:
            m = models[(t, h)]
            if sorted(m.model.feature_name()) != sorted(feature_columns(x) + ["region"]):
                raise RuntimeError("feature set changed since the models were trained; retrain needed")
            rows.append(pd.DataFrame({
                "origin": origin, "region": x["region"].to_numpy(), "target": t, "horizon": h,
                "p": np.round(m.predict(x), 4), "clim": x[CLIM_COL[t]].to_numpy().astype(float).round(4),
                "active_now": x["active_now"].to_numpy().astype(int),
            }))
    return pd.concat(rows, ignore_index=True)


def wide(long: pd.DataFrame) -> pd.DataFrame:
    """region x {active,onset}_{h}h table (reports/forecast_now.csv)."""
    w = long.assign(col=long["target"] + "_" + long["horizon"].astype(str) + "h").pivot(
        index="region", columns="col", values="p")
    cols = [f"{t}_{h}h" for t in TARGETS for h in HORIZONS]
    return w[cols].round(3).reset_index()
