"""alerts.in.ua payloads: parsing and the training-consistent oblast-level alert.

Single source of truth for
  * the oblast-name map (alerts.in.ua Ukrainian titles / UIDs -> dataset region names);
  * parsing `/v1/alerts/active.json` and `/v1/regions/{uid}/alerts/month_ago.json`
    into typed `LiveAlert` records (every level, every alert type);
  * turning per-location records into "an alert in the oblast" in the sense of
    the training data (the volunteer eTryvoga file, see data.py):
    `oblast_intervals` (history), `model_alerts` (current state), `gap_alerts`
    (prediction features after the end of the dataset).

Why not "any alert anywhere in the oblast, since its earliest start"
--------------------------------------------------------------------
alerts.in.ua keeps some raion/hromada alerts open for weeks on the front line
(a Kharkivska hromada since 2026-03-10, two Dnipropetrovska hromadas since
2026-09-17). The training data has nothing like it: load_alerts caps every
record at MAX_ALERT_HOURS. The old merge made those oblasts "under alert for
months" (frac_24h = 1.0, elapsed far beyond training) and leaked into training.

Why not "oblast-level records only"
-----------------------------------
Since December 2025 alerts are declared per raion. In the official file
(Dec 2025 - Sep 2026) there are no oblast-level records outside Kyiv City,
while the volunteer file has 3-14 alerts a day per frontline oblast.

What the volunteer file is (measured, scripts/live_definition_check.py)
----------------------------------------------------------------------
It is parsed from a Telegram channel: one open alert per oblast, started by
any "alert" post for any part of the oblast (the latest post replaces the
start), ended by the next all-clear post for any part. 77-93 % of its ends
coincide (within 3 min) with an official all-clear somewhere in the oblast;
only 23-65 % of its starts with an official activation (the channel also posts
threat notices). `oblast_intervals` replays the official per-location records
through the same rules; with LIVE_EXPIRY_HOURS it gives the best forecast
Brier of the candidates on three periods, including the real alerts.in.ua feed.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .data import MAX_ALERT_HOURS

API = "https://api.alerts.in.ua"
ACTIVE_URL = f"{API}/v1/alerts/active.json"
LOCATIONS_CSV = Path(__file__).resolve().parent / "resources" / "alerts_in_ua_locations.csv"
LEVELS = ("oblast", "raion", "hromada", "city", "unknown")

# alerts.in.ua `location_oblast` / oblast `location_title` -> dataset region name.
OBLAST_EN = {
    "Черкаська область": "Cherkaska oblast", "Чернігівська область": "Chernihivska oblast",
    "Чернівецька область": "Chernivetska oblast", "Дніпропетровська область": "Dnipropetrovska oblast",
    "Донецька область": "Donetska oblast", "Івано-Франківська область": "Ivano-Frankivska oblast",
    "Харківська область": "Kharkivska oblast", "Херсонська область": "Khersonska oblast",
    "Хмельницька область": "Khmelnytska oblast", "Кіровоградська область": "Kirovohradska oblast",
    "м. Київ": "Kyiv City", "Київ": "Kyiv City", "Київська область": "Kyivska oblast",
    "Луганська область": "Luhanska oblast",
    "Львівська область": "Lvivska oblast", "Миколаївська область": "Mykolaivska oblast",
    "Одеська область": "Odeska oblast", "Полтавська область": "Poltavska oblast",
    "Рівненська область": "Rivnenska oblast", "Сумська область": "Sumska oblast",
    "Тернопільська область": "Ternopilska oblast", "Вінницька область": "Vinnytska oblast",
    "Волинська область": "Volynska oblast", "Закарпатська область": "Zakarpatska oblast",
    "Запорізька область": "Zaporizka oblast", "Житомирська область": "Zhytomyrska oblast",
}
# alerts.in.ua oblast UIDs (https://devs.alerts.in.ua/). 29 Crimea and 30 Sevastopol
# are not in the dataset and are skipped.
OBLAST_UID = {
    "3": "Khmelnytska oblast", "4": "Vinnytska oblast", "5": "Rivnenska oblast", "8": "Volynska oblast",
    "9": "Dnipropetrovska oblast", "10": "Zhytomyrska oblast", "11": "Zakarpatska oblast",
    "12": "Zaporizka oblast", "13": "Ivano-Frankivska oblast", "14": "Kyivska oblast",
    "15": "Kirovohradska oblast", "16": "Luhanska oblast", "17": "Mykolaivska oblast", "18": "Odeska oblast",
    "19": "Poltavska oblast", "20": "Sumska oblast", "21": "Ternopilska oblast", "22": "Kharkivska oblast",
    "23": "Khersonska oblast", "24": "Cherkaska oblast", "25": "Chernihivska oblast",
    "26": "Chernivetska oblast", "27": "Lvivska oblast", "28": "Donetska oblast", "31": "Kyiv City",
}
UID_OF_OBLAST = {v: k for k, v in OBLAST_UID.items()}


@dataclass(frozen=True)
class LiveAlert:
    uid: str | None  # alerts.in.ua location_uid
    oblast: str  # dataset region name, e.g. "Kharkivska oblast", "Kyiv City"
    oblast_uid: str | None
    title: str  # location_title (Ukrainian)
    level: str  # "oblast" | "raion" | "hromada" | "city" | "unknown"
    raion: str | None  # raion title if known (location_raion or derived), else None
    started_at: pd.Timestamp  # UTC
    alert_type: str  # "air_raid" etc.
    finished_at: pd.Timestamp | None = None  # UTC; only history records have it
    id: str | None = None  # alerts.in.ua record id


@lru_cache(maxsize=1)
def locations() -> pd.DataFrame:
    """alerts.in.ua location list: uid, title, level, raion_uid, oblast_uid, oblast (dataset name).

    From the "full list of locations" sheet linked in the API docs; the order of
    the sheet gives each raion/hromada its oblast.
    """
    df = pd.read_csv(LOCATIONS_CSV, dtype={"uid": str, "raion_uid": str, "oblast_uid": str}, keep_default_na=False)
    return df.set_index("uid", drop=False)


def raion_uids(oblast: str | None = None) -> list[str]:
    loc = locations()
    q = loc[(loc["level"] == "raion") & (loc["oblast"] != "")]
    return q[q["oblast"] == oblast]["uid"].tolist() if oblast else q["uid"].tolist()


def _ts(v) -> pd.Timestamp | None:
    if not v:
        return None
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _str(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _oblast(a: dict, loc: pd.DataFrame) -> str | None:
    for key in ("location_oblast", "location_title"):
        if (r := OBLAST_EN.get(a.get(key) or "")) is not None:
            return r
    if (r := OBLAST_UID.get(_str(a.get("location_oblast_uid")) or "")) is not None:
        return r
    uid = _str(a.get("location_uid"))
    if uid in loc.index and loc.at[uid, "oblast"]:
        return loc.at[uid, "oblast"]
    return None


def _raion(a: dict, level: str, loc: pd.DataFrame) -> str | None:
    if a.get("location_raion"):
        return a["location_raion"]
    if level == "raion":
        return a.get("location_title")
    uid = _str(a.get("location_uid"))
    if uid in loc.index and loc.at[uid, "raion_uid"] in loc.index:
        return loc.at[loc.at[uid, "raion_uid"], "title"]
    return None


def parse_alerts(payload: dict) -> list[LiveAlert]:
    """Every alert record in an alerts.in.ua response (active or history), all levels and types.

    Records outside the dataset's regions (Crimea, Sevastopol) or without a
    start time are skipped; nothing else is filtered here.
    """
    loc = locations()
    out = []
    for a in payload.get("alerts") or []:
        start, oblast = _ts(a.get("started_at")), _oblast(a, loc)
        if start is None or oblast is None:
            continue
        level = a.get("location_type") if a.get("location_type") in LEVELS else "unknown"
        out.append(LiveAlert(
            uid=_str(a.get("location_uid")), oblast=oblast,
            oblast_uid=_str(a.get("location_oblast_uid")) or UID_OF_OBLAST.get(oblast),
            title=a.get("location_title") or "", level=level, raion=_raion(a, level, loc),
            started_at=start, alert_type=a.get("alert_type") or "unknown",
            finished_at=_ts(a.get("finished_at")), id=_str(a.get("id")),
        ))
    return out


def parse_active(payload: dict) -> list[LiveAlert]:
    """`/v1/alerts/active.json` -> records (all levels, all alert types kept; filter later)."""
    return parse_alerts(payload)


def to_frame(alerts: list[LiveAlert]) -> pd.DataFrame:
    cols = list(LiveAlert.__dataclass_fields__)
    df = pd.DataFrame([vars(a) for a in alerts], columns=cols)
    for c in ("started_at", "finished_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def from_frame(df: pd.DataFrame) -> list[LiveAlert]:
    cols = list(LiveAlert.__dataclass_fields__)
    rows = df[cols].astype(object).where(df[cols].notna(), None).to_dict("records")
    return [LiveAlert(**r) for r in rows]


# An oblast-level alert with no new activation for this long is treated as over.
# Chosen on Jan-Apr 2026 by the Brier score of the volunteer-trained model fed
# with live-rule states instead of volunteer ones (1, 2, 3, 6, 12 h tried), and
# confirmed on May-Sep 2026 and on the real alerts.in.ua feed of Sep-Oct 2026
# (scripts/live_definition_check.py, README "Live data"). It also reproduces the
# training data's share of hours under alert (9.8 % vs 10.4 %) and mean alert
# length (0.57 h vs 0.56 h). Always <= MAX_ALERT_HOURS, the cap load_alerts applies.
LIVE_EXPIRY_HOURS = 1.0
assert LIVE_EXPIRY_HOURS <= MAX_ALERT_HOURS

# Upstream volunteer processor (Vadimkin/ukrainian-air-raid-sirens-dataset,
# processors/volunteer_etryvoga_processor.py): one open alert per oblast; a new
# "alert" message replaces its start, unless the open one is older than 3 h, in
# which case it is closed at start + 30 min (naive=True); any all-clear closes it.
NAIVE_AFTER = pd.Timedelta(hours=3)
NAIVE_LENGTH = pd.Timedelta(minutes=30)
_EPOCH = pd.Timestamp("2020-01-01", tz="UTC")


def _min(ts) -> np.ndarray:
    return ((pd.to_datetime(pd.Series(ts), utc=True) - _EPOCH) / pd.Timedelta(minutes=1)).to_numpy(np.float64)


def oblast_intervals(records: pd.DataFrame, as_of: pd.Timestamp, cap_hours: float = LIVE_EXPIRY_HOURS) -> pd.DataFrame:
    """Oblast-level alerts (region, start, end, open) rebuilt from per-location records known at `as_of`.

    `records` has columns oblast, started_at, finished_at (NaT while running or
    unknown) and optionally alert_type (only air_raid is used). Only what was
    known at `as_of` is used: starts before it, all-clears at or before it.
    Every activation of any level is an "alert" message for the oblast and
    every all-clear an "all-clear" message, replayed through the volunteer
    processor's rules above, then cut at `cap_hours` after the activation
    (LIVE_EXPIRY_HOURS, never more than the MAX_ALERT_HOURS cap of load_alerts).
    `open` marks an alert still running at `as_of` (its `end` is only the cap).
    """
    assert cap_hours <= MAX_ALERT_HOURS
    as_of = as_of.tz_convert("UTC")
    r = records[records["alert_type"] == "air_raid"] if "alert_type" in records else records
    now, cap = _min([as_of])[0], cap_hours * 60
    out = []
    for region, q in r.groupby("oblast"):
        st, fin = _min(q["started_at"]), _min(q["finished_at"])
        S = np.unique(st[st < now])  # NaN-safe: NaN < now is False
        if not len(S):
            continue
        C = np.sort(fin[fin <= now])
        j = np.searchsorted(C, S, "right")  # first all-clear strictly after each activation
        nxt_clear = np.append(C, np.inf)[j]
        nxt_start = np.append(S[1:], np.inf)
        kept = nxt_clear <= nxt_start  # closed before the next activation could replace it
        naive = ~kept & (nxt_start - S > NAIVE_AFTER / pd.Timedelta(minutes=1))
        end = np.where(kept, np.minimum(nxt_clear, S + cap), S + NAIVE_LENGTH / pd.Timedelta(minutes=1))
        m = kept | naive
        out.append(pd.DataFrame({"region": region, "start": S[m], "end": end[m],
                                 "open": (kept & np.isinf(nxt_clear) & (S + cap > now))[m]}))
    if not out:
        return pd.DataFrame({"region": pd.Series(dtype=str), "start": pd.Series(dtype="datetime64[ns, UTC]"),
                             "end": pd.Series(dtype="datetime64[ns, UTC]"), "open": pd.Series(dtype=bool)})
    df = pd.concat(out, ignore_index=True)
    for c in ("start", "end"):
        df[c] = _EPOCH + pd.to_timedelta(df[c], unit="min")
    return df.sort_values(["region", "start"]).reset_index(drop=True)


def model_alerts(alerts: list[LiveAlert], now: pd.Timestamp, cleared: pd.DataFrame | None = None) -> pd.DataFrame:
    """Oblasts under alert at `now` in the training data's sense: columns region, start (UTC).

    The running alert of `oblast_intervals` built from the live records:
    * an air_raid record of any level counts (alerts are declared per raion);
    * the start is the latest activation in the oblast (the volunteer record's
      start is the last "alert" post);
    * no new activation for LIVE_EXPIRY_HOURS means the alert is over, so a
      month-long frontline raion/hromada record never makes an oblast active
      (and nothing ever exceeds the MAX_ALERT_HOURS cap of `load_alerts`);
    * `cleared` (columns region, end; optional): all-clears already seen for
      parts of the oblast (from polls or history). One after the latest
      activation ends the oblast alert, as in the volunteer file. Without it
      the rule can over-report but never invents an activation.
    """
    now = now.tz_convert("UTC")
    recs = to_frame([a for a in alerts if a.alert_type == "air_raid"])[["oblast", "started_at", "finished_at"]]
    if cleared is not None and len(cleared):
        recs = pd.concat([recs, pd.DataFrame({"oblast": cleared["region"].to_numpy(), "started_at": pd.NaT,
                                              "finished_at": pd.to_datetime(cleared["end"], utc=True).to_numpy()})],
                         ignore_index=True)
    iv = oblast_intervals(recs, now)
    iv = iv[iv["open"]]
    return iv[["region", "start"]].reset_index(drop=True)


KEY = ["uid", "started_at", "alert_type"]
POLL_GAP = pd.Timedelta(minutes=30)  # polls further apart than this break continuous coverage


def store_records(root: Path | str, as_of: pd.Timestamp, since: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Per-location records from the store as known at `as_of`, and how far they are complete.

    * history pulls (`alerts_history`, version known at `as_of`);
    * polls (`alerts_active`, observed_at <= as_of): a record seen in one poll
      and missing from the next is over by that next poll, the moment we
      knew it (no earlier end is invented).
    Returns (records with oblast, uid, started_at, finished_at, alert_type, level;
    `covered_until`): the store is complete from `since` up to `covered_until`
    (None if it does not cover `since`): through the oldest of the latest
    history pulls of all oblasts, then through polls without gaps > POLL_GAP.
    """
    from . import store  # local import: live_alerts must not need the store to parse payloads
    lo = since - pd.Timedelta(hours=MAX_ALERT_HOURS)
    hist = store.load(root, "alerts_history", as_of=as_of, since=lo)
    polls = store.load(root, "polls", as_of=as_of, since=lo)
    act = store.load(root, "alerts_active", as_of=as_of, since=lo)
    cols = ["oblast", "uid", "started_at", "finished_at", "alert_type", "level"]
    parts = []
    if len(hist):
        parts.append(hist[hist["started_at"] < as_of][cols])
    if len(act) and len(polls):
        t = pd.DatetimeIndex(pd.to_datetime(polls["observed_at"], utc=True).sort_values().unique())
        last = act.groupby(KEY, dropna=False).agg(oblast=("oblast", "last"), level=("level", "last"),
                                                   seen=("observed_at", "max")).reset_index()
        i = t.searchsorted(pd.DatetimeIndex(last["seen"]), side="right")  # first poll after the last sighting
        last["finished_at"] = t.append(pd.DatetimeIndex([pd.NaT], tz="UTC"))[i]
        parts.append(last[cols])
    rec = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    if len(rec):
        # prefer a known end (history) over none; then the earliest known end
        rec = rec.sort_values(["finished_at"], na_position="last").drop_duplicates(KEY, keep="first")
    # complete up to: the oldest of the latest pulls of every requested UID
    # (each pull holds ~30 days), if every oblast was pulled; then polls
    reach = None
    # when each UID was last pulled: the pull log, and the rows pulls wrote
    pulls = store.load(root, "history_pulls", as_of=as_of)
    raw = store.load(root, "alerts_history", as_of=as_of, raw=True)
    pulled = pd.concat([pulls[pulls["status"].isin([200, 304])][["uid", "fetched_at"]] if len(pulls) else None,
                        raw.rename(columns={"requested_uid": "uid"})[["uid", "fetched_at"]] if len(raw) else None,
                        pd.DataFrame(columns=["uid", "fetched_at"])])
    if len(pulled):
        pulled = pulled.groupby("uid")["fetched_at"].max()
        if {u for u, r in OBLAST_UID.items() if r not in ("Luhanska oblast",)} <= set(pulled.index) \
                and pulled.min() - pd.Timedelta(days=29) <= since:
            reach = pulled.min()
    if len(polls):
        t = pd.to_datetime(pd.Series(np.sort(polls["observed_at"].unique())), utc=True)
        run = (t.diff() > POLL_GAP).cumsum()
        for _, r in t.groupby(run):  # continuous runs of polls, in time order
            base = since if reach is None else reach
            if r.iloc[0] <= base + POLL_GAP and r.iloc[-1] > base:
                reach = r.iloc[-1]
    covered = reach
    return rec.reset_index(drop=True), covered


def after_dataset(intervals: pd.DataFrame, dataset: pd.DataFrame) -> pd.DataFrame:
    """Live-derived intervals (region, start, end, ...) that may be added to the dataset.

    The dataset is authoritative up to its end (its last start): intervals
    that were over by then are dropped, and no interval may overlap a dataset
    record of its region (its start is moved to the end of the region's last
    record if needed). What remains fills only the hole the volunteer file has
    at its end (it holds completed alerts only) and the time after it. A live
    record can never rewrite, extend or swallow history.
    """
    if not len(intervals):
        return intervals
    last_end = dataset.groupby("region")["end"].max()
    out = intervals[intervals["end"] > dataset["start"].max()].copy()
    c = out["region"].map(last_end)
    out["start"] = out["start"].where(c.isna() | (out["start"] >= c), c)
    return out[out["end"] > out["start"]].reset_index(drop=True)


def gap_alerts(records: pd.DataFrame | None, origin: pd.Timestamp, dataset: pd.DataFrame) -> pd.DataFrame:
    """Oblast-level alerts after the end of the dataset, for prediction features only.

    Built from per-location records known at `origin` with `oblast_intervals`
    and clipped by `after_dataset`. Never use them as training labels: alerts
    that started and ended between two polls are missing.
    """
    cols = ["region", "start", "end", "naive"]
    if records is None or not len(records):
        return pd.DataFrame(columns=cols)
    iv = oblast_intervals(records, origin)
    iv = after_dataset(iv[iv["region"].isin(set(dataset["region"]))], dataset)
    return iv.assign(naive=False)[cols]


def fetch(url: str, token: str, timeout: float = 20, if_modified_since: str | None = None) -> tuple[int, dict | None, dict]:
    """GET an alerts.in.ua endpoint: (status, json or None on 304, response headers).

    The token goes only into the Authorization header; it is never logged.
    """
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "air-raid-forecast-ua"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, dict(e.headers)
        raise


def fetch_active(token: str, url: str = ACTIVE_URL, timeout: float = 20) -> dict:
    return fetch(url, token, timeout)[1]
