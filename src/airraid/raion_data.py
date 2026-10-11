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


def _raion_titles(uids) -> list[str | None]:
    """Raion title of each alerts.in.ua location UID (hromadas via the collector's location list)."""
    try:
        from .live_alerts import locations
        loc = locations()
    except Exception:  # noqa: BLE001 - the list ships with the live collector
        return [None] * len(uids)
    out = []
    for u in uids:
        u = None if u is None or (isinstance(u, float) and np.isnan(u)) else str(u)
        ru = loc.at[u, "raion_uid"] if u in loc.index else ""
        out.append(loc.at[ru, "title"] if ru in loc.index else (loc.at[u, "title"] if u in loc.index else None))
    return out


POLL_GAP = pd.Timedelta(minutes=30)  # as airraid.live_alerts: polls further apart break continuous coverage
KEY = ["uid", "started_at", "alert_type"]


def store_raion_records(root: Path, as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp | None, pd.Timestamp | None]:
    """Per-location alerts.in.ua records known at `as_of` and the span over which they are complete.

    Same rules as `airraid.live_alerts.store_records` (written for the oblast
    gap): history pulls as known at `as_of`; a record seen in one poll and
    missing from the next is over by that next poll. Complete from 29 days
    before the oldest of the latest pulls of all oblasts (each pull holds ~30
    days) to that pull, then through polls without gaps > POLL_GAP.
    Returns (records, since, covered_until); since/covered_until are None
    when the store does not hold a complete span.
    """
    from . import store
    from .live_alerts import OBLAST_UID

    cols = ["oblast", "uid", "level", "started_at", "finished_at", "alert_type"]
    hist = store.load(root, "alerts_history", as_of=as_of)
    raw = store.load(root, "alerts_history", as_of=as_of, raw=True)
    pulls = store.load(root, "history_pulls", as_of=as_of)
    polls = store.load(root, "polls", as_of=as_of)
    act = store.load(root, "alerts_active", as_of=as_of)
    parts = [hist[hist["started_at"] < as_of][cols]] if len(hist) else []
    t = pd.DatetimeIndex(pd.to_datetime(polls["observed_at"], utc=True).sort_values().unique()) if len(polls) else None
    if len(act) and t is not None:
        last = act.groupby(KEY, dropna=False).agg(oblast=("oblast", "last"), level=("level", "last"),
                                                   seen=("observed_at", "max")).reset_index()
        i = t.searchsorted(pd.DatetimeIndex(last["seen"]), side="right")  # first poll after the last sighting
        last["finished_at"] = t.append(pd.DatetimeIndex([pd.NaT], tz="UTC"))[i]
        parts.append(last[cols])
    rec = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    if len(rec):
        rec = rec.sort_values("finished_at", na_position="last").drop_duplicates(KEY, keep="first")
    pulled = []
    if len(pulls):
        ok = pulls[pulls["status"].isin([200, 304])]
        pulled.append(pd.Series(ok["fetched_at"].to_numpy(), index=ok["uid"].astype(str).to_numpy()))
    if len(raw) and "requested_uid" in raw:
        pulled.append(pd.Series(raw["fetched_at"].to_numpy(), index=raw["requested_uid"].astype(str).to_numpy()))
    since = reach = None
    if pulled:
        last_pull = pd.concat(pulled).groupby(level=0).max()
        need = {u for u, r in OBLAST_UID.items() if r not in NO_DATA_OBLASTS}
        if need <= set(last_pull.index):
            reach = pd.Timestamp(last_pull[list(need)].min())
            since = reach - pd.Timedelta(days=29)
    if t is not None and len(t):
        runs = pd.Series(t).diff().gt(POLL_GAP).cumsum()
        for _, r in pd.Series(t).groupby(runs.to_numpy()):
            if reach is None:
                since, reach = r.iloc[0], r.iloc[-1]  # polls only: complete over their latest run
            elif r.iloc[0] <= reach + POLL_GAP and r.iloc[-1] > reach:
                reach = r.iloc[-1]
    return rec.reset_index(drop=True), since, reach


def load_store(root: Path | None, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[tuple]]:
    """Raion alerts and the covered time span from the live store (empty without it)."""
    empty = pd.DataFrame(columns=COLUMNS), []
    if root is None or not Path(root).exists():
        return empty
    try:
        rec, since, covered = store_raion_records(Path(root), pd.Timestamp.now(tz="UTC") if as_of is None else as_of)
    except ImportError:
        return empty
    if not len(rec):
        return empty
    rec = rec.assign(title=None, raion=_raion_titles(rec["uid"].tolist()),
                     observed_until=covered if covered is not None else rec["started_at"])
    out = from_live_records(rec, "alerts.in.ua")
    return out, ([(since, covered)] if since is not None and covered is not None and covered > since else [])


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

    def latest_window(self, length: pd.Timedelta, until: pd.Timestamp) -> pd.Timestamp | None:
        """Latest end e <= until such that [e - length, e] is covered (None if no span is long enough)."""
        for a, b in sorted(self.spans, key=lambda x: x[1], reverse=True):
            e = min(b, until)
            if e - length >= a:
                return e
        return None


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
