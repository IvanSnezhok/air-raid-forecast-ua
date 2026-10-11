"""Collect alerts.in.ua data into the point-in-time store (see store.py).

    export ALERTS_IN_UA_TOKEN=...           # never commit it
    python -m airraid.collect poll    --root live-data
    python -m airraid.collect history --root live-data [--scope auto|oblast|raion] [--max-requests N]

`poll`    one `/v1/alerts/active.json` request -> `polls` (one row) and
          `alerts_active` (every record, all levels and alert types, with
          `observed_at`).
`history` `/v1/regions/{uid}/alerts/month_ago.json` for every oblast UID (and
          raion UIDs, see --scope) -> `alerts_history` with `fetched_at`.
          The API keeps only about 30 days and allows 2 history requests a
          minute, so requests are spaced by --sleep seconds. Resume-safe: UIDs
          fetched less than --min-age-h ago are skipped, so the workflow can run
          it in chunks and commit between them. Uses If-Modified-Since.

The token is read from the environment only and is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

import pandas as pd

from . import store
from .live_alerts import ACTIVE_URL, API, OBLAST_UID, fetch, locations, parse_alerts, to_frame

SKIP_OBLASTS = {"Luhanska oblast"}  # permanent alert since 2022, excluded from the model
STATE = "_state/history.json"
EXTRA = ("alert_level", "notes", "threats", "updated_at", "location_title_en", "calculated")


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").floor("s")


def _token() -> str:
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    if not token:
        sys.exit("ALERTS_IN_UA_TOKEN is not set")
    return token


def records(payload: dict) -> pd.DataFrame:
    """Parsed records plus the raw fields we do not model yet (threat types, notes) as JSON."""
    df = to_frame(parse_alerts(payload))
    raw = {str(a.get("id")): {k: a[k] for k in EXTRA if k in a} for a in payload.get("alerts") or []}
    df["extra"] = [json.dumps(raw.get(str(i), {}), ensure_ascii=False) for i in df["id"]]
    return df


def poll(root: Path, token: str) -> pd.DataFrame:
    t = _now()
    status, payload, _ = fetch(ACTIVE_URL, token)
    df = records(payload).assign(observed_at=t)
    store.append(root, "alerts_active", df)
    meta = pd.DataFrame([{
        "observed_at": t, "status": status, "n_records": len(df),
        "n_air_raid": int((df["alert_type"] == "air_raid").sum()),
        "levels": json.dumps(df["level"].value_counts().to_dict()),
    }])
    store.append(root, "polls", meta)
    print(f"poll {t:%Y-%m-%d %H:%M:%S}Z: {len(df)} active records, levels {meta.at[0, 'levels']}")
    return df


def _load_state(root: Path) -> dict:
    p = root / STATE
    return json.loads(p.read_text()) if p.exists() else {"uids": {}}


def _save_state(root: Path, state: dict) -> None:
    p = root / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, sort_keys=True))


def history_uids(scope: str, state: dict) -> list[str]:
    obl = [u for u, r in OBLAST_UID.items() if r not in SKIP_OBLASTS]
    loc = locations()
    raions = loc[(loc["level"] == "raion") & (loc["oblast"] != "") & ~loc["oblast"].isin(SKIP_OBLASTS)]["uid"].tolist()
    if scope == "oblast":
        return obl
    if scope == "raion":
        return obl + raions
    # auto: raions only if the oblast history turned out not to contain raion/hromada records
    has = state.get("oblast_history_has_subregions")
    return obl + (raions if has is False else [])


def _name(uid: str) -> str:
    loc = locations()
    return OBLAST_UID.get(uid) or (loc.at[uid, "title"] if uid in loc.index else uid)


def history(root: Path, token: str, scope: str = "auto", max_requests: int | None = None,
            min_age_h: float = 20, sleep_s: float = 31) -> int:
    state = _load_state(root)
    now = _now()
    n_req, failed = 0, set()
    oblasts = [u for u, r in OBLAST_UID.items() if r not in SKIP_OBLASTS]
    while True:
        if state.get("oblast_history_has_subregions") is None and all(
                state["uids"].get(u, {}).get("n") is not None for u in oblasts):
            state["oblast_history_has_subregions"] = False  # every oblast answered, none had raion records
        uids = history_uids(scope, state)
        due = [u for u in uids if u not in failed and now - pd.Timestamp(
            state["uids"].get(u, {}).get("fetched_at", "2000-01-01T00:00Z")) > pd.Timedelta(hours=min_age_h)]
        due.sort(key=lambda u: state["uids"].get(u, {}).get("fetched_at", ""))
        if not due or (max_requests is not None and n_req >= max_requests):
            break
        uid = due[0]
        # history endpoint: 2 requests per minute, also across chunked runs
        last = state.get("last_request_at")
        if last is not None:
            time.sleep(max(0.0, sleep_s - (pd.Timestamp.now(tz="UTC") - pd.Timestamp(last)).total_seconds()))
        prev = state["uids"].get(uid, {})
        t = _now()
        state["last_request_at"] = str(pd.Timestamp.now(tz="UTC"))
        try:
            status, payload, headers = fetch(f"{API}/v1/regions/{uid}/alerts/month_ago.json", token, timeout=60,
                                             if_modified_since=prev.get("last_modified"))
        except urllib.error.HTTPError as e:
            n_req += 1
            print(f"history uid={uid}: HTTP {e.code}")
            if e.code == 429:
                time.sleep(65)
                continue
            failed.add(uid)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            n_req += 1
            print(f"history uid={uid}: {type(e).__name__}")
            failed.add(uid)
            continue
        n_req += 1
        entry = {**prev, "fetched_at": str(t), "status": status, "last_modified": headers.get("Last-Modified")}
        if status == 304:
            print(f"history uid={uid}: not modified")
        else:
            df = records(payload).assign(fetched_at=t, requested_uid=uid)
            written = store.append(root, "alerts_history", df)
            levels = df["level"].value_counts().to_dict()
            entry.update(n=len(df), levels=levels, first=str(df["started_at"].min()) if len(df) else None)
            print(f"history uid={uid} ({_name(uid)}): "
                  f"{len(df)} records {levels}, from {entry['first']}, {'new rows written' if written else 'nothing new'}")
            if uid in OBLAST_UID and any(lv not in ("oblast", "city") for lv in levels):
                state["oblast_history_has_subregions"] = True
        state["uids"][uid] = entry
        _save_state(root, state)
    print(f"history: {n_req} requests; oblast history has raion/hromada records: "
          f"{state.get('oblast_history_has_subregions')}")
    return n_req


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--root", type=Path, required=True)
    h = sub.add_parser("history")
    h.add_argument("--root", type=Path, required=True)
    h.add_argument("--scope", choices=("auto", "oblast", "raion"), default="auto",
                   help="auto: raion UIDs too, unless oblast histories already contain raion/hromada records")
    h.add_argument("--max-requests", type=int, default=None)
    h.add_argument("--min-age-h", type=float, default=20, help="skip UIDs fetched more recently than this")
    h.add_argument("--sleep", type=float, default=31, help="seconds between history requests (limit: 2/min)")
    args = ap.parse_args(argv)
    token = _token()
    if args.cmd == "poll":
        poll(args.root, token)
    else:
        history(args.root, token, args.scope, args.max_requests, args.min_age_h, args.sleep)


if __name__ == "__main__":
    main()
