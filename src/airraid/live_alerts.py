"""alerts.in.ua payloads: parsing and the training-consistent oblast-level alert.

Single source of truth for
  * the oblast-name map (alerts.in.ua Ukrainian titles / UIDs -> dataset region names);
  * parsing `/v1/alerts/active.json` and `/v1/regions/{uid}/alerts/month_ago.json`
    into typed `LiveAlert` records (every level, every alert type);
  * `model_alerts`: which live records count as "an alert in the oblast" in the
    sense of the training data (the volunteer eTryvoga file, see data.py).

Why the definition is not "any alert anywhere in the oblast"
------------------------------------------------------------
alerts.in.ua keeps raion/hromada alerts open for weeks on the front line
(Kharkivska "since 2026-03-10" was seen live). The training data never contains
such alerts: every record is capped at MAX_ALERT_HOURS by `load_alerts`. Taking
the earliest start of any active record therefore produced "under alert for
months" in live features (frac_24h = 1.0, elapsed far beyond anything seen in
training) and, before the fix, also leaked into the training grid.

The rule below was chosen by comparing candidate definitions with the volunteer
file over its overlap with the official raion-level file (scripts/
live_definition_check.py; numbers in the README):
  * oblast-level records alone are wrong: since December 2025 the official
    system declares alerts per raion; outside Kyiv City there are no
    oblast-level records at all, while the volunteer file has 3-14 alerts a
    day per oblast;
  * the volunteer file records "an alert in the oblast" from the most recent
    activation message to the next all-clear message for any part of the
    oblast (97 % of its ends coincide with an official all-clear in the same
    oblast within 3 min in Kharkivska), so its alert starts are the *latest*
    activation and records older than 12 h do not exist.
So: an oblast is under alert when it has an `air_raid` record of any level that
started less than MAX_ALERT_HOURS ago (the same cap `load_alerts` applies, so an
alert older than that is treated as over) and no all-clear for a part of the
oblast has been seen since; the alert's start is the latest such activation.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


def model_alerts(alerts: list[LiveAlert], now: pd.Timestamp, cleared: pd.DataFrame | None = None) -> pd.DataFrame:
    """Oblasts under alert at `now` in the training data's sense: columns region, start (UTC).

    * only `air_raid` records that started before `now` and less than
      MAX_ALERT_HOURS ago (older ones are over by the same rule load_alerts
      applies to the history, so month-long frontline records never count);
    * any level counts (since Dec 2025 alerts are declared per raion);
    * the start is the latest such activation in the oblast, like the
      volunteer record whose start is the last "alert" message;
    * `cleared` (columns region, end; optional) lists all-clears already seen
      for parts of the oblast (from polls or history). An all-clear after the
      latest activation ends the oblast-level alert, as in the volunteer file.
      Without it the rule can only over-report, never invent a start.
    """
    now = now.tz_convert("UTC")
    cap = pd.Timedelta(hours=MAX_ALERT_HOURS)
    df = to_frame([a for a in alerts if a.alert_type == "air_raid"])
    df = df[(df["started_at"] < now) & (df["started_at"] > now - cap)]
    df = df[df["finished_at"].isna() | (df["finished_at"] > now)]
    out = df.groupby("oblast", as_index=False)["started_at"].max().rename(columns={"oblast": "region", "started_at": "start"})
    if cleared is not None and len(cleared) and len(out):
        c = cleared[cleared["end"] <= now].groupby("region")["end"].max()
        out = out[~(out["region"].map(c) > out["start"])]
    return out.sort_values("region").reset_index(drop=True)


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
