"""Loading and cleaning the air raid alert history.

Source: volunteer (eTryvoga) dataset maintained at
https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset

Why the volunteer file and not the "official" one:
  * the official file in that repo stopped updating on 2026-09-07, so it cannot
    be used to evaluate on the latest period;
  * since Dec 2025 official alerts are mostly declared per raion (district),
    which silently changes what "an alert in the oblast" means over time.
    The volunteer file is oblast-level for the whole war.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_URL = (
    "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/"
    "main/datasets/volunteer_data_en.csv"
)
RAW_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "volunteer_data_en.csv"

# Luhansk has had a permanent alert since 2022-04-04 and is (almost) absent from
# the file; Crimea is not in the file at all. Both carry no forecastable signal.
EXCLUDED_REGIONS = {"Luhanska oblast"}

# A handful of records last for days in western oblasts (e.g. 177 h in
# Chernivtsi), which is a missed "all clear" message, not a real alert.
# We cap every alert at MAX_ALERT_HOURS. The cap only depends on the start time
# and elapsed time, so the same rule can be applied in real time ("an alert
# older than 12 h is assumed to be over") and does not leak future information.
MAX_ALERT_HOURS = 12


def download(path: Path = RAW_PATH, url: str = DATA_URL) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)
    return path


def load_alerts(path: Path = RAW_PATH) -> pd.DataFrame:
    """Return cleaned alerts: region, start, end (UTC, tz-aware), naive flag."""
    if not path.exists():
        download(path)
    df = pd.read_csv(path, parse_dates=["started_at", "finished_at"])
    df = df.rename(columns={"started_at": "start", "finished_at": "end"})
    df = df[~df["region"].isin(EXCLUDED_REGIONS)]
    df = df[df["end"] > df["start"]]  # 6 zero/negative-length records
    cap = df["start"] + pd.Timedelta(hours=MAX_ALERT_HOURS)
    df["end"] = df["end"].where(df["end"] <= cap, cap)
    return df.sort_values(["region", "start"]).reset_index(drop=True)


@dataclass
class AlertGrid:
    """Minute-resolution alert state per region.

    active[r, m] is True when region r has an alert during minute m, where
    minute 0 is `t0`. Built from merged intervals, so overlapping records in the
    same region are counted once.
    """

    regions: list[str]
    t0: pd.Timestamp
    active: np.ndarray  # bool [n_regions, n_minutes]
    starts: list[np.ndarray]  # per region, merged interval starts (minute index)
    ends: list[np.ndarray]  # per region, merged interval ends (exclusive)
    data_end: pd.Timestamp  # last moment covered by the data

    @property
    def n_minutes(self) -> int:
        return self.active.shape[1]

    def minute(self, ts: pd.Timestamp) -> int:
        return int((ts - self.t0) / pd.Timedelta(minutes=1))


def _merge(starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(starts)
    s, e = starts[order], ends[order]
    out_s, out_e = [], []
    for a, b in zip(s, e):
        if out_e and a <= out_e[-1]:
            out_e[-1] = max(out_e[-1], b)
        else:
            out_s.append(a)
            out_e.append(b)
    return np.array(out_s, dtype=np.int64), np.array(out_e, dtype=np.int64)


def build_grid(alerts: pd.DataFrame, data_end: pd.Timestamp | None = None, regions: list[str] | None = None,
               t0: pd.Timestamp | None = None) -> AlertGrid:
    """Rasterise alerts onto a minute grid.

    `data_end` is the moment up to which we treat the data as complete. Anything
    after it is unknown, so alerts are clipped there. Passing an earlier
    `data_end` simulates what the data looked like at that moment; the leakage
    test uses this. `regions` / `t0` fix the rows and the start of the grid
    (default: the regions in the data, the day of the first alert); the raion
    layer uses them to keep raions without recent alerts.
    """
    if data_end is None:
        data_end = alerts["start"].max().floor("h")
    alerts = alerts[alerts["start"] < data_end]
    if t0 is None:
        t0 = alerts["start"].min().floor("D")
    else:
        alerts = alerts[alerts["end"] > t0]
    n_minutes = int((data_end - t0) / pd.Timedelta(minutes=1))
    regions = sorted(alerts["region"].unique()) if regions is None else sorted(regions)
    active = np.zeros((len(regions), n_minutes), dtype=bool)
    starts, ends = [], []
    for i, reg in enumerate(regions):
        sub = alerts[alerts["region"] == reg]
        s = np.maximum(((sub["start"] - t0) / pd.Timedelta(minutes=1)).to_numpy(), 0).astype(np.int64)
        e = np.ceil((sub["end"] - t0) / pd.Timedelta(minutes=1)).to_numpy().astype(np.int64)
        e = np.minimum(e, n_minutes)
        s, e = _merge(s, e)
        for a, b in zip(s, e):
            active[i, a:b] = True
        starts.append(s)
        ends.append(e)
    return AlertGrid(regions, t0, active, starts, ends, data_end)
