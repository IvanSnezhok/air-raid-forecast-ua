"""Raion-level (district) alert history.

Sources
-------
* The "official" file of github.com/Vadimkin/ukrainian-air-raid-sirens-dataset
  (`official_data_en.csv`): oblast-, raion- and hromada-level records from
  2022-03-15. It stopped updating on 2026-09-07. Since roughly November 2025
  almost every alert in it is declared per raion (see `coverage`).
* alerts.in.ua records persisted by the live collector (`airraid.store`, kinds
  `alerts_history` and `alerts_active`), when that store is available. They are
  the only raion-level source after 2026-09-07.

Every record is mapped to the raions it covers (column `region` holds the raion
id from `airraid.raions`, so `airraid.data.build_grid` works unchanged):
  * an oblast-level record covers every raion of the oblast;
  * a raion-level record covers that raion;
  * a hromada- or city-level record covers the raion it lies in. It is only
    part of the raion, but this is what a resident of the raion sees on an
    alert map, and the official file does the same at oblast level.
Luhansk oblast, Crimea and Sevastopol are dropped (no usable signal; the oblast
model drops Luhansk too).

Cleaning follows `airraid.data.load_alerts`: zero-length records are dropped
and every alert is capped at MAX_ALERT_HOURS. The cap depends only on elapsed
time, so it can be applied in real time and does not leak.

No-leakage convention: `alerts_as_of(df, T)` keeps what was known at T, i.e.
alerts that started before T, with the end of any alert still running at T
cut at T. Features only ever look at minutes before T (see airraid.features).
"""
from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .data import MAX_ALERT_HOURS, RAW_PATH
from .raions import BY_ID, NO_DATA_OBLASTS, RAIONS, forecastable, from_alerts_in_ua, from_official, oblast_raions

log = logging.getLogger(__name__)

OFFICIAL_URL = (
    "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/"
    "main/datasets/official_data_en.csv"
)
OFFICIAL_PATH = RAW_PATH.parent / "official_data_en.csv"
COLUMNS = ["region", "oblast", "start", "end", "level", "source"]
FORECAST_RAIONS = [r.id for r in RAIONS if forecastable(r)]
# Polls further apart than this leave the time between them unknown.
MAX_POLL_GAP = pd.Timedelta(minutes=15)


def download_official(path: Path = OFFICIAL_PATH, url: str = OFFICIAL_URL) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)
    return path


def read_official(path: Path = OFFICIAL_PATH, download: bool = True) -> pd.DataFrame:
    if not path.exists():
        if not download:
            raise FileNotFoundError(path)
        download_official(path)
    return pd.read_csv(path, parse_dates=["started_at", "finished_at"])


def _expand(rows: pd.DataFrame, oblast_col: str, resolve) -> pd.DataFrame:
    """One output row per (record, covered raion). `resolve(row) -> raion id | None` for sub-oblast rows."""
    out = []
    obl = rows[rows["level"] == "oblast"]
    for oblast, q in obl.groupby(oblast_col):
        ids = [r.id for r in oblast_raions(oblast) if forecastable(r)]
        for rid in ids:
            out.append(q.assign(region=rid))
    sub = rows[rows["level"] != "oblast"]
    if len(sub):
        rid = [resolve(r) for r in sub.itertuples(index=False)]
        sub = sub.assign(region=rid)
        missing = sub["region"].isna()
        if missing.any():
            log.warning("%d sub-oblast records could not be mapped to a raion", int(missing.sum()))
        out.append(sub[~missing])
    if not out:
        return pd.DataFrame(columns=list(rows.columns) + ["region"])
    return pd.concat(out, ignore_index=True)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["end"] > df["start"]]
    cap = df["start"] + pd.Timedelta(hours=MAX_ALERT_HOURS)
    df = df.assign(end=df["end"].where(df["end"] <= cap, cap))
    df = df[df["region"].map(lambda r: r in BY_ID and forecastable(BY_ID[r]))]
    return df[COLUMNS].sort_values(["region", "start"]).reset_index(drop=True)


def load_official(path: Path = OFFICIAL_PATH, since: pd.Timestamp | None = None, download: bool = True,
                  raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Raion-level alerts from the official file: columns region (raion id), oblast, start, end, level, source."""
    df = read_official(path, download) if raw is None else raw
    df = df[~df["oblast"].isin(NO_DATA_OBLASTS)]
    if since is not None:
        df = df[df["finished_at"] > since]
    df = df.rename(columns={"started_at": "start", "finished_at": "end"})
    # Kyiv City is a single unit: every record is "oblast" level.
    df = df.assign(level=np.where(df["oblast"] == "Kyiv City", "oblast", df["level"]))
    resolve = lambda r: getattr(from_official(r.oblast, r.raion), "id", None)  # noqa: E731
    out = _expand(df, "oblast", resolve)
    out["source"] = "official"
    return _clean(out)


def official_known_until(raw: pd.DataFrame) -> pd.Timestamp:
    """Last full hour the official file covers (its last update, approximated by the last all-clear)."""
    return raw["finished_at"].max().floor("h")


# --------------------------------------------------------------- alerts.in.ua
def from_live_records(df: pd.DataFrame, source: str = "alerts.in.ua") -> pd.DataFrame:
    """alerts.in.ua records -> raion alerts.

    `df` has the columns of `airraid.live_alerts.to_frame` (uid, oblast, title,
    level, raion, started_at, finished_at, alert_type). Only `air_raid` alerts
    count. `finished_at` may be missing (alert still running when observed);
    such rows get end = `observed_until` if that column exists, else NaT
    (callers cut them at the forecast time).
    """
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df[df["alert_type"] == "air_raid"].copy()
    df = df[~df["oblast"].isin(NO_DATA_OBLASTS)]
    df["level"] = np.where(df["oblast"] == "Kyiv City", "oblast", df["level"])
    df = df.rename(columns={"started_at": "start"})
    end = df["finished_at"]
    if "observed_until" in df:
        end = end.fillna(df["observed_until"])
    df["end"] = end
    resolve = lambda r: getattr(from_alerts_in_ua(r.uid, r.title, r.raion), "id", None)  # noqa: E731
    out = _expand(df, "oblast", resolve)
    out["source"] = source
    out["end"] = out["end"].fillna(out["start"] + pd.Timedelta(hours=MAX_ALERT_HOURS))
    return _clean(out)


def intervals_from_polls(polls: pd.DataFrame) -> pd.DataFrame:
    """Polls of `/alerts/active.json` (one row per alert per poll, column observed_at) -> alert records.

    An alert seen in consecutive polls is one record; it ended at the first poll
    where it is missing. Polls more than MAX_POLL_GAP apart do not vouch for the
    time between them, so the record ends at the last poll that saw it. An alert
    still present in the latest poll is open (finished_at NaT, observed_until =
    the latest poll).
    """
    if polls.empty:
        return polls.assign(observed_until=pd.Series(dtype="datetime64[ns, UTC]"))
    times = np.sort(polls["observed_at"].unique())
    key = ["id"] if "id" in polls and polls["id"].notna().all() else ["uid", "title", "started_at"]
    g = polls.sort_values("observed_at").groupby(key, dropna=False)
    rec = g.last().reset_index()
    last_seen = g["observed_at"].max().reset_index(drop=True).to_numpy()
    nxt_idx = np.searchsorted(times, last_seen, "right")
    latest = times[-1]
    fin = []
    for ls, i in zip(last_seen, nxt_idx):
        if i >= len(times):
            fin.append(pd.NaT)
        else:
            nxt = times[i]
            fin.append(nxt if nxt - ls <= MAX_POLL_GAP else ls)
    fin = pd.to_datetime(pd.Series(fin), utc=True)
    rec["finished_at"] = rec["finished_at"].fillna(fin) if "finished_at" in rec else fin
    rec["observed_until"] = pd.Timestamp(latest)
    return rec


def load_store(root: Path | None, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[tuple]]:
    """Raion alerts and covered time spans from the live store (empty without it).

    Uses airraid.store (append-only daily partitions written by the collector):
    `alerts_history` (alerts.in.ua month_ago history, columns of
    live_alerts.to_frame plus fetched_at) and `alerts_active` (polls, plus
    observed_at). `as_of` restricts to rows written up to that moment.
    """
    empty = pd.DataFrame(columns=COLUMNS), []
    if root is None:
        return empty
    try:
        from . import store  # written by the live collector (separate module)
    except ImportError:
        return empty
    frames, spans = [], []
    try:
        hist = store.load(root, "alerts_history", as_of)
    except Exception as e:  # noqa: BLE001 - a missing/partial store must not break forecasting
        log.info("no alerts_history in store: %s", e)
        hist = pd.DataFrame()
    if hist is not None and len(hist):
        tcol = "fetched_at" if "fetched_at" in hist else "first_seen_at"
        hist = hist.sort_values(tcol)
        idcol = "id" if "id" in hist else None
        latest = hist.drop_duplicates(idcol, keep="last") if idcol else hist
        latest = latest.assign(observed_until=latest[tcol])
        frames.append(from_live_records(latest, "alerts.in.ua history"))
        # month_ago.json covers the 30 days before each fetch (29 to be safe)
        spans.append((hist[tcol].min() - pd.Timedelta(days=29), hist[tcol].max()))
    try:
        polls = store.load(root, "alerts_active", as_of)
    except Exception as e:  # noqa: BLE001
        log.info("no alerts_active in store: %s", e)
        polls = pd.DataFrame()
    if polls is not None and len(polls):
        frames.append(from_live_records(intervals_from_polls(polls), "alerts.in.ua polls"))
        t = np.sort(polls["observed_at"].unique())
        cut = np.flatnonzero(np.diff(t) > MAX_POLL_GAP) + 1
        for a, b in zip(np.r_[0, cut], np.r_[cut, len(t)]):
            spans.append((pd.Timestamp(t[a]), pd.Timestamp(t[b - 1])))
    if not frames:
        return empty
    return pd.concat(frames, ignore_index=True), spans


def alerts_as_of(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """What was known at `as_of`: alerts that started before it, running ones cut at it."""
    df = df[df["start"] < as_of]
    return df.assign(end=df["end"].where(df["end"] <= as_of, as_of))


@dataclass
class RaionHistory:
    alerts: pd.DataFrame  # COLUMNS
    spans: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)  # covered time

    def covered(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        """True when [start, end) lies within the union of covered spans."""
        t = start
        for a, b in sorted(self.spans):
            if a <= t < b:
                t = b
            if t >= end:
                return True
        return t >= end

    def known_until(self) -> pd.Timestamp | None:
        return max((b for _, b in self.spans), default=None)


def load_history(official_path: Path = OFFICIAL_PATH, store_root: Path | None = None,
                 as_of: pd.Timestamp | None = None, since: pd.Timestamp | None = None,
                 download: bool = True) -> RaionHistory:
    """Official file + live store, restricted to what was known at `as_of`."""
    frames, spans = [], []
    try:
        raw = read_official(official_path, download)
        frames.append(load_official(since=since, raw=raw))
        spans.append((raw["started_at"].min().floor("h"), official_known_until(raw)))
    except Exception as e:  # noqa: BLE001 - serving must survive a missing file
        log.warning("official raion file unavailable: %s", e)
    st, sp = load_store(store_root, as_of)
    if len(st):
        frames.append(st)
    spans += sp
    alerts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    if as_of is not None:
        alerts = alerts_as_of(alerts, as_of)
        spans = [(a, min(b, as_of)) for a, b in spans if a < as_of]
    return RaionHistory(alerts.sort_values(["region", "start"]).reset_index(drop=True), spans)


def coverage(raw: pd.DataFrame) -> pd.DataFrame:
    """Per oblast: how much raion-level information the official file has, and since when.

    `raion_era_since`: first month from which at least 90 % of the oblast's
    records are declared below oblast level, for every later month.
    """
    df = raw[~raw["oblast"].isin(NO_DATA_OBLASTS)].copy()
    df["sub"] = df["level"] != "oblast"
    df["month"] = df["started_at"].dt.strftime("%Y-%m")
    rows = []
    for oblast, q in df.groupby("oblast"):
        m = q.groupby("month")["sub"].mean()
        ok = (m >= 0.9)[::-1].cumprod()[::-1].astype(bool)  # true from that month to the end
        since = ok[ok].index.min() if ok.any() else None
        n_raions = len([r for r in oblast_raions(oblast) if forecastable(r)])
        seen = {getattr(from_official(oblast, r), "id", None) for r in q["raion"].dropna().unique()} - {None}
        rows.append({
            "oblast": oblast, "raions": n_raions, "raions_with_own_records": len(seen),
            "records": len(q), "sub_oblast_share": round(float(q["sub"].mean()), 3),
            "first_sub_oblast_record": str(q.loc[q["sub"], "started_at"].min().date()) if q["sub"].any() else None,
            "raion_era_since": since,
            "sub_oblast_share_last_90d": round(float(q.loc[q["started_at"] > q["started_at"].max() - pd.Timedelta(days=90), "sub"].mean()), 3),
            "last_record": str(q["started_at"].max().date()),
        })
    return pd.DataFrame(rows)
