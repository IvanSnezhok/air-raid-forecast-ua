"""Current air raid alert state: alerts.in.ua when a token is set, else the dataset.

alerts.in.ua `/v1/alerts/active.json` returns
{"alerts": [{"alert_type", "location_title", "location_type", "location_oblast",
"started_at", ...}]}. Any active `air_raid` alert anywhere in an oblast (oblast,
raion or hromada level) marks the whole oblast as under alert, which matches
how the volunteer dataset records alerts at oblast level.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

import pandas as pd

from .regions import ALERTS_IN_UA

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"


@dataclass
class RegionState:
    active: bool
    since: pd.Timestamp | None = None  # start of the earliest ongoing alert (UTC)
    locations: list[str] = field(default_factory=list)  # raions/hromadas under alert (live only)


@dataclass
class LiveState:
    source: str  # "alerts.in.ua" or "dataset"
    as_of: pd.Timestamp  # moment the state describes (UTC)
    regions: dict[str, RegionState]
    error: str | None = None  # why the live API was not used, if it failed

    def ongoing_alerts(self) -> pd.DataFrame:
        """Ongoing alerts as rows (region, start) to append to the history."""
        rows = [{"region": r, "start": s.since} for r, s in self.regions.items() if s.active and s.since is not None]
        return pd.DataFrame(rows, columns=["region", "start"])


def fetch_active(token: str, url: str = API_URL, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "air-raid-forecast-ua"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_active(payload: dict, regions: list[str], now: pd.Timestamp) -> LiveState:
    states = {r: RegionState(False) for r in regions}
    for a in payload.get("alerts", []):
        if a.get("alert_type") != "air_raid":
            continue
        region = ALERTS_IN_UA.get(a.get("location_oblast") or "") or ALERTS_IN_UA.get(a.get("location_title") or "")
        if region not in states or not a.get("started_at"):
            continue
        start = pd.Timestamp(a["started_at"])
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        st = states[region]
        st.active = True
        st.since = start if st.since is None else min(st.since, start)
        if a.get("location_type") != "oblast" and a.get("location_title"):
            st.locations.append(a["location_title"])
    return LiveState("alerts.in.ua", now, states)


def state_from_dataset(alerts: pd.DataFrame, regions: list[str], error: str | None = None) -> LiveState:
    """Alerts running at the last moment covered by the dataset."""
    as_of = alerts["start"].max()
    states = {r: RegionState(False) for r in regions}
    running = alerts[(alerts["start"] <= as_of) & (alerts["end"] > as_of)]
    for region, start in running.groupby("region")["start"].min().items():
        if region in states:
            states[region] = RegionState(True, start)
    return LiveState("dataset", as_of, states, error)
