"""What the service saw and what it predicted, kept on disk.

* `LiveRecorder` turns the minute-by-minute alerts.in.ua polls into alert
  intervals. They fill the gap between the end of the volunteer dataset
  (updated about once a day) and now, both for the forecast features and for
  checking past forecasts.
* `ForecastJournal` stores every forecast the service issued, so it can later be
  compared with what actually happened (`ForecastEngine.verification`).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import pandas as pd

from .live import LiveState

log = logging.getLogger("airraid_app")

# Polls further apart than this mean the service was down or asleep: what
# happened in between is unknown, so coverage restarts.
MAX_POLL_GAP = pd.Timedelta(minutes=15)
KEEP = pd.Timedelta(days=30)


class LiveRecorder:
    COLUMNS = ["region", "start", "end", "open"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.closed: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
        self.open: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}  # region -> (start, last seen)
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
        # After a restart the last poll is old, so everything still open is closed
        # at the moment it was last seen; coverage starts again from scratch.
        for r in df.itertuples():
            self.closed.append((r.region, r.start, r.end))

    def observe(self, state: LiveState) -> None:
        if state.source != "alerts.in.ua":
            return
        now = state.as_of
        with self.lock:
            if self.last_poll is None or now - self.last_poll > MAX_POLL_GAP:
                for region, (start, seen) in self.open.items():
                    self.closed.append((region, start, seen))
                self.open.clear()
                self.covered_since = now
            for region, s in state.regions.items():
                if s.active:
                    start = s.since if s.since is not None else now
                    if region in self.open:
                        start = min(start, self.open[region][0])
                    self.open[region] = (start, now)
                elif region in self.open:
                    start, _ = self.open.pop(region)
                    self.closed.append((region, start, now))
            self.last_poll = now
            cutoff = now - KEEP
            self.closed = [c for c in self.closed if c[2] > cutoff]
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.frame(include_open=True).to_csv(self.path, index=False)

    def frame(self, include_open: bool = True) -> pd.DataFrame:
        rows = [(r, s, e, False) for r, s, e in self.closed]
        if include_open:
            rows += [(r, s, seen, True) for r, (s, seen) in self.open.items()]
        df = pd.DataFrame(rows, columns=self.COLUMNS)
        df["start"] = pd.to_datetime(df["start"], utc=True)
        df["end"] = pd.to_datetime(df["end"], utc=True)
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
