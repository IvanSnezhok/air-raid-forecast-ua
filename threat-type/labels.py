"""Label every alert onset with the threat that caused it.

For an alert that starts in oblast r at time s we look at Air Force posts
published in [s - BEFORE, s + AFTER] that name oblast r (e.g. "БпЛА на
Сумщині", "Ракета на Дніпро"). If there are none, nationwide posts (ballistic
threat "з півдня", MiG-31K take-off, bombers) published in
[s - NATIONAL_BEFORE, s + NATIONAL_AFTER] are used instead.
The strongest threat wins: ballistic > missile > kab > drone. Onsets with no
matching post stay "unknown" and are left out of training and scoring (they
are reported, not hidden).

"massed_drone": a drone onset during a night the Air Force summary reports at
least MASSED_DRONES drones (the 2026 median is ~140, 90th percentile ~265),
or, for daytime alerts and nights without a summary, while at least
MASSED_REGIONS oblasts are on alert at once.

Labels describe the past and are not features. Features for the type model
only use posts published before the forecast origin (see features.py).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from threat_lexicon import CLASSES, LEVEL, classify, top_threat

BEFORE = pd.Timedelta(minutes=60)
AFTER = pd.Timedelta(minutes=15)
NATIONAL_BEFORE = pd.Timedelta(minutes=20)
NATIONAL_AFTER = pd.Timedelta(minutes=5)
MASSED_DRONES = 200
MASSED_REGIONS = 12
KYIV = "Europe/Kyiv"
TYPES = ["drone", "massed_drone", "kab", "missile", "ballistic"]


def load_posts(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    info = df["text"].fillna("").map(classify)
    for k in ("threats", "regions", "national", "summary", "all_clear", "drones_reported"):
        df[k] = info.map(lambda d: d[k])
    df = df.sort_values("date").reset_index(drop=True)
    return inherit_threats(df)


def inherit_threats(df: pd.DataFrame, within=pd.Timedelta(minutes=15)) -> pd.DataFrame:
    """Follow-ups like "Продовжує рух на Київ!" or "Київ - в укриття!" name a
    place but not the threat: take it from the previous threat post (<= 15 min,
    earlier posts only, so this stays usable live)."""
    last_t, last_th = None, []
    out = []
    for d, th, regs, summ, clear in zip(df["date"], df["threats"], df["regions"], df["summary"], df["all_clear"]):
        if th:
            last_t, last_th = d, th
            out.append(th)
        elif regs and not summ and not clear and last_t is not None and d - last_t <= within:
            out.append(list(last_th))
        else:
            out.append(th)
    df["threats"] = out
    return df


def night_of(ts: pd.Series) -> pd.Series:
    """Attack night key: Kyiv date of (time - 12 h), so 18:00 .. 12:00 next day is one night."""
    return (ts.dt.tz_convert(KYIV) - pd.Timedelta(hours=12)).dt.date


def drones_per_night(posts: pd.DataFrame) -> pd.Series:
    """Drones reported by the morning summaries, per attack night."""
    s = posts[posts["summary"] & posts["drones_reported"].notna()]
    # a summary posted at 08:00 describes the night that ended that morning
    key = night_of(s["date"])
    return s.groupby(key)["drones_reported"].max()


def onsets(alerts: pd.DataFrame) -> pd.DataFrame:
    """One row per alert start (region, start), from the cleaned volunteer data."""
    on = alerts[["region", "start", "end"]].copy()
    return on.sort_values("start").reset_index(drop=True)


def concurrent_regions(on: pd.DataFrame) -> np.ndarray:
    """Number of oblasts on alert at each onset (including itself)."""
    s = on["start"].to_numpy()
    starts = np.sort(on["start"].to_numpy())
    ends = np.sort(on["end"].to_numpy())
    return (np.searchsorted(starts, s, "right") - np.searchsorted(ends, s, "right")).astype(int)


def label_onsets(on: pd.DataFrame, posts: pd.DataFrame) -> pd.DataFrame:
    live = posts[~posts["summary"]]
    t = live["date"].to_numpy()
    nat = live[live["national"]]
    tn = nat["date"].to_numpy()
    # explode region posts once
    ex = live[["date", "regions", "threats"]].explode("regions").dropna(subset=["regions"])
    by_region = {r: (g["date"].to_numpy(), g["threats"].to_numpy()) for r, g in ex.groupby("regions")}

    lab, src = [], []
    for r, s in zip(on["region"].to_numpy(), on["start"].to_numpy()):
        found: set[str] = set()
        how = ""
        if r in by_region:
            d, th = by_region[r]
            lo, hi = np.searchsorted(d, s - BEFORE.to_timedelta64()), np.searchsorted(d, s + AFTER.to_timedelta64(), "right")
            for x in th[lo:hi]:
                found.update(x)
            if found:
                how = "region"
        lo, hi = np.searchsorted(tn, s - NATIONAL_BEFORE.to_timedelta64()), np.searchsorted(tn, s + NATIONAL_AFTER.to_timedelta64(), "right")
        if hi > lo and not found:  # a post naming the oblast is better evidence
            for x in nat["threats"].to_numpy()[lo:hi]:
                found.update(x)
            how = "national"
        lab.append(top_threat(found) or "unknown")
        src.append(how)
    out = on.copy()
    out["type"] = lab
    out["label_source"] = src
    out["n_concurrent"] = concurrent_regions(on)

    dpn = drones_per_night(posts)
    out["night"] = night_of(out["start"])
    out["night_drones"] = out["night"].map(dpn)
    # the morning summary covers the night (about 18:00 .. 08:00 Kyiv time)
    hour = out["start"].dt.tz_convert(KYIV).dt.hour
    by_summary = out["night_drones"].notna() & ((hour >= 17) | (hour < 9))
    massed = np.where(by_summary, out["night_drones"] >= MASSED_DRONES, out["n_concurrent"] >= MASSED_REGIONS)
    is_drone = out["type"] == "drone"
    # The summary comes out the next morning, so features may only use a
    # "live" version of the label where massed is judged by concurrent alerts.
    out["type_live"] = out["type"].where(~(is_drone & (out["n_concurrent"] >= MASSED_REGIONS)), "massed_drone")
    out.loc[is_drone & massed, "type"] = "massed_drone"
    out["level"] = out["type"].map(LEVEL).fillna("unknown")
    # posts in the first coverage day may be missing: drop onsets before the first post
    return out[out["start"] >= posts["date"].min() + BEFORE].reset_index(drop=True)
