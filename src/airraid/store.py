"""Append-only, point-in-time store for live data.

Everything the live system sees or says is kept here so it can be reused later
for training, gap filling and verification *without* hindsight:

    <root>/<kind>/<YYYY-MM-DD>/<stamp>.parquet

* one immutable file per `append`, in the directory of the UTC day of the kind's
  time column. Files are never rewritten, so a git branch holding the store
  grows only by the new rows (no rewritten daily blobs);
* every row carries the moment it became known (`TIME_COL`): `observed_at`
  for polls, `fetched_at` for history pulls, `issued_at` for forecasts,
  `verified_at` for verification;
* `load(root, kind, as_of)` returns only rows known at `as_of`. For
  `alerts_history` it also resolves versions: a record (uid, started_at,
  alert_type) first seen without an end and later with one is returned as
  known at `as_of`, with `first_seen_at` (first fetch that contained it) and
  `known_at` (fetch that gave its current `finished_at`).

The workflow keeps the store on the orphan branch `live-data`; locally any
directory works.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

TIME_COL = {
    "polls": "observed_at",  # one row per alerts.in.ua poll (also when nothing is active)
    "alerts_active": "observed_at",  # every record of every poll, all levels and types
    "alerts_history": "fetched_at",  # month_ago records, deduplicated on append
    "history_pulls": "fetched_at",  # one row per history request (also when nothing was new)
    "forecasts": "issued_at",  # the forecast log
    "verification": "verified_at",  # scored forecasts
    "explanations": "issued_at",  # why each forecast: Telegram effect, grouped drivers, signals (airraid.explain)
    "calibration": "fitted_at",  # recalibration maps learned from verified forecasts (airraid.recalibration)
}
# Rows whose key is already stored are dropped on append (history pulls overlap by ~30 days).
DEDUP_KEY = {
    "alerts_history": ["uid", "started_at", "alert_type", "finished_at"],
    "verification": ["origin", "region", "target", "horizon", "issued_at", "label_source"],
}
HISTORY_KEY = ["uid", "started_at", "alert_type"]


def _files(root: Path, kind: str, since: pd.Timestamp | None, as_of: pd.Timestamp | None) -> list[Path]:
    d = Path(root) / kind
    if not d.exists():
        return []
    lo = None if since is None else str(since.tz_convert("UTC").date())
    hi = None if as_of is None else str(as_of.tz_convert("UTC").date())
    days = sorted(p for p in d.iterdir() if p.is_dir() and (lo is None or p.name >= lo) and (hi is None or p.name <= hi))
    return [f for day in days for f in sorted(day.glob("*.parquet"))]


def _read(files: list[Path]) -> pd.DataFrame:
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _key_frame(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    return pd.util.hash_pandas_object(df[cols].astype(str), index=False)


def append(root: Path | str, kind: str, df: pd.DataFrame) -> list[Path]:
    """Write `df` as new immutable part files (one per UTC day of its time column)."""
    if kind not in TIME_COL:
        raise ValueError(f"unknown kind {kind!r}")
    col = TIME_COL[kind]
    if df.empty:
        return []
    if col not in df:
        raise ValueError(f"{kind} rows need a {col!r} column (when they became known)")
    df = df.copy()
    df[col] = pd.to_datetime(df[col], utc=True)
    if df[col].isna().any():
        raise ValueError(f"{kind}: {col} must be set on every row")
    if kind in DEDUP_KEY:
        key = DEDUP_KEY[kind]
        df = df.drop_duplicates(key)
        # a stored copy of a row was fetched after the row's event started, so only
        # partitions from then on need to be read
        lo = pd.to_datetime(df["started_at"], utc=True).min() if "started_at" in df else None
        known = _known_keys(Path(root), kind, None if lo is None or pd.isna(lo) else lo.floor("D"))
        df = df[~_key_frame(df, key).isin(known)]
        if df.empty:
            return []
    out = []
    for day, part in df.groupby(df[col].dt.strftime("%Y-%m-%d")):
        d = Path(root) / kind / day
        d.mkdir(parents=True, exist_ok=True)
        stamp = part[col].max().strftime("%Y%m%dT%H%M%S")
        digest = hashlib.sha1(pd.util.hash_pandas_object(part.astype(str), index=False).values.tobytes()).hexdigest()[:8]
        path = d / f"{stamp}-{digest}.parquet"
        part.reset_index(drop=True).to_parquet(path, index=False, compression="zstd")
        out.append(path)
    if kind in DEDUP_KEY:
        hashes, read = _KEYS.setdefault((str(Path(root).resolve()), kind), (set(), set()))
        hashes.update(_key_frame(df, DEDUP_KEY[kind]))
        read.update(out)
    return out


# Key hashes of the part files already read, per (root, kind): appends in one
# process (a history pull writes one part per request) read each file once.
_KEYS: dict[tuple[str, str], tuple[set, set]] = {}


def _known_keys(root: Path, kind: str, since: pd.Timestamp | None) -> set:
    hashes, read = _KEYS.setdefault((str(root.resolve()), kind), (set(), set()))
    new = [f for f in _files(root, kind, since, None) if f not in read]
    if new:
        hashes.update(_key_frame(_read(new), DEDUP_KEY[kind]))
        read.update(new)
    return hashes


def load(root: Path | str, kind: str, as_of: pd.Timestamp | None = None, since: pd.Timestamp | None = None,
         raw: bool = False) -> pd.DataFrame:
    """Rows of `kind` known at `as_of` (time column <= as_of); all rows when as_of is None.

    `since` prunes day directories before that day (time column >= since).
    For alerts_history (unless `raw`), one row per record as known at `as_of`.
    """
    if kind not in TIME_COL:
        raise ValueError(f"unknown kind {kind!r}")
    col = TIME_COL[kind]
    df = _read(_files(Path(root), kind, since, as_of))
    if df.empty:
        return df
    df[col] = pd.to_datetime(df[col], utc=True)
    if as_of is not None:
        df = df[df[col] <= as_of]
    if since is not None:
        df = df[df[col] >= since]
    if kind == "alerts_history" and not raw and len(df):
        df = df.sort_values(col)
        first = df.groupby(HISTORY_KEY, dropna=False)[col].transform("min")
        df = df.assign(first_seen_at=first, known_at=df[col])
        # the latest version known at as_of; a version with an end beats one without
        df = df.assign(_has_end=df["finished_at"].notna()).sort_values(["_has_end", col])
        df = df.drop_duplicates(HISTORY_KEY, keep="last").drop(columns="_has_end")
    return df.sort_values(col, kind="stable").reset_index(drop=True)
