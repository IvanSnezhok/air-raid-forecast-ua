"""Turn collected Telegram posts into per-(origin, region) features.

No-leakage contract
-------------------
Every feature at forecast origin T uses only posts whose `available_at` is
strictly before T, where:
  * live posts (collected by `follow`): available_at = collected_at, the moment
    we actually saw them;
  * backfilled posts: available_at = date (publish time), unless the post was
    edited more than EDIT_GRACE after publishing, in which case the final text
    we hold was only public from edit_date, so available_at = edit_date.
Origins outside the period the collection covers get NaN features, not zeros,
so "no posts because we weren't collecting" is never read as "quiet night".
`tests/test_no_leakage.py` checks that features at T do not change when every
post available at or after T is removed.

Output columns (w in WINDOWS minutes; all counts are posts, not distinct events):
  national, identical for every region:
    tg_msgs_{w}m                    all posts
    tg_{threat}_{w}m                posts mentioning that threat class
    tg_min_since_{threat}           minutes since the last such post (cap 1440)
  regional:
    tg_reg_any_{w}m                 posts naming the region
    tg_reg_{drone|missile|kab|all_clear}_{w}m
                                    posts naming the region AND that class
                                    (missile = missile|cruise|ballistic)
    tg_reg_min_since_any            minutes since region last named (cap 1440)
    tg_reg_min_since_threat         minutes since region named with drone/missile/kab
  tg_covered                        1 if origin is inside the collection period
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from lexicon import REGIONS, THREATS, tag

WINDOWS = (30, 60, 180)
EDIT_GRACE = pd.Timedelta(minutes=2)
CAP_MIN = 1440
NAT_MIN_SINCE = ("launch_platform", "ballistic", "cruise", "drone", "kab")
REG_CLASSES = {
    "drone": {"drone"},
    "missile": {"missile", "cruise", "ballistic"},
    "kab": {"kab"},
    "all_clear": {"all_clear"},
}
_THREAT_ANY = {"drone", "missile", "cruise", "ballistic", "kab"}


def load_messages(folder: str | Path) -> pd.DataFrame:
    """Read every *.jsonl from the collector, dedupe, compute available_at."""
    rows = []
    for p in sorted(Path(folder).glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return prepare(pd.DataFrame(rows))


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("date", "edit_date", "collected_at"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    # the same post may be fetched twice (live, then backfill): keep the copy
    # we saw first, its text is the one that was public at that time
    df = df.sort_values("collected_at").drop_duplicates(["channel", "id"], keep="first")
    late_edit = df["edit_date"].notna() & (df["edit_date"] - df["date"] > EDIT_GRACE)
    avail = df["date"].where(~late_edit, df["edit_date"])
    df["available_at"] = avail.where(df["mode"] != "live", df["collected_at"])
    tags = df["text"].fillna("").map(tag)
    df["regions"] = tags.str[0]
    df["threats"] = tags.str[1]
    return df.sort_values("available_at").reset_index(drop=True)


def _ns(ts) -> np.ndarray:
    return pd.DatetimeIndex(ts).as_unit("ns").asi8


def _counts(times: np.ndarray, T: np.ndarray, w_min: int) -> np.ndarray:
    """Number of sorted `times` in [T - w, T)."""
    w = np.int64(w_min) * 60_000_000_000
    return (np.searchsorted(times, T, "left") - np.searchsorted(times, T - w, "left")).astype(np.float32)


def _min_since(times: np.ndarray, T: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(times, T, "left") - 1
    out = np.full(T.shape, CAP_MIN, np.float32)
    ok = idx >= 0
    out[ok] = np.minimum((T[ok] - times[idx[ok]]) / 60e9, CAP_MIN)
    return out


def build_text_features(
    msgs: pd.DataFrame,
    origins,
    regions: list[str] | None = None,
    coverage: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> pd.DataFrame:
    """Features for every (origin, region); origins are UTC timestamps."""
    regions = list(regions or REGIONS)
    oi = pd.DatetimeIndex(origins)
    T = _ns(oi.tz_convert("UTC") if oi.tz is not None else oi.tz_localize("UTC"))
    nT, nR = len(T), len(regions)
    if coverage is None:
        coverage = (msgs["available_at"].min(), msgs["collected_at"].max())
    c0, c1 = _ns([coverage[0]])[0], _ns([coverage[1]])[0]
    covered = (T > c0) & (T <= c1)

    t_all = _ns(msgs["available_at"])
    threat_sets = msgs["threats"].map(set).to_numpy()
    nat: dict[str, np.ndarray] = {}
    for w in WINDOWS:
        nat[f"tg_msgs_{w}m"] = _counts(t_all, T, w)
    for th in THREATS:
        tt = t_all[np.array([th in s for s in threat_sets], bool)] if len(t_all) else t_all
        for w in WINDOWS:
            nat[f"tg_{th}_{w}m"] = _counts(tt, T, w)
        if th in NAT_MIN_SINCE:
            nat[f"tg_min_since_{th}"] = _min_since(tt, T)

    # explode to (post, region) once
    ex = msgs[["available_at", "regions", "threats"]].explode("regions").dropna(subset=["regions"])
    ex_sets = ex["threats"].map(set)
    reg: dict[str, np.ndarray] = {}
    names = [f"tg_reg_any_{w}m" for w in WINDOWS]
    names += [f"tg_reg_{c}_{w}m" for c in REG_CLASSES for w in WINDOWS]
    names += ["tg_reg_min_since_any", "tg_reg_min_since_threat"]
    for n in names:
        reg[n] = np.empty((nR, nT), np.float32)
    for i, r in enumerate(regions):
        m = (ex["regions"] == r).to_numpy()
        tr = _ns(ex.loc[m, "available_at"])
        sr = ex_sets[m].to_numpy()
        for w in WINDOWS:
            reg[f"tg_reg_any_{w}m"][i] = _counts(tr, T, w)
        for c, cls in REG_CLASSES.items():
            tc = tr[np.array([bool(s & cls) for s in sr], bool)] if len(tr) else tr
            for w in WINDOWS:
                reg[f"tg_reg_{c}_{w}m"][i] = _counts(tc, T, w)
        reg["tg_reg_min_since_any"][i] = _min_since(tr, T)
        tthr = tr[np.array([bool(s & _THREAT_ANY) for s in sr], bool)] if len(tr) else tr
        reg["tg_reg_min_since_threat"][i] = _min_since(tthr, T)

    out = {
        "origin": np.tile(pd.DatetimeIndex(T, tz="UTC"), nR),
        "region": np.repeat(regions, nT),
    }
    for k, v in nat.items():
        out[k] = np.tile(v, nR)
    for k, v in reg.items():
        out[k] = v.reshape(-1)
    df = pd.DataFrame(out)
    feat = [c for c in df.columns if c.startswith("tg_")]
    df.loc[~np.tile(covered, nR), feat] = np.nan
    df["tg_covered"] = np.tile(covered, nR).astype(np.int8)
    return df


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build hourly text features from collected posts.")
    p.add_argument("--in", dest="inp", default=str(Path(__file__).parent / "data" / "telegram"))
    p.add_argument("--out", default=str(Path(__file__).parent / "data" / "text_features.parquet"))
    a = p.parse_args()
    msgs = load_messages(a.inp)
    origins = pd.date_range(msgs["available_at"].min().ceil("h"), msgs["collected_at"].max().floor("h"), freq="h")
    feats = build_text_features(msgs, origins)
    feats.to_parquet(a.out, index=False) if a.out.endswith(".parquet") else feats.to_csv(a.out, index=False)
    print(f"{len(msgs)} posts -> {feats.shape} features -> {a.out}")
