"""Collect text signals into the point-in-time archive (the `text-data` branch).

    PYTHONPATH=src python -m airraid.text.collect update   --root text-data          # hourly, < 1 min
    PYTHONPATH=src python -m airraid.text.collect backfill --root text-data --since 2025-01-01
    PYTHONPATH=src python -m airraid.text.collect gdelt    --root text-data --days 90  # news history
    PYTHONPATH=src python -m airraid.text.collect import-snapshot --root text-data \
        --channel kpszsu --parquet tg_kpszsu.parquet --seen-at 2026-10-11T10:29:00Z --complete-from 2025-01-01
    PYTHONPATH=src python -m airraid.text.collect status   --root text-data

`update` is idempotent: it fetches only posts newer than the stored ones (plus the
newest page again to notice edits) and the current RSS items, and appends them.
Public sources only; no account or key is needed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from . import archive, news, telegram


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cmd_update(a) -> int:
    root = Path(a.root)
    t = time.time()
    errors = 0
    for ch in a.channels:
        try:
            telegram.update(root, ch, max_pages=a.max_pages, log=_log)
        except Exception as e:  # keep going: one channel down must not block the rest
            errors += 1
            st = archive.read_state(root, "telegram", ch)
            st["last_error"] = f"{archive.now_utc()}: {e!r}"
            archive.write_state(root, "telegram", ch, st)
            _log(f"ERROR {ch}: {e!r}")
    if not a.no_news:
        news.update_rss(root, log=_log)
    _log(f"update done in {time.time() - t:.0f}s, {errors} channel errors")
    return 1 if errors == len(a.channels) else 0


def cmd_backfill(a) -> int:
    root = Path(a.root)
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        futs = {ch: ex.submit(telegram.backfill, root, ch, a.since, a.max_minutes, log=_log) for ch in a.channels}
        for ch, f in futs.items():
            try:
                _log(json.dumps(f.result(), default=str))
            except Exception as e:  # one channel failing must not stop the others
                errors += 1
                _log(f"ERROR {ch}: {e!r}")
    return 1 if errors else 0


def cmd_gdelt(a) -> int:
    news.backfill_gdelt(Path(a.root), days=a.days, window_h=a.window_hours, max_minutes=a.max_minutes, log=_log)
    return 0


def cmd_import(a) -> int:
    df = pd.read_parquet(a.parquet)
    res = telegram.import_snapshot(Path(a.root), a.channel, df, a.seen_at, a.complete_from)
    _log(json.dumps(res))
    return 0


def status(root: Path) -> pd.DataFrame:
    rows = []
    for kind in ("telegram", "news"):
        base = root / kind
        if not base.exists():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            st = archive.read_state(root, kind, d.name)
            r = archive.read_records(root, kind, d.name)
            rows.append({
                "kind": kind, "source": d.name, "items": len(r),
                "first": r["date"].min(), "last": r["date"].max(),
                "complete_from": st.get("complete_from"), "complete_until": st.get("complete_until"),
                "gaps": len(st.get("gaps", [])),
                "edited_first_share": round(float(r["edited_first"].mean()), 4) if len(r) else None,
                "edited_seen_share": round(float(r["edited_seen"].mean()), 4) if len(r) else None,
                "live_share": round(float((r["collected_mode"] == "live").mean()), 4) if len(r) else None,
                "last_error": st.get("last_error"),
            })
    return pd.DataFrame(rows)


def cmd_status(a) -> int:
    df = status(Path(a.root))
    print(df.to_string(index=False) if len(df) else "empty archive")
    if a.json:
        Path(a.json).write_text(df.to_json(orient="records", date_format="iso", indent=1))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m airraid.text.collect", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="fetch new posts and news (hourly)")
    u.add_argument("--root", required=True)
    u.add_argument("--channels", nargs="*", default=list(telegram.CHANNELS))
    u.add_argument("--max-pages", type=int, default=150)
    u.add_argument("--no-news", action="store_true")
    u.set_defaults(fn=cmd_update)
    b = sub.add_parser("backfill", help="history from the web preview (resumable)")
    b.add_argument("--root", required=True)
    b.add_argument("--since", default="2025-01-01")
    b.add_argument("--channels", nargs="*", default=telegram.FEATURE_CHANNELS)
    b.add_argument("--max-minutes", type=float, default=60)
    b.add_argument("--parallel", type=int, default=3, help="channels fetched at once (1 request/s each)")
    b.set_defaults(fn=cmd_backfill)
    g = sub.add_parser("gdelt", help="news history from GDELT (rolling ~3 months)")
    g.add_argument("--root", required=True)
    g.add_argument("--days", type=float, default=90)
    g.add_argument("--window-hours", type=int, default=3)
    g.add_argument("--max-minutes", type=float, default=120)
    g.set_defaults(fn=cmd_gdelt)
    i = sub.add_parser("import-snapshot", help="import an earlier t.me/s scrape (parquet)")
    i.add_argument("--root", required=True)
    i.add_argument("--channel", required=True)
    i.add_argument("--parquet", required=True)
    i.add_argument("--seen-at", required=True, help="UTC time by which the scrape had happened")
    i.add_argument("--complete-from", required=True)
    i.set_defaults(fn=cmd_import)
    s = sub.add_parser("status", help="coverage and edited share per source")
    s.add_argument("--root", required=True)
    s.add_argument("--json", default=None)
    s.set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
