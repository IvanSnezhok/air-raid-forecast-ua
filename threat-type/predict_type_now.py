"""Live threat-type forecast: for each oblast, what the next alert is likely to be.

    python threat-type/predict_type_now.py --posts data/tg_kpszsu.parquet

`--posts` is the Air Force post history (the `threat-labels-data` branch). The
newest posts are scraped from https://t.me/s/kpszsu on top of it, so features
such as "drones heading to the oblast in the last hour" are current.

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
    ap.add_argument("--posts", required=True)
    ap.add_argument("--no-fetch", action="store_true", help="do not scrape new posts")
    a = ap.parse_args()

    stored = pd.read_parquet(a.posts)
    if not a.no_fetch:
        try:
            new = fresh_posts(int(stored["id"].max()))
            stored = pd.concat([stored, new], ignore_index=True).drop_duplicates("id", keep="last")
            print(f"+{len(new)} fresh posts, newest {new['date'].max() if len(new) else '-'}")
        except Exception as e:
            print(f"WARNING: could not fetch fresh posts ({e!r}); using stored history only")
    tmp = HERE / "reports" / "_posts_now.parquet"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    stored.to_parquet(tmp, index=False)
    posts = load_posts(tmp)
    tmp.unlink()

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
