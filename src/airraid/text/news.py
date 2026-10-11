"""Internet news with timestamps we can trust.

Live: RSS feeds of national outlets, polled by `collect update`. An item counts as
public from the moment we first saw it in the feed (first_seen_at); the claimed
publish time is stored but not trusted, because outlets backdate, re-time and
rewrite headlines. RSS keeps only the latest items, so it has no usable history.

History: the GDELT DOC 2.0 API (rolling ~3 months). Its `seendate` is when the
GDELT crawler saw the article, i.e. an upper bound on when it was public, so it is
used as first_seen_at. Requests are limited to one every 5 s.

Only items whose title or summary matches the attack lexicon are stored (launches,
drones/missiles heading somewhere, explosions, air defence at work), which keeps
the archive small.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd

from . import archive
from .lexicon import is_attack_news
from .telegram import get

# Checked 2026-10-11 from a cloud runner. Suspilne's feed answers 403 to bots.
RSS = {
    "ukrinform": "https://www.ukrinform.ua/rss/block-lastnews",
    "pravda": "https://www.pravda.com.ua/rss/view_news/",
    "unian": "https://rss.unian.net/site/news_ukr.rss",
    "rbc": "https://www.rbc.ua/static/rss/all.ukr.rss.xml",
}
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT matches keywords against its English translation of each article (a Cyrillic
# query returned nothing); titles come back in the original language.
GDELT_QUERY = '(drone OR drones OR missile OR missiles OR ballistic OR shahed OR "air raid" OR explosions) sourcelang:ukrainian'
GDELT_WAITS = (30, 60, 120)  # seconds to wait after HTTP 429 (shared runner IPs hit the limit)
TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str | None, n: int = 400) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", s or ""))).strip()[:n]


def parse_rss(xml: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title"), 300)
        desc = _clean(it.findtext("description"), 300)
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        pub = it.findtext("pubDate")
        try:
            date = pd.Timestamp(parsedate_to_datetime(pub)).tz_convert("UTC") if pub else pd.NaT
        except (TypeError, ValueError):
            date = pd.NaT
        if not link or not title:
            continue
        text = title if not desc or desc.startswith(title) else f"{title}\n{desc}"
        out.append({"source": source, "id": link, "date": date, "text": text, "edited": False})
    return out


def update_rss(root, sources: dict[str, str] | None = None, log=print) -> list[dict]:
    res = []
    for src, url in (sources or RSS).items():
        st = archive.read_state(root, "news", src)
        t0 = archive.now_utc()
        try:
            items = parse_rss(get(url, tries=3), src)
        except Exception as e:  # one feed failing must not stop the others
            st["last_error"] = f"{t0}: {e!r}"
            archive.write_state(root, "news", src, st)
            log(f"WARNING news {src}: {e!r}")
            continue
        df = pd.DataFrame(items, columns=["source", "id", "date", "text", "edited"])
        df = df[df["text"].map(is_attack_news)]
        df["seen_at"] = t0
        # the publish time is only a claim: never let it be later than our sighting
        df["date"] = pd.to_datetime(df["date"], utc=True).fillna(t0).clip(upper=t0)
        df["collected_mode"] = "live"
        n_new, n_again, _ = archive.upsert(root, "news", src, df)
        st.setdefault("complete_from", archive._iso(t0))
        st["complete_until"], st["last_run"], st["last_error"] = archive._iso(t0), archive._iso(t0), None
        st["items_in_feed"] = len(items)
        archive.write_state(root, "news", src, st)
        log(f"news {src}: {len(items)} items in feed, {len(df)} relevant, +{n_new} new")
        res.append({"source": src, "new": n_new})
    return res


def gdelt_window(start: pd.Timestamp, end: pd.Timestamp, query: str = GDELT_QUERY) -> list[dict]:
    q = {
        "query": query, "mode": "artlist", "format": "json", "maxrecords": "250", "sort": "dateasc",
        "startdatetime": f"{start:%Y%m%d%H%M%S}", "enddatetime": f"{end:%Y%m%d%H%M%S}",
    }
    url = f"{GDELT_URL}?{urllib.parse.urlencode(q)}"
    for wait in (*GDELT_WAITS, None):
        try:
            raw = get(url, tries=1)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)
    try:
        arts = json.loads(raw.decode("utf-8", "replace") or "{}").get("articles", [])
    except ValueError:
        raise RuntimeError(f"GDELT answered: {raw[:200]!r}")
    out = []
    for a in arts:
        seen = pd.Timestamp(a["seendate"]).tz_localize("UTC") if a.get("seendate") else None
        if seen is None or not a.get("url"):
            continue
        out.append({"source": "gdelt", "id": a["url"], "date": seen, "text": _clean(a.get("title"), 300),
                    "edited": False, "seen_at": seen, "domain": a.get("domain")})
    return out


def backfill_gdelt(root, days: float = 90, window_h: int = 3, max_minutes: float = 120, pause: float = 10,
                   log=print) -> dict:
    """Article lists in `window_h`-hour windows, oldest first, resumable via state.json."""
    st = archive.read_state(root, "news", "gdelt")
    now = archive.now_utc().floor("h")
    start = pd.Timestamp(st["complete_until"]) if st.get("complete_until") else now - pd.Timedelta(days=days)
    t_end = time.time() + max_minutes * 60
    n_total, n_win, saturated = 0, 0, st.get("saturated_windows", 0)
    while start < now - pd.Timedelta(hours=1) and time.time() < t_end:
        end = min(start + pd.Timedelta(hours=window_h), now)
        try:
            rows = gdelt_window(start, end)
        except Exception as e:
            st["last_error"] = repr(e)
            log(f"WARNING gdelt {start}: {e!r}")
            break
        if len(rows) >= 250:
            saturated += 1
        df = pd.DataFrame(rows, columns=["source", "id", "date", "text", "edited", "seen_at", "domain"])
        df = df[df["text"].map(is_attack_news)]
        df["first_seen_at"] = df["seen_at"]
        df["collected_mode"] = "gdelt"
        n_new, _, _ = archive.upsert(root, "news", "gdelt", df.drop(columns="domain"))
        n_total += n_new
        n_win += 1
        st.setdefault("complete_from", archive._iso(start))
        st["complete_until"] = archive._iso(end)
        st["saturated_windows"] = saturated
        archive.write_state(root, "news", "gdelt", st)
        if n_win % 24 == 0:
            log(f"gdelt: at {end}, +{n_total} items, {saturated} saturated windows")
        start = end
        time.sleep(pause)
    st["last_run"] = archive._iso(archive.now_utc())
    archive.write_state(root, "news", "gdelt", st)
    log(f"gdelt: {n_win} windows, +{n_total} items, complete {st.get('complete_from')} .. {st.get('complete_until')}")
    return {"windows": n_win, "new": n_total}
