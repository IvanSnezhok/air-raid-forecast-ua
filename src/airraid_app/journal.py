"""What the service saw and what it predicted, kept on disk.

* `LiveRecorder` turns the minute-by-minute alerts.in.ua polls into oblast-level
  alert intervals with the training data's definition (airraid.live_alerts:
  latest activation, ended by an all-clear for any part of the oblast or after
  LIVE_EXPIRY_HOURS without a new activation). They fill the gap between the
  end of the volunteer dataset (updated about once a day) and now, both for
  the forecast features and for checking past forecasts. Each row keeps
  `observed_at`, the poll that established it.
* `ForecastJournal` stores every forecast the service issued, so it can later be
  compared with what actually happened (`ForecastEngine.verification`).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import pandas as pd

from airraid.data import MAX_ALERT_HOURS
from airraid.live_alerts import LIVE_EXPIRY_HOURS, NAIVE_AFTER, NAIVE_LENGTH, LiveAlert

from .live import LiveState

log = logging.getLogger("airraid_app")

# Polls further apart than this mean the service was down or asleep: what
# happened in between is unknown, so coverage restarts.
MAX_POLL_GAP = pd.Timedelta(minutes=15)
KEEP = pd.Timedelta(days=30)
CAP = pd.Timedelta(hours=LIVE_EXPIRY_HOURS)  # same expiry as live_alerts.model_alerts
CLEARS_KEPT = pd.Timedelta(hours=MAX_ALERT_HOURS)


def _keys(alerts: list[LiveAlert]) -> dict[tuple, str]:
    return {(a.uid, a.started_at, a.level): a.oblast for a in alerts if a.alert_type == "air_raid"}


class LiveRecorder:
    COLUMNS = ["region", "start", "end", "open", "observed_at"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.closed: list[tuple[str, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []  # region, start, end, observed_at
        self.open: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}  # region -> (start, last seen)
        self.seen: dict[tuple, str] = {}  # air_raid records of the last poll -> oblast
        self.clears: list[tuple[str, pd.Timestamp]] = []  # all-clears for parts of an oblast: (region, observed_at)
        self.covered_since: pd.Timestamp | None = None  # start of the current unbroken run of polls
        self.last_poll: pd.Timestamp | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            df = pd.read_csv(self.path, parse_dates=["start", "end"])
        except (OSError, ValueError):
            log.warning("could not read %s", self.path)
            return
        obs = pd.to_datetime(df["observed_at"], utc=True) if "observed_at" in df else df["end"]
        # After a restart the last poll is old, so everything still open is closed
        # at the moment it was last seen; coverage starts again from scratch.
        for r, o in zip(df.itertuples(), obs):
            self.closed.append((r.region, r.start, min(r.end, r.start + CAP), o))

    def observe(self, state: LiveState) -> None:
        if state.source != "alerts.in.ua":
            return
        now = state.as_of
        with self.lock:
            if self.last_poll is None or now - self.last_poll > MAX_POLL_GAP:
                for region, (start, seen) in self.open.items():
                    self.closed.append((region, start, min(seen, start + CAP), seen))
                self.open.clear()
                self.seen = {}
                self.covered_since = now
            self.clears += self._gone(state.alerts, now)
            self.seen = _keys(state.alerts)
            for region, s in state.regions.items():
                if s.active:
                    start = s.since if s.since is not None else now
                    if region in self.open and self.open[region][0] != start:
                        old = self.open[region][0]
                        # A new activation replaces the open alert's start, as in the
                        # volunteer file; one more than 3 h older is first closed as a
                        # 30-minute alert (upstream processor rule).
                        if start - old > NAIVE_AFTER:
                            self.closed.append((region, old, old + NAIVE_LENGTH, now))
                    self.open[region] = (start, now)
                elif region in self.open:
                    start, _ = self.open.pop(region)
                    self.closed.append((region, start, min(now, start + CAP), now))
            self.last_poll = now
            cutoff = now - KEEP
            self.closed = [c for c in self.closed if c[2] > cutoff]
            self.clears = [c for c in self.clears if c[1] > now - CLEARS_KEPT]
            self._save()

    def _gone(self, alerts: list[LiveAlert], now: pd.Timestamp) -> list[tuple[str, pd.Timestamp]]:
        """All-clears: air raid records of the previous poll that are missing from this one."""
        if self.last_poll is None or now - self.last_poll > MAX_POLL_GAP:
            return []
        keys = _keys(alerts)
        return [(oblast, now) for k, oblast in self.seen.items() if k not in keys]

    def cleared(self, alerts: list[LiveAlert] | None = None, now: pd.Timestamp | None = None) -> pd.DataFrame:
        """All-clears of the last MAX_ALERT_HOURS (region, end), plus those implied by
        a new poll's `alerts` at `now`; pass them to live.state_from_alerts."""
        with self.lock:
            rows = self.clears + (self._gone(alerts, now) if alerts is not None and now is not None else [])
        return pd.DataFrame(rows, columns=["region", "end"])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.frame(include_open=True).to_csv(self.path, index=False)

    def frame(self, include_open: bool = True) -> pd.DataFrame:
        rows = [(r, s, e, False, o) for r, s, e, o in self.closed]
        if include_open:
            rows += [(r, s, seen, True, seen) for r, (s, seen) in self.open.items()]
        df = pd.DataFrame(rows, columns=self.COLUMNS)
        for c in ("start", "end", "observed_at"):
            df[c] = pd.to_datetime(df[c], utc=True)
        return df

    def alerts(self) -> pd.DataFrame:
        """Recorded alerts in the dataset's format (region, start, end, naive)."""
        with self.lock:
            df = self.frame()
        df = df[df["end"] > df["start"]]
        return df.assign(naive=False)[["region", "start", "end", "naive"]]

    def known_until(self, hist_end: pd.Timestamp) -> pd.Timestamp:
        """Last moment for which the alert record is complete.

        The dataset is complete up to its end; live polls extend that only when
        they have been running without a break since before the dataset ended.
        """
        with self.lock:
            if self.covered_since is not None and self.covered_since <= hist_end and self.last_poll is not None:
                return max(hist_end, self.last_poll)
        return hist_end

    def version(self) -> tuple:
        with self.lock:
            return (len(self.closed), tuple(sorted((r, str(s)) for r, (s, _) in self.open.items())))


class ForecastJournal:
    COLUMNS = ["origin", "region", "target", "horizon", "p", "clim", "issued_at", "live"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.df = pd.DataFrame(columns=self.COLUMNS)
        if path.exists():
            try:
                self.df = pd.read_parquet(path)
            except (OSError, ValueError):
                log.warning("could not read %s", path)

    def has(self, origin: pd.Timestamp) -> bool:
        with self.lock:
            return bool(len(self.df)) and (self.df["origin"] == origin).any()

    def record(self, rows: pd.DataFrame) -> None:
        with self.lock:
            origins = set(rows["origin"])
            keep = self.df[~self.df["origin"].isin(origins)] if len(self.df) else self.df
            df = pd.concat([keep, rows[self.COLUMNS]], ignore_index=True) if len(keep) else rows[self.COLUMNS].copy()
            df = df[df["origin"] > df["origin"].max() - KEEP]
            self.df = df.reset_index(drop=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.df.to_parquet(self.path)

    def frame(self) -> pd.DataFrame:
        with self.lock:
            return self.df.copy()
