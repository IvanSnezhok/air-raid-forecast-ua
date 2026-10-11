"""Live threat-type forecast: for each oblast, what the next alert is likely to be.

    python threat-type/predict_type_now.py --posts data/tg_kpszsu.parquet

`--posts` is the Air Force post history (the `threat-labels-data` branch). The
newest posts are scraped from https://t.me/s/kpszsu on top of it, so features
such as "drones heading to the oblast in the last hour" are current.

`--store DIR` (a checkout of the `text-data` branch) is the better source: the
fresh posts are written into that point-in-time archive with the time we first
saw them (nothing is thrown away), and the posts are read back from it with the
text as first seen. Commit DIR afterwards to keep them.

Writes threat-type/reports/type_forecast_now.csv: per oblast and horizon,
P(type | an alert starts) for drone, massed_drone, kab, missile, ballistic,
P(red) and the expected level. When reports/forecast_now.csv from the main
model exists, also P(alert of each type starts within h hours) =
P(onset within h) x P(type | onset).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
from airraid.data import download, load_alerts  # noqa: E402
from features import build  # noqa: E402
from airraid.text import archive, telegram  # noqa: E402
from fetch_sources import get, parse_page  # noqa: E402
from labels import TYPES, label_onsets, load_posts, onsets  # noqa: E402
from run_type import gbm_fit, rows_for  # noqa: E402
from threat_lexicon import LEVEL  # noqa: E402

ROUNDS = {1: 70, 3: 50}  # about what early stopping picked in run_type.py


def fresh_posts(known_max_id: int, channel: str = "kpszsu", max_pages: int = 150) -> pd.DataFrame:
    rows, before = [], None
    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        page = parse_page(get(url).decode("utf-8", "replace"), channel)
        if not page:
            break
        rows += page
        before = min(p["id"] for p in page)
        if before <= known_max_id:
            break
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", help="tg_kpszsu.parquet from the threat-labels-data branch")
    ap.add_argument("--store", help="text-data archive: fresh posts are stored there and read back")
    ap.add_argument("--no-fetch", action="store_true", help="do not scrape new posts")
    a = ap.parse_args()
    if not (a.posts or a.store):
        ap.error("give --store (preferred) or --posts")

    if a.store:
        if not a.no_fetch:
            try:
                telegram.update(Path(a.store), "kpszsu", max_pages=150)
            except Exception as e:
                print(f"WARNING: could not fetch fresh posts ({e!r}); using stored history only")
        rec = archive.read_records(Path(a.store), "telegram", "kpszsu")
        # same columns as tg_kpszsu.parquet, with the text as first seen
        stored = pd.DataFrame({"channel": "kpszsu", "id": rec["id"], "date": rec["date"],
                               "edited": rec["edited_first"], "text": rec["text_first"]})
    else:
        stored = pd.read_parquet(a.posts)
        if not a.no_fetch:
            try:
                new = fresh_posts(int(stored["id"].max()))
                # keep the stored text when a post comes back edited: it is what was public first
                stored = pd.concat([stored, new], ignore_index=True).drop_duplicates("id", keep="first")
                print(f"+{len(new)} fresh posts, newest {new['date'].max() if len(new) else '-'} "
                      "(not kept: pass --store to archive them)")
            except Exception as e:
                print(f"WARNING: could not fetch fresh posts ({e!r}); using stored history only")
    posts = load_posts(stored)

    try:
        download()
    except Exception as e:
        print(f"WARNING: could not refresh alert history ({e!r})")
    alerts = load_alerts()
    lab = label_onsets(onsets(alerts), posts)
    known = lab[lab["type"] != "unknown"]
    origin = pd.Timestamp.now(tz="UTC").floor("h")
    regions = sorted(alerts["region"].unique())
    now_rows = pd.DataFrame({"origin": [origin] * len(regions), "region": regions})

    out = []
    for h in (1, 3):
        R = rows_for(known, h)
        X = build(R, lab, alerts, posts)
        model, _ = gbm_fit(X, R["y"].to_numpy(), ROUNDS[h])
        Xn = build(now_rows, lab, alerts, posts)
        Xn["region"] = pd.Categorical(Xn["region"].astype(str), categories=X["region"].cat.categories)
        P = model.predict(Xn)
        df = pd.DataFrame(P, columns=[f"p_{c}" for c in TYPES]).round(3)
        df.insert(0, "horizon_h", h)
        df.insert(0, "region", regions)
        df["p_red"] = (1 - df["p_drone"]).round(3)
        df["level"] = np.where(df["p_red"] >= 0.5, "red", "yellow")
        df["most_likely"] = [TYPES[i] for i in P.argmax(1)]
        out.append(df)
    out = pd.concat(out, ignore_index=True)

    main_fc = ROOT / "reports" / "forecast_now.csv"
    if main_fc.exists():
        f = pd.read_csv(main_fc)
        for h in (1, 3):
            col = f"onset_{h}h"
            if col in f:
                m = out["horizon_h"] == h
                p_on = out.loc[m, "region"].map(f.set_index("region")[col])
                for c in TYPES:
                    out.loc[m, f"p_onset_{c}"] = (p_on * out.loc[m, f"p_{c}"]).round(3)
    out.to_csv(HERE / "reports" / "type_forecast_now.csv", index=False)
    (HERE / "reports" / "type_forecast_meta.json").write_text(json.dumps({
        "origin_utc": f"{origin:%Y-%m-%d %H:%M}",
        "posts_until_utc": f"{posts['date'].max():%Y-%m-%d %H:%M}",
        "alerts_until_utc": f"{alerts['start'].max():%Y-%m-%d %H:%M}",
        "levels": LEVEL,
    }, indent=2))
    print(out[out["horizon_h"] == 1].sort_values("p_red", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
