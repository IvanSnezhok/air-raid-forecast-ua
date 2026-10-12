"""Optional: collect Telegram posts through the Telegram API (Telethon) instead of the web preview.

The default collector needs no account: `python -m airraid.text.collect update`
reads the public web preview (t.me/s). This script is the alternative for when
the web preview is blocked or a channel hides it. It writes into the same
point-in-time archive (src/airraid/text/archive.py), so features, tests and the
live forecast do not care which collector saw a post.

    export TG_API_ID=123456          # from https://my.telegram.org -> API development tools
    export TG_API_HASH=abcdef...     # never commit these
    pip install -r text-features/requirements.txt

    # history (resumable: re-running only fetches older posts than stored)
    PYTHONPATH=src python text-features/collect_telegram.py backfill --root text-data --since 2025-01-01
    # live: records every new post with the moment we saw it
    PYTHONPATH=src python text-features/collect_telegram.py follow --root text-data

In CI there is no session file: put a StringSession in the TG_SESSION_STRING secret
(see README). The string gives full access to the account; never print or commit it.

Edits: a message that already carries an edit_date when we first see it is stored
as "edited when first seen", so it counts only from the moment we saw it (the
same rule as the web preview; the exact edit_date is kept in the log line but not
used, to keep one rule for every collector).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from airraid.text import archive  # noqa: E402
from airraid.text.telegram import CHANNELS  # noqa: E402

DEFAULT_SESSION = Path.home() / ".airraid_telegram"  # keep the login outside any git repo


def _client():
    from telethon import TelegramClient  # imported lazily so tests don't need it

    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("Set TG_API_ID and TG_API_HASH (https://my.telegram.org).")
    if os.environ.get("TG_SESSION_STRING"):
        from telethon.sessions import StringSession

        session = StringSession(os.environ["TG_SESSION_STRING"])
    else:
        session = os.environ.get("TG_SESSION", str(DEFAULT_SESSION))
    client = TelegramClient(session, int(api_id), api_hash)
    client.flood_sleep_threshold = 120
    return client


def _obs(channel: str, msgs: list) -> pd.DataFrame:
    seen = pd.Timestamp(datetime.now(timezone.utc)).floor("s")
    return pd.DataFrame([{
        "channel": channel, "id": int(m.id), "date": pd.Timestamp(m.date).tz_convert("UTC"),
        "edited": m.edit_date is not None, "text": m.message or "", "seen_at": seen,
    } for m in msgs], columns=["channel", "id", "date", "edited", "text", "seen_at"])


async def backfill(root: Path, since: datetime, channels: list[str], budget_min: float | None = None) -> None:
    per_channel = budget_min * 60 / len(channels) if budget_min else None
    async with _client() as client:
        for ch in channels:
            st = archive.read_state(root, "telegram", ch)
            t_start, batch, n = time.monotonic(), [], 0
            # newest -> oldest, starting below the oldest stored id
            async for msg in client.iter_messages(ch, offset_id=st.get("min_id") or 0, wait_time=1):
                if msg.date < since:
                    st["complete_from"] = archive._iso(pd.Timestamp(since))
                    break
                batch.append(msg)
                if len(batch) >= 2000 or (per_channel and time.monotonic() - t_start > per_channel):
                    n += archive.upsert(root, "telegram", ch, _obs(ch, batch))[0]
                    st["min_id"] = int(min(m.id for m in batch))
                    st["complete_from"] = archive._iso(pd.Timestamp(min(m.date for m in batch)))
                    batch = []
                    if per_channel and time.monotonic() - t_start > per_channel:
                        break
            if batch:
                n += archive.upsert(root, "telegram", ch, _obs(ch, batch))[0]
                st["min_id"] = int(min(m.id for m in batch))
            st.setdefault("max_id", st.get("min_id"))
            st.setdefault("complete_until", archive._iso(archive.now_utc()))
            archive.write_state(root, "telegram", ch, st)
            print(f"{ch}: +{n} posts, complete from {st.get('complete_from')}")


async def follow(root: Path, channels: list[str]) -> None:
    from telethon import events, utils

    client = _client()
    await client.start()
    entities = {}
    for ch in channels:
        entities[utils.get_peer_id(await client.get_entity(ch))] = ch

    @client.on(events.NewMessage(chats=list(entities)))
    async def handler(event):
        ch = entities.get(event.chat_id)
        if ch is None:
            return
        archive.upsert(root, "telegram", ch, _obs(ch, [event.message]))
        st = archive.read_state(root, "telegram", ch)
        st["max_id"] = max(int(event.message.id), st.get("max_id") or 0)
        archive.write_state(root, "telegram", ch, st)

    print(f"Following {len(entities)} channels. Ctrl+C to stop. Run `collect update` now and then to "
          "refresh coverage (complete_until) and catch anything missed while disconnected.")
    await client.run_until_disconnected()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--root", required=True)
    b.add_argument("--since", default="2025-01-01", help="UTC date, YYYY-MM-DD")
    b.add_argument("--channels", nargs="*", default=list(CHANNELS))
    b.add_argument("--budget-min", type=float, default=None, help="stop after this many minutes (CI)")
    f = sub.add_parser("follow")
    f.add_argument("--root", required=True)
    f.add_argument("--channels", nargs="*", default=list(CHANNELS))
    a = p.parse_args()
    if a.cmd == "backfill":
        since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
        asyncio.run(backfill(Path(a.root), since, a.channels, a.budget_min))
    else:
        asyncio.run(follow(Path(a.root), a.channels))


if __name__ == "__main__":
    main()
