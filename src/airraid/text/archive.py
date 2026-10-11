"""Point-in-time archive of collected text: Telegram posts and news items.

Layout under ROOT (kept on the `text-data` branch):

    telegram/<channel>/<YYYY-MM>.jsonl   one line per post, partitioned by publish month (UTC)
    telegram/<channel>/state.json        coverage of this channel (see below)
    news/<source>/<YYYY-MM>.jsonl        one line per news item
    news/<source>/state.json

One record per item:

    channel | source   where it came from
    id                 post id (Telegram) or URL (news)
    date               publish time claimed by the source, UTC
    first_seen_at      when we (or the crawler that saw it for us, e.g. GDELT) first saw it
    last_seen_at       when we last saw it
    text_first         text as first seen. NEVER overwritten
    text_last          text as last seen (null when unchanged)
    edited_first       the source marked it as edited when we first saw it
    edited_seen        edited at any sighting, or text changed between sightings
    collected_mode     live (first seen within LIVE_LAG of publishing) | backfill | gdelt

When the text became public (`available_at`, the only time features may use):

    not edited when first seen  -> date            the text we hold is the original one
    edited when first seen      -> first_seen_at   the edit time is unknown; the text we hold
                                                   was public at the latest when we saw it
    news (RSS)                  -> first_seen_at   claimed publish times are not verified
    news (GDELT)                -> first_seen_at   = GDELT crawl time (`seendate`)

Coverage (state.json): `complete_from` .. `complete_until` is the period for which
we hold every post the channel published (minus `gaps`). Outside it features are
NaN, so "we were not collecting" never looks like "nothing happened".
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

LIVE_LAG = pd.Timedelta(hours=3)
KEY = {"telegram": "channel", "news": "source"}
RECORD = ["id", "date", "first_seen_at", "last_seen_at", "text_first", "text_last",
          "edited_first", "edited_seen", "collected_mode"]
TIME_COLS = ("date", "first_seen_at", "last_seen_at")


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(x) -> pd.Timestamp | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else pd.Timestamp(x).tz_convert("UTC")


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").floor("s")


# ------------------------------------------------------------------ state
def source_dir(root: Path, kind: str, source: str) -> Path:
    return Path(root) / kind / source


def read_state(root: Path, kind: str, source: str) -> dict:
    p = source_dir(root, kind, source) / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_state(root: Path, kind: str, source: str, state: dict) -> None:
    d = source_dir(root, kind, source)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def coverage(state: dict) -> tuple[pd.Timestamp | None, pd.Timestamp | None, list]:
    gaps = [(_ts(a), _ts(b)) for a, b in state.get("gaps", [])]
    return _ts(state.get("complete_from")), _ts(state.get("complete_until")), gaps


# ---------------------------------------------------------------- records
def read_records(root: Path, kind: str, source: str, months: list[str] | None = None) -> pd.DataFrame:
    """All records of one source (or only the given YYYY-MM partitions)."""
    d = source_dir(root, kind, source)
    paths = sorted(d.glob("*.jsonl")) if months is None else [d / f"{m}.jsonl" for m in months]
    rows = []
    for p in paths:
        if p.exists():
            with p.open(encoding="utf-8") as f:
                rows.extend(json.loads(line) for line in f if line.strip())
    df = pd.DataFrame(rows, columns=[KEY[kind], *RECORD])
    if df.empty:
        df[KEY[kind]] = df[KEY[kind]].astype(object)
    df[KEY[kind]] = source
    for c in TIME_COLS:
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    df["text_last"] = df["text_last"].where(df["text_last"].notna(), df["text_first"])
    for c in ("edited_first", "edited_seen"):
        df[c] = df[c].fillna(False).astype(bool)
    return df


def write_records(root: Path, kind: str, source: str, df: pd.DataFrame) -> list[str]:
    """Write the given records into their month partitions; returns the months written.

    Partitions are rewritten whole but deterministically (sorted, stable keys), so an
    unchanged partition is byte-identical and git stores only the lines that changed.
    """
    d = source_dir(root, kind, source)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    month = df["date"].dt.strftime("%Y-%m")
    for m, g in df.groupby(month):
        g = g.sort_values("id", key=_id_sort_key)
        lines = []
        for r in g.itertuples(index=False):
            rec = {KEY[kind]: source, "id": _plain(r.id)}
            rec["date"] = _iso(r.date)
            rec["first_seen_at"] = _iso(r.first_seen_at)
            rec["last_seen_at"] = _iso(r.last_seen_at)
            rec["text_first"] = r.text_first
            rec["text_last"] = None if r.text_last == r.text_first else r.text_last
            rec["edited_first"] = bool(r.edited_first)
            rec["edited_seen"] = bool(r.edited_seen)
            rec["collected_mode"] = r.collected_mode
            lines.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        p = d / f"{m}.jsonl"
        body = "\n".join(lines) + "\n"
        if not p.exists() or p.read_text(encoding="utf-8") != body:
            p.write_text(body, encoding="utf-8")
            written.append(m)
    return written


def _plain(x):
    return int(x) if isinstance(x, (np.integer, int)) else x


def _id_sort_key(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce") if pd.api.types.is_numeric_dtype(s) else s.astype(str)


def merge(old: pd.DataFrame, obs: pd.DataFrame, key: str) -> pd.DataFrame:
    """Merge new observations into existing records without touching first-seen fields.

    obs columns: key, id, date, text, edited, seen_at (and optionally first_seen_at /
    collected_mode to override, used for GDELT whose crawl time is the first sighting).
    Returns the full set of affected records (existing ones updated, new ones added).
    """
    obs = obs.sort_values("seen_at").drop_duplicates([key, "id"], keep="first")
    if old is None or old.empty:
        old = pd.DataFrame(columns=[key, *RECORD])
    idx = old.set_index([key, "id"]).index
    have = obs.set_index([key, "id"]).index.isin(idx)

    new = obs[~have]
    first = new["first_seen_at"] if "first_seen_at" in new else new["seen_at"]
    mode = np.where(first - new["date"] <= LIVE_LAG, "live", "backfill")
    if "collected_mode" in new:
        mode = new["collected_mode"].fillna(pd.Series(mode, index=new.index)).to_numpy()
    added = pd.DataFrame({
        key: new[key].to_numpy(), "id": new["id"].to_numpy(), "date": new["date"].to_numpy(),
        "first_seen_at": first.to_numpy(), "last_seen_at": new["seen_at"].to_numpy(),
        "text_first": new["text"].to_numpy(), "text_last": new["text"].to_numpy(),
        "edited_first": new["edited"].astype(bool).to_numpy(), "edited_seen": new["edited"].astype(bool).to_numpy(),
        "collected_mode": mode,
    })

    seen = obs[have].set_index([key, "id"])
    upd = old.set_index([key, "id"])
    upd = upd.loc[upd.index.isin(seen.index)].copy()
    if len(upd):
        s = seen.loc[upd.index]
        upd["last_seen_at"] = np.maximum(upd["last_seen_at"].to_numpy(), s["seen_at"].to_numpy())
        later = (s["seen_at"] >= upd["last_seen_at"]).to_numpy()
        upd.loc[later, "text_last"] = s.loc[later, "text"].to_numpy()
        changed = (s["text"].to_numpy() != upd["text_first"].to_numpy())
        upd["edited_seen"] = upd["edited_seen"].astype(bool).to_numpy() | s["edited"].astype(bool).to_numpy() | changed
    upd = upd.reset_index()
    out = pd.concat([x for x in (upd, added) if len(x)], ignore_index=True) if len(upd) or len(added) else added
    for c in TIME_COLS:
        out[c] = pd.to_datetime(out[c], utc=True)
    return out


def available_at(df: pd.DataFrame, kind: str = "telegram") -> pd.Series:
    """When the text we hold (text_first) was public. See the module docstring."""
    if kind == "telegram":
        return df["date"].where(~df["edited_first"].astype(bool), df["first_seen_at"])
    return df["first_seen_at"]


def upsert(root: Path, kind: str, source: str, obs: pd.DataFrame) -> tuple[int, int, list[str]]:
    """Merge observations into the stored partitions they belong to. Returns (new, seen again, months written)."""
    if obs.empty:
        return 0, 0, []
    key = KEY[kind]
    obs = obs.assign(**{key: source})
    months = sorted(set(obs["date"].dt.strftime("%Y-%m")))
    old = read_records(root, kind, source, months)
    merged = merge(old, obs, key)
    n_new = int((~merged.set_index([key, "id"]).index.isin(old.set_index([key, "id"]).index)).sum()) if len(old) else len(merged)
    # unchanged records in the touched months must be kept as they are
    keep = old[~old.set_index([key, "id"]).index.isin(merged.set_index([key, "id"]).index)] if len(old) else old
    full = pd.concat([x for x in (keep, merged) if len(x)], ignore_index=True)
    written = write_records(root, kind, source, full)
    return n_new, len(merged) - n_new, written


def load(root: Path, kind: str, sources: list[str] | None = None) -> pd.DataFrame:
    """All records of the given sources with `available_at` and coverage attached as attrs."""
    base = Path(root) / kind
    if sources is None:
        sources = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []
    frames = [read_records(root, kind, s) for s in sources]
    frames = [f for f in frames if len(f)]
    key = KEY[kind]
    if frames:
        df = pd.concat(frames, ignore_index=True)
    else:
        df = read_records(root, kind, "_none_")
    df = df.rename(columns={key: "source"})
    df["available_at"] = available_at(df, kind)
    df["kind"] = kind
    df.attrs["coverage"] = {s: coverage(read_state(root, kind, s)) for s in sources}
    return df.sort_values("available_at", kind="stable").reset_index(drop=True)
