"""Fetch the raw material for threat-type labels. Runs in GitHub Actions.

Two public sources, neither reachable from the cloud sandbox we develop in, so
the workflow `.github/workflows/threat-labels.yml` runs this script and commits
the output to the `threat-labels-data` branch:

1. Air Force channel history from the public web preview https://t.me/s/kpszsu
   (no Telegram account needed). Each post has its publication time and the
   final (possibly edited) text. Posts like "Загроза застосування балістичного
   озброєння", "Ударні БпЛА на Сумщині" or "Ракета курсом на Київ" say which
   threat caused an alert in which oblast.
2. Kaggle "Massive Missile Attacks on Ukraine" (piterfm, built from the Air
   Force morning reports): per attack, time window, weapon model, launched
   count. Used to tell a massed drone attack from a few drones.

    python threat-type/fetch_sources.py --since 2025-01-01 --out data
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
KAGGLE_URL = "https://www.kaggle.com/api/v1/datasets/download/piterfm/massive-missile-attacks-on-ukraine"


def get(url: str, tries: int = 6) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2 ** i * 5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(2 ** i * 5)
                continue
            raise
    raise RuntimeError("unreachable")


def parse_page(html: str, channel: str) -> list[dict]:
    """Posts on one t.me/s/<channel> page: id, UTC time, edited flag, text."""
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


def scrape_channel(channel: str, since: pd.Timestamp, max_minutes: float, pause: float = 1.0) -> pd.DataFrame:
    """Walk the web preview backwards until `since` (or the time budget runs out)."""
    t_end = time.time() + max_minutes * 60
    rows: list[dict] = []
    before = None
    while time.time() < t_end:
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        page = parse_page(get(url).decode("utf-8", "replace"), channel)
        if not page:
            break
        rows += page
        oldest = min(p["id"] for p in page)
        if before is not None and oldest >= before:
            break
        before = oldest
        if min(p["date"] for p in page) < since:
            break
        if len(rows) % 1000 < len(page):
            print(f"{channel}: {len(rows)} posts, back to {min(p['date'] for p in page)}", flush=True)
        time.sleep(pause)
    df = pd.DataFrame(rows).drop_duplicates("id").sort_values("id")
    return df[df["date"] >= since].reset_index(drop=True)


def fetch_kaggle(out: Path) -> list[str]:
    raw = get(KAGGLE_URL)
    names = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            if n.endswith(".csv"):
                (out / Path(n).name).write_bytes(z.read(n))
                names.append(Path(n).name)
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01")
    ap.add_argument("--channels", default="kpszsu")
    ap.add_argument("--out", default="data")
    ap.add_argument("--max-minutes", type=float, default=150)
    ap.add_argument("--existing", default=None, help="earlier tg_<channel>.parquet dir: only fetch newer posts")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"fetched_at_utc": str(pd.Timestamp.now(tz="UTC")), "since": a.since}

    try:
        report["kaggle_files"] = fetch_kaggle(out)
    except Exception as e:  # keep going: the Telegram history is the main source
        report["kaggle_error"] = repr(e)
    print(json.dumps(report), flush=True)

    since = pd.Timestamp(a.since, tz="UTC")
    for ch in a.channels.split(","):
        old_path = Path(a.existing or "-") / f"tg_{ch}.parquet"
        old = pd.read_parquet(old_path) if old_path.exists() else None
        # re-read the last 2 days of an existing history to pick up edits
        start = since if old is None else max(since, old["date"].max() - pd.Timedelta(days=2))
        try:
            df = scrape_channel(ch, start, a.max_minutes)
        except Exception as e:
            report[f"{ch}_error"] = repr(e)
            continue
        if old is not None:
            df = pd.concat([old, df], ignore_index=True).drop_duplicates("id", keep="last").sort_values("id")
            df = df[df["date"] >= since].reset_index(drop=True)
        df.to_parquet(out / f"tg_{ch}.parquet", index=False)
        report[ch] = {"posts": len(df), "first": str(df["date"].min()), "last": str(df["date"].max())}
        print(json.dumps(report[ch]), flush=True)
        print(df.sample(min(15, len(df)), random_state=0)[["date", "text"]].to_string()[:4000], flush=True)
    (out / "fetch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
