"""Text features for the main forecast, per (origin hour, region).

Row order matches `airraid.features.build_features` (region-major: every origin
of the first region, then the next region), so the columns can be put next to
the alert-history features as they are.

No-leakage contract
-------------------
A post or news item counts at origin T only if its `available_at` is strictly
before T (see archive.py for the rule: publish time for posts that were not
edited when we first saw them, else the moment we first saw them; first sighting
for news). Every window is [T - w, T). Follow-up posts that name a place but no
threat ("Продовжує рух на Київ") take the threat of the previous post of the same
channel only if that post was available earlier. Origins outside the collection
coverage get NaN, never 0, and `tg_covered = 0`.
`tests/test_text_leakage.py` checks all of this mechanically.

Columns (w in minutes):
  regional  tg_reg_any_{w}m                    posts naming the oblast, w = 30/60/180
            tg_reg_{drone|missile|ballistic|kab|clear}_{w}m   ... with that threat (or all-clear)
            tg_reg_min_since_threat / _any     minutes since the oblast was last named (cap 1440)
  national  tg_nat_posts_60m                   all posts
            tg_nat_{drone|missile|ballistic|kab}_{60|180}m
            tg_nat_regions_60m                 oblasts named with a threat in the last hour
            tg_nat_platform_{60|180}m, tg_min_since_platform   bombers / MiG-31K / take-offs
            tg_nat_national_60m, tg_min_since_national         nationwide ballistic threat etc.
            tg_nat_launch_{60|180}m, tg_nat_massed_180m, tg_nat_explosion_60m
            tg_summary_drones, tg_summary_missiles, tg_summary_age_h
                                               the latest morning summary of the night attack
  news      news_attack_{60|180}m, news_reg_180m (only with news coverage)
  coverage  tg_covered, news_covered
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import archive
from .lexicon import classify
from .telegram import FEATURE_CHANNELS

WINDOWS = (30, 60, 180)
CAP_MIN = 1440
WARMUP = pd.Timedelta(days=2)  # history needed before an origin counts as covered
INHERIT = pd.Timedelta(minutes=15)
SUMMARY_MAX_AGE_H = 36
REG_CLASSES = ("drone", "missile", "ballistic", "kab")
NS_MIN = 60_000_000_000
RULES = ("strict", "unedited_only", "permissive")


def load_posts(root: Path | str, channels: list[str] | None = None, rule: str = "strict") -> pd.DataFrame:
    """Posts from the archive, classified, with `available_at` per `rule`.

    strict         archive rule (the one used for the model and live)
    unedited_only  drop every post ever seen edited (sensitivity)
    permissive     publish time for every post, final text: NOT leak-free, only to
                   bound how much the edit rule matters
    """
    if rule not in RULES:
        raise ValueError(rule)
    df = archive.load(Path(root), "telegram", channels or FEATURE_CHANNELS)
    cov = df.attrs["coverage"]
    if rule == "unedited_only":
        df = df[~(df["edited_first"] | df["edited_seen"])]
    elif rule == "permissive":
        df = df.assign(available_at=df["date"], text_first=df["text_last"])
    df = classify_posts(df)
    df.attrs["coverage"] = cov
    return df


def classify_posts(df: pd.DataFrame) -> pd.DataFrame:
    """Tag each post (text_first) and let follow-ups inherit the threat of the previous post."""
    df = df.sort_values(["available_at", "source", "id"], kind="stable").reset_index(drop=True)
    info = df["text_first"].fillna("").map(classify)
    for k in ("threats", "regions", "drones_reported", "missiles_reported"):
        df[k] = info.map(lambda d, k=k: d[k])
    for k in ("national", "summary", "all_clear", "platform", "launch", "massed", "explosion"):
        df[k] = info.map(lambda d, k=k: d[k]).astype(bool)
    threats = df["threats"].tolist()
    last: dict[str, tuple] = {}
    for i, (src, t, regs, summ, clear) in enumerate(zip(df["source"], df["available_at"], df["regions"],
                                                        df["summary"], df["all_clear"])):
        if threats[i]:
            last[src] = (t, threats[i])
        elif regs and not summ and not clear and src in last and t - last[src][0] <= INHERIT:
            threats[i] = list(last[src][1])
    df["threats"] = threats
    return df


def _ns(x) -> np.ndarray:
    return pd.DatetimeIndex(x).as_unit("ns").asi8 if len(x) else np.array([], np.int64)


def _count(times: np.ndarray, T: np.ndarray, w_min: int) -> np.ndarray:
    """Number of sorted `times` in [T - w, T)."""
    return (np.searchsorted(times, T, "left") - np.searchsorted(times, T - w_min * NS_MIN, "left")).astype(np.float32)


def _min_since(times: np.ndarray, T: np.ndarray, cap: int = CAP_MIN) -> np.ndarray:
    i = np.searchsorted(times, T, "left") - 1
    out = np.full(T.shape, cap, np.float32)
    ok = i >= 0
    out[ok] = np.minimum((T[ok] - times[i[ok]]) / NS_MIN, cap)
    return out


def covered(T: np.ndarray, coverage: dict, warmup: pd.Timedelta = WARMUP) -> np.ndarray:
    """True where every source has complete data over [T - warmup, T]."""
    ok = np.ones(len(T), bool)
    if not coverage:
        return ~ok
    w = warmup.value
    for start, end, gaps in coverage.values():
        if start is None or end is None:
            return ~ok
        ok &= (T >= start.value + w) & (T <= end.value)
        for a, b in gaps:
            a = start.value if a is None else a.value
            ok &= ~((T > a) & (T - w < b.value))
    return ok


def origins_ns(t0: pd.Timestamp, K: np.ndarray) -> np.ndarray:
    return t0.value + np.asarray(K, np.int64) * 3_600_000_000_000


def telegram_features(posts: pd.DataFrame, T: np.ndarray, regions: list[str]) -> dict[str, np.ndarray]:
    """Raw feature arrays, national ones of shape [nT], regional ones [nR, nT]."""
    live = posts[~posts["summary"]]
    t_all = _ns(live["available_at"])
    th = live["threats"].to_numpy()
    nat: dict[str, np.ndarray] = {"tg_nat_posts_60m": _count(t_all, T, 60)}
    for c in REG_CLASSES:
        tc = t_all[np.fromiter((c in x for x in th), bool, len(th))]
        for w in (60, 180):
            nat[f"tg_nat_{c}_{w}m"] = _count(tc, T, w)
    for flag, wins, since in (("platform", (60, 180), True), ("national", (60,), True), ("launch", (60, 180), False),
                              ("massed", (180,), False), ("explosion", (60,), False)):
        tf = t_all[live[flag].to_numpy(bool)]
        for w in wins:
            nat[f"tg_nat_{flag}_{w}m"] = _count(tf, T, w)
        if since:
            nat[f"tg_min_since_{flag}"] = _min_since(tf, T)

    # latest morning summary (published after the night it describes)
    summ = posts[posts["summary"] & (posts["drones_reported"].notna() | posts["missiles_reported"].notna())]
    ts = _ns(summ["available_at"])
    i = np.searchsorted(ts, T, "left") - 1
    age = np.full(len(T), np.inf)
    if len(ts):
        age = np.where(i >= 0, (T - ts[np.maximum(i, 0)]) / 3.6e12, np.inf)
    fresh = age <= SUMMARY_MAX_AGE_H
    for k, col in (("drones", "drones_reported"), ("missiles", "missiles_reported")):
        v = summ[col].astype(float).fillna(0).to_numpy()
        last = v[np.maximum(i, 0)] if len(v) else np.zeros(len(T))
        nat[f"tg_summary_{k}"] = np.where(fresh, last, 0).astype(np.float32)
    nat["tg_summary_age_h"] = np.minimum(age, 72).astype(np.float32)

    ex = live[["available_at", "regions", "threats", "all_clear"]].explode("regions").dropna(subset=["regions"])
    reg: dict[str, np.ndarray] = {}
    names = [f"tg_reg_any_{w}m" for w in WINDOWS] + [f"tg_reg_{c}_{w}m" for c in (*REG_CLASSES, "clear") for w in WINDOWS]
    names += ["tg_reg_min_since_threat", "tg_reg_min_since_any"]
    for n in names:
        reg[n] = np.zeros((len(regions), len(T)), np.float32)
    groups = {r: g for r, g in ex.groupby("regions")}
    for j, r in enumerate(regions):
        g = groups.get(r)
        if g is None:
            reg["tg_reg_min_since_threat"][j] = CAP_MIN
            reg["tg_reg_min_since_any"][j] = CAP_MIN
            continue
        tr = _ns(g["available_at"])
        gth = g["threats"].to_numpy()
        for w in WINDOWS:
            reg[f"tg_reg_any_{w}m"][j] = _count(tr, T, w)
        for c in REG_CLASSES:
            tc = tr[np.fromiter((c in x for x in gth), bool, len(gth))]
            for w in WINDOWS:
                reg[f"tg_reg_{c}_{w}m"][j] = _count(tc, T, w)
        tcl = tr[g["all_clear"].to_numpy(bool)]
        for w in WINDOWS:
            reg[f"tg_reg_clear_{w}m"][j] = _count(tcl, T, w)
        tthr = tr[np.fromiter((bool(x) for x in gth), bool, len(gth))]
        reg["tg_reg_min_since_threat"][j] = _min_since(tthr, T)
        reg["tg_reg_min_since_any"][j] = _min_since(tr, T)
    any_threat = sum(reg[f"tg_reg_{c}_60m"] for c in REG_CLASSES) > 0
    nat["tg_nat_regions_60m"] = any_threat.sum(axis=0).astype(np.float32)
    return {"nat": nat, "reg": reg}


def news_features(items: pd.DataFrame, T: np.ndarray, regions: list[str]) -> dict[str, np.ndarray]:
    t = _ns(items["available_at"])
    nat = {f"news_attack_{w}m": _count(t, T, w) for w in (60, 180)}
    reg = {"news_reg_180m": np.zeros((len(regions), len(T)), np.float32)}
    if len(items):
        from .lexicon import normalise, regions_in

        rr = items["text_first"].fillna("").map(lambda s: regions_in(normalise(s)))
        ex = pd.DataFrame({"t": items["available_at"].to_numpy(), "r": rr.to_numpy()}).explode("r").dropna()
        for j, r in enumerate(regions):
            reg["news_reg_180m"][j] = _count(_ns(ex.loc[ex["r"] == r, "t"]), T, 180)
    return {"nat": nat, "reg": reg}


def _frame(parts: dict, T: np.ndarray, K: np.ndarray, regions: list[str], ok: np.ndarray, flag: str) -> pd.DataFrame:
    R = len(regions)
    cols = {}
    for k, v in parts["nat"].items():
        cols[k] = np.tile(v, R)
    for k, v in parts["reg"].items():
        cols[k] = v.reshape(-1)
    df = pd.DataFrame(cols)
    okr = np.tile(ok, R)
    df.loc[~okr, list(cols)] = np.nan
    df[flag] = okr.astype(np.float32)
    return df


def build_text_features(t0: pd.Timestamp, K: np.ndarray, regions: list[str], posts: pd.DataFrame,
                        news: pd.DataFrame | None = None) -> pd.DataFrame:
    """Text features for every (origin hour in K, region), region-major like build_features.

    `posts` / `news` come from load_posts / load_news and carry their coverage in .attrs.
    """
    K = np.asarray(K, np.int64)
    T = origins_ns(t0, K)
    ok = covered(T, posts.attrs.get("coverage", {}))
    out = _frame(telegram_features(posts, T, regions), T, K, regions, ok, "tg_covered")
    if news is not None:
        okn = covered(T, news.attrs.get("coverage", {}), warmup=pd.Timedelta(hours=3))
        out = pd.concat([out, _frame(news_features(news, T, regions), T, K, regions, okn, "news_covered")], axis=1)
    out.insert(0, "region", np.repeat(np.array(regions), len(K)))
    out.insert(0, "origin_hour", np.tile(K, len(regions)))
    return out


def load_news(root: Path | str, sources: list[str] | None = None) -> pd.DataFrame:
    df = archive.load(Path(root), "news", sources)
    return df


def attach(base: pd.DataFrame, text: pd.DataFrame) -> pd.DataFrame:
    """Put text columns next to build_features output after checking the row order."""
    if not (np.array_equal(base["origin_hour"].to_numpy(), text["origin_hour"].to_numpy())
            and np.array_equal(base["region"].to_numpy(), text["region"].to_numpy())):
        raise ValueError("text features are not aligned with the alert-history features")
    return pd.concat([base.reset_index(drop=True), text.drop(columns=["origin_hour", "region"]).reset_index(drop=True)],
                     axis=1)


def text_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith(("tg_", "news_"))]
