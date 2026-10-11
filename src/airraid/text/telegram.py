"""Public Telegram channels through the t.me/s web preview (no account, no API keys).

Each page of https://t.me/s/<channel>?before=<id> lists the ~20 posts before that
id with their publish time, an "edited" mark and the current text. We record every
sighting in the point-in-time archive (see archive.py): the first text we saw is
never overwritten, so features can use exactly what was public at the time.

    update    newest pages until we reach posts we already have (fast, hourly)
    backfill  walk back from the oldest stored post to a date (once, resumable)
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request

import pandas as pd

from . import archive

UA = "air-raid-forecast-ua/0.2 (research; +https://github.com/IvanSnezhok/air-raid-forecast-ua)"

# Public channels that post real-time air threat updates (checked 2026-10-11).
# "features": used by the model. A channel joins the model only when its history
# can be timestamped leak-free (see README); the others are collected live so a
# history builds up.
CHANNELS = {
    "kpszsu": {"name": "Повітряні сили ЗСУ (official)", "features": True},
    "deraketaua": {"name": "Де ракета (volunteer radar; strategic aviation status, launches)", "features": True},
    "monitorwarr": {"name": "Монітор (volunteer radar; drone tracks)", "features": True},
    "war_monitor": {"name": "war_monitor (volunteer; ballistic threats, launches)", "features": True},
    # nearly every post is edited seconds after publishing (a footer is added), so the
    # web preview cannot tell when its text appeared: live collection only
    "raketa_trevoga": {"name": "Ракетна тривога (mirror of the Air Force posts)", "features": False},
}
FEATURE_CHANNELS = [c for c, v in CHANNELS.items() if v["features"]]
PAUSE_S = 1.0  # between page requests to one channel


def get(url: str, tries: int = 5, ua: str = UA) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if i < tries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def parse_page(html: str, channel: str) -> list[dict]:
    """Posts on one t.me/s/<channel> page: id, UTC publish time, edited mark, text."""
    from bs4 import BeautifulSoup  # only the collector needs it

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for m in soup.select("div.tgme_widget_message[data-post]"):
        post = m["data-post"]
        if not post.lower().startswith(channel.lower() + "/"):
            continue
        t = m.select_one("a.tgme_widget_message_date time[datetime]") or m.select_one("time[datetime]")
        if t is None:
            continue
        txt = m.select_one("div.tgme_widget_message_text")
        if txt:
            for br in txt.find_all("br"):
                br.replace_with("\n")
        text = txt.get_text("").strip() if txt else ""
        meta = m.select_one("span.tgme_widget_message_meta")
        edited = bool(meta and "edited" in meta.get_text(" ", strip=True).lower())
        out.append({
            "channel": channel,
            "id": int(post.split("/")[1]),
            "date": pd.Timestamp(t["datetime"]).tz_convert("UTC"),
            "edited": edited,
            "text": text,
        })
    return out


def fetch_page(channel: str, before: int | None = None, tries: int = 5) -> list[dict]:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    seen_at = archive.now_utc()
    page = parse_page(get(url, tries=tries).decode("utf-8", "replace"), channel)
    for p in page:
        p["seen_at"] = seen_at
    return page


def _obs(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["channel", "id", "date", "edited", "text", "seen_at"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["seen_at"] = pd.to_datetime(df["seen_at"], utc=True)
    return df


def update(root, channel: str, max_pages: int = 150, pause: float = PAUSE_S, log=print) -> dict:
    """Fetch posts newer than the newest stored one (plus the newest page again, for edits).

    Coverage moves forward to the moment the newest page was fetched only if the walk
    reached posts we already had. Otherwise the hole is recorded as a gap.
    """
    st = archive.read_state(root, "telegram", channel)
    known = st.get("max_id")
    t0 = archive.now_utc()
    rows, before, pages, connected = [], None, 0, known is None
    while pages < max_pages:
        page = fetch_page(channel, before)
        pages += 1
        if not page:
            break
        rows += page
        lo = min(p["id"] for p in page)
        if known is not None and lo <= known:
            connected = True
            break
        if before is not None and lo >= before:
            break
        before = lo
        if known is None:
            break  # a new channel: start coverage from the newest page, backfill extends it
        time.sleep(pause)
    obs = _obs(rows)
    n_new, n_again, months = archive.upsert(root, "telegram", channel, obs)
    if len(obs):
        st["max_id"] = int(max(obs["id"].max(), known or 0))
        if st.get("complete_from") is None:
            st["complete_from"] = archive._iso(obs["date"].min())
        if not connected:
            st.setdefault("gaps", []).append([st.get("complete_until"), archive._iso(obs["date"].min())])
            log(f"WARNING {channel}: did not reach stored posts within {max_pages} pages; gap recorded")
        st["complete_until"] = archive._iso(t0)
    st["last_run"] = archive._iso(t0)
    st["last_error"] = None
    archive.write_state(root, "telegram", channel, st)
    log(f"{channel}: {pages} pages, +{n_new} new, {n_again} seen again, months {months}")
    return {"channel": channel, "pages": pages, "new": n_new, "again": n_again}


def backfill(root, channel: str, since: str, max_minutes: float = 60, pause: float = PAUSE_S, log=print) -> dict:
    """Walk back from the oldest stored post (or from the newest post) to `since`."""
    since_ts = pd.Timestamp(since, tz="UTC")
    st = archive.read_state(root, "telegram", channel)
    if st.get("min_id") is None and st.get("max_id") is None:
        update(root, channel, max_pages=1, pause=pause, log=log)
        st = archive.read_state(root, "telegram", channel)
        recs = archive.read_records(root, "telegram", channel)
        st["min_id"] = int(recs["id"].min())
    before = st.get("min_id") or int(archive.read_records(root, "telegram", channel)["id"].min())
    t_end = time.time() + max_minutes * 60
    rows, pages, reached, error = [], 0, False, None
    oldest = pd.Timestamp(st["complete_from"]) if st.get("complete_from") else None
    try:
        while time.time() < t_end:
            page = fetch_page(channel, before, tries=8)
            pages += 1
            if not page:
                reached = True  # start of the channel
                break
            lo = min(p["id"] for p in page)
            if lo >= before:
                break
            rows += page
            before = lo
            oldest = min(p["date"] for p in page)
            if oldest < since_ts:
                reached = True
                break
            if len(rows) >= 2000:  # flush now and then so an interrupted run keeps its work
                archive.upsert(root, "telegram", channel, _obs(rows))
                st["min_id"], st["complete_from"] = int(before), archive._iso(oldest)
                archive.write_state(root, "telegram", channel, st)
                log(f"{channel}: back to {oldest}")
                rows = []
            time.sleep(pause)
    except Exception as e:  # keep what we have; the next run resumes from min_id
        error = e
    n_new, _, _ = archive.upsert(root, "telegram", channel, _obs(rows))
    st["min_id"] = int(before)
    st["complete_from"] = archive._iso(since_ts if reached else oldest)
    st["last_error"] = None if error is None else f"{archive.now_utc()}: backfill {error!r}"
    archive.write_state(root, "telegram", channel, st)
    if error is not None:
        log(f"ERROR {channel}: backfill stopped at {oldest} ({error!r}); run it again to resume")
    log(f"{channel}: backfill {pages} pages, +{n_new}, complete from {st['complete_from']} (reached={reached})")
    return {"channel": channel, "pages": pages, "new": n_new, "complete_from": st["complete_from"]}


def import_snapshot(root, channel: str, df: pd.DataFrame, seen_at: str, complete_from: str) -> dict:
    """Import an earlier scrape (e.g. the `threat-labels-data` parquet: channel, id, date, edited, text).

    `seen_at` must be a time by which that scrape had certainly happened (e.g. the
    commit time of the file): it becomes first_seen_at, and the text and edited mark in
    the file are what was public at that moment.
    """
    obs = df[["id", "date", "edited", "text"]].copy()
    obs["channel"] = channel
    obs["date"] = pd.to_datetime(obs["date"], utc=True)
    obs["seen_at"] = pd.Timestamp(seen_at).tz_convert("UTC")
    obs["collected_mode"] = "backfill"
    n_new, n_again, _ = archive.upsert(root, "telegram", channel, obs)
    st = archive.read_state(root, "telegram", channel)
    st["min_id"] = int(min(obs["id"].min(), st.get("min_id") or 10**12))
    st["max_id"] = int(max(obs["id"].max(), st.get("max_id") or 0))
    st["complete_from"] = archive._iso(pd.Timestamp(complete_from, tz="UTC"))
    # everything published before the newest post in the snapshot was in it
    newest = obs["date"].max()
    if st.get("complete_until") is None or pd.Timestamp(st["complete_until"]) < newest:
        st["complete_until"] = archive._iso(newest)
    st["imported_from"] = f"snapshot seen by {seen_at}"
    archive.write_state(root, "telegram", channel, st)
    return {"channel": channel, "new": n_new, "again": n_again}
