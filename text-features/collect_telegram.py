"""Collect posts from public Telegram channels into JSONL (run on your own machine).

Telegram is not reachable from the cloud sandbox, so this script is meant to be
run locally with your own Telegram API credentials:

    export TG_API_ID=123456          # from https://my.telegram.org -> API development tools
    export TG_API_HASH=abcdef...     # never commit these
    pip install -r requirements.txt

    # one-off history backfill (resumable: re-running only fetches newer posts)
    python collect_telegram.py backfill --since 2024-01-01

    # live mode: records every new post with the moment we saw it
    python collect_telegram.py follow

Output: one file per channel, data/telegram/<channel>.jsonl, one JSON object per
line with fields:
    channel, id, date (UTC publish time), edit_date (UTC or null), text,
    views, fwd_from (channel id or null), collected_at (UTC), mode (backfill|live)

Leakage note: a backfilled post shows its *final* text. If a post was edited
later (e.g. "UPD: missiles heading to Kyiv"), that text was not known at
publish time. features.py handles this by treating such posts as available
only from `edit_date`. Posts deleted before the backfill are missing entirely;
live mode avoids both problems, so start `follow` as early as you can.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from channels import CHANNELS

OUT_DIR = Path(__file__).resolve().parent / "data" / "telegram"
# The session file holds your Telegram login. Keep it outside any git repo.
DEFAULT_SESSION = Path.home() / ".airraid_telegram"


def _client():
    from telethon import TelegramClient  # imported lazily so tests don't need it

    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("Set TG_API_ID and TG_API_HASH (https://my.telegram.org).")
    # In CI (GitHub Actions) there is no session file: pass the login as a
    # StringSession secret instead (generate once locally with
    # `python -c "from telethon.sessions import StringSession; ..."`, see README).
    if os.environ.get("TG_SESSION_STRING"):
        from telethon.sessions import StringSession

        session = StringSession(os.environ["TG_SESSION_STRING"])
    else:
        session = os.environ.get("TG_SESSION", str(DEFAULT_SESSION))
    client = TelegramClient(session, int(api_id), api_hash)
    client.flood_sleep_threshold = 120  # sleep through FloodWait up to 2 min
    return client


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _record(channel: str, msg, mode: str) -> dict:
    fwd = None
    if msg.fwd_from is not None and getattr(msg.fwd_from, "from_id", None) is not None:
        fwd = getattr(msg.fwd_from.from_id, "channel_id", None)
    return {
        "channel": channel,
        "id": msg.id,
        "date": _iso(msg.date),
        "edit_date": _iso(msg.edit_date),
        "text": msg.message or "",
        "views": msg.views,
        "fwd_from": fwd,
        "collected_at": _iso(datetime.now(timezone.utc)),
        "mode": mode,
    }


def _last_id(path: Path) -> int:
    last = 0
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    last = max(last, int(json.loads(line)["id"]))
                except (ValueError, KeyError):
                    continue
    return last


async def backfill(since: datetime, channels: list[str], budget_min: float | None = None) -> None:
    """Fetch posts newer than what we already have.

    `budget_min` caps the run time (split evenly across channels) so a scheduled
    CI job with a timeout still makes progress on the first, long backfill:
    each run stops cleanly and the next one resumes from the last stored id.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    per_channel = budget_min * 60 / len(channels) if budget_min else None
    async with _client() as client:
        for ch in channels:
            t_start = time.monotonic()
            path = OUT_DIR / f"{ch}.jsonl"
            min_id = _last_id(path)
            n = 0
            with path.open("a", encoding="utf-8") as f:
                # reverse=True walks oldest -> newest starting at offset_date.
                async for msg in client.iter_messages(
                    ch, reverse=True, offset_date=since, min_id=min_id, wait_time=1
                ):
                    if not msg.message:
                        continue  # photos without caption, service messages
                    f.write(json.dumps(_record(ch, msg, "backfill"), ensure_ascii=False) + "\n")
                    n += 1
                    if per_channel and time.monotonic() - t_start > per_channel:
                        print(f"  {ch}: time budget reached, will resume next run")
                        break
                    if n % 5000 == 0:
                        f.flush()
                        print(f"  {ch}: {n} posts, at {msg.date:%Y-%m-%d}")
            print(f"{ch}: +{n} posts -> {path}")


async def follow(channels: list[str]) -> None:
    from telethon import events, utils

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = _client()
    await client.start()
    entities = {}
    for ch in channels:
        ent = await client.get_entity(ch)
        entities[utils.get_peer_id(ent)] = ch

    @client.on(events.NewMessage(chats=list(entities)))
    async def handler(event):
        ch = entities.get(event.chat_id)
        if ch is None or not event.message.message:
            return
        rec = _record(ch, event.message, "live")
        with (OUT_DIR / f"{ch}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Following {len(entities)} channels. Ctrl+C to stop.")
    await client.run_until_disconnected()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--since", default="2024-01-01", help="UTC date, YYYY-MM-DD")
    b.add_argument("--channels", nargs="*", default=list(CHANNELS))
    b.add_argument("--budget-min", type=float, default=None, help="stop after this many minutes (CI)")
    f = sub.add_parser("follow")
    f.add_argument("--channels", nargs="*", default=list(CHANNELS))
    a = p.parse_args()
    if a.cmd == "backfill":
        since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
        asyncio.run(backfill(since, a.channels, a.budget_min))
    else:
        asyncio.run(follow(a.channels))


if __name__ == "__main__":
    main()
