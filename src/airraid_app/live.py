"""Current air raid alert state: alerts.in.ua when a token is set, else the dataset.

Parsing and the definition of "an alert in the oblast" live in
`airraid.live_alerts` and are shared with predict_now.py. The oblast state used
for the forecast follows the training data: an air_raid record of any level
counts, its start is the latest activation, an all-clear for any part of the
oblast ends it, and without a new activation it is over after
LIVE_EXPIRY_HOURS. So a hromada on the front line that alerts.in.ua keeps open
for months does not make the oblast "under alert since March". What
alerts.in.ua reports is kept for display: `RegionState.reported` (any air raid
record in the oblast right now), `.locations`, and every record in
`LiveState.alerts`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from airraid.live_alerts import ACTIVE_URL as API_URL
from airraid.live_alerts import LiveAlert, model_alerts
from airraid.live_alerts import fetch_active as _fetch_active
from airraid.live_alerts import parse_active as _parse_active

from .regions import ALERTS_IN_UA  # noqa: F401  (re-exported for old imports)


@dataclass
class RegionState:
    active: bool
    since: pd.Timestamp | None = None  # start of the running oblast-level alert (UTC), training definition
    locations: list[str] = field(default_factory=list)  # raions/hromadas/cities under air raid alert (display only)
    reported: bool = False  # alerts.in.ua reports an air raid alert somewhere in the oblast (display only)


@dataclass
class LiveState:
    source: str  # "alerts.in.ua" or "dataset"
    as_of: pd.Timestamp  # moment the state describes (UTC)
    regions: dict[str, RegionState]
    error: str | None = None  # why the live API was not used, if it failed
    alerts: list[LiveAlert] = field(default_factory=list)  # every record of the poll, all levels and types

    def ongoing_alerts(self) -> pd.DataFrame:
        """Ongoing alerts as rows (region, start) to append to the history."""
        rows = [{"region": r, "start": s.since} for r, s in self.regions.items() if s.active and s.since is not None]
        return pd.DataFrame(rows, columns=["region", "start"])


def fetch_active(token: str, url: str = API_URL, timeout: float = 15) -> dict:
    return _fetch_active(token, url, timeout)


def parse_active(payload: dict, regions: list[str], now: pd.Timestamp, cleared: pd.DataFrame | None = None) -> LiveState:
    return state_from_alerts(_parse_active(payload), regions, now, cleared)


def state_from_alerts(alerts: list[LiveAlert], regions: list[str], now: pd.Timestamp,
                      cleared: pd.DataFrame | None = None) -> LiveState:
    """Oblast state from one poll's records.

    `cleared` (region, end): all-clears for parts of oblasts seen so far,
    including those implied by this poll (LiveRecorder.cleared(alerts, now)).
    """
    states = {r: RegionState(False) for r in regions}
    cur = model_alerts(alerts, now, cleared)
    for region, start in zip(cur["region"], cur["start"]):
        if region in states:
            states[region] = RegionState(True, start)
    for a in alerts:
        if a.alert_type == "air_raid" and a.oblast in states:
            states[a.oblast].reported = True
            if a.level != "oblast" and a.title:
                states[a.oblast].locations.append(a.title)
    return LiveState("alerts.in.ua", now, states, alerts=alerts)


def state_from_dataset(alerts: pd.DataFrame, regions: list[str], error: str | None = None) -> LiveState:
    """Alerts running at the last moment covered by the dataset."""
    as_of = alerts["start"].max()
    states = {r: RegionState(False) for r in regions}
    running = alerts[(alerts["start"] <= as_of) & (alerts["end"] > as_of)]
    for region, start in running.groupby("region")["start"].min().items():
        if region in states:
            states[region] = RegionState(True, start, reported=True)
    return LiveState("dataset", as_of, states, error)
