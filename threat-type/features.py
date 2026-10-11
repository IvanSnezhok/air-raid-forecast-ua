"""Features for "which threat will the next alert be", known at the forecast origin T.

No-leakage contract (checked by tests/test_type_leakage.py):
  * an onset's label counts as known only from start + AFTER (the window in which
    its posts are searched has closed), and its "massed" part is judged by the
    number of oblasts on alert, not by the next morning's summary;
  * an alert counts as active at T if it started before T and has not ended
    before T (its future end time is never used);
  * Telegram posts count only if published strictly before T.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from labels import AFTER, KYIV, TYPES

LIVE_CLASSES = ["ballistic", "missile", "kab", "drone"]
H = np.timedelta64(1, "h")


def _ns(x) -> np.ndarray:
    return pd.DatetimeIndex(x).as_unit("ns").asi8 if len(x) else np.array([], np.int64)


def _count(times: np.ndarray, T: np.ndarray, hours: float) -> np.ndarray:
    w = int(hours * 3600e9)
    return (np.searchsorted(times, T, "left") - np.searchsorted(times, T - w, "left")).astype(np.float32)


def _hours_since(times: np.ndarray, T: np.ndarray, cap: float = 24 * 30) -> np.ndarray:
    i = np.searchsorted(times, T, "left") - 1
    out = np.full(len(T), cap, np.float32)
    ok = i >= 0
    out[ok] = np.minimum((T[ok] - times[i[ok]]) / 3600e9, cap)
    return out


def build(rows: pd.DataFrame, labelled: pd.DataFrame, alerts: pd.DataFrame, posts: pd.DataFrame) -> pd.DataFrame:
    """rows: DataFrame with columns origin (UTC Timestamp) and region. Returns features aligned to rows."""
    T = _ns(rows["origin"])
    regions = rows["region"].to_numpy()
    f: dict[str, np.ndarray] = {}
    loc = rows["origin"].dt.tz_convert(KYIV)
    f["hour"] = loc.dt.hour.to_numpy(np.float32)
    f["dow"] = loc.dt.dayofweek.to_numpy(np.float32)
    f["night"] = ((f["hour"] >= 20) | (f["hour"] < 7)).astype(np.float32)

    # ---- label history (available from start + AFTER) ----
    # type_live: the label as it could be known at start + AFTER (no morning summary)
    lab = labelled[labelled["type_live"] != "unknown"].assign(type=lambda d: d["type_live"])
    lab["known"] = lab["start"] + AFTER
    lab = lab.sort_values("known")
    nat_t = {c: _ns(lab.loc[lab["type"] == c, "known"]) for c in TYPES}
    for c in TYPES:
        for w in (3, 24, 24 * 7):
            f[f"nat_{c}_{w}h"] = _count(nat_t[c], T, w)
        f[f"nat_since_{c}_h"] = _hours_since(nat_t[c], T)
    reg_cols = {f"reg_{c}_{w}d": np.zeros(len(T), np.float32) for c in TYPES for w in (7, 30)}
    reg_cols.update({f"reg_since_{c}_h": np.full(len(T), 24 * 30, np.float32) for c in TYPES})
    reg_cols["reg_last_type"] = np.full(len(T), -1, np.float32)
    reg_cols["reg_labelled_30d"] = np.zeros(len(T), np.float32)
    for r, g in lab.groupby("region"):
        m = regions == r
        if not m.any():
            continue
        Tm = T[m]
        kt = _ns(g["known"])
        types = g["type"].to_numpy()
        reg_cols["reg_labelled_30d"][m] = _count(kt, Tm, 24 * 30)
        i = np.searchsorted(kt, Tm, "left") - 1
        last = np.full(len(Tm), -1, np.float32)
        ok = i >= 0
        last[ok] = [TYPES.index(x) for x in types[i[ok]]]
        reg_cols["reg_last_type"][m] = last
        for c in TYPES:
            tc = kt[types == c]
            for w in (7, 30):
                reg_cols[f"reg_{c}_{w}d"][m] = _count(tc, Tm, 24 * w)
            reg_cols[f"reg_since_{c}_h"][m] = _hours_since(tc, Tm)
    f.update(reg_cols)
    for c in TYPES:
        f[f"reg_share_{c}_30d"] = f[f"reg_{c}_30d"] / np.maximum(f["reg_labelled_30d"], 1)

    # ---- alert state at T (leak-free) ----
    s = _ns(alerts["start"].sort_values())
    e_sorted = np.sort(_ns(alerts["end"]))
    started = np.searchsorted(s, T, "left")
    ended = np.searchsorted(e_sorted, T, "left")  # ends strictly before T are known
    f["nat_active_now"] = (started - ended).astype(np.float32)
    f["nat_starts_1h"] = _count(s, T, 1)
    f["nat_starts_3h"] = _count(s, T, 3)
    reg_active = np.zeros(len(T), np.float32)
    reg_starts_24h = np.zeros(len(T), np.float32)
    for r, g in alerts.groupby("region"):
        m = regions == r
        if not m.any():
            continue
        rs = np.sort(_ns(g["start"]))
        re_ = np.sort(_ns(g["end"]))
        reg_active[m] = np.searchsorted(rs, T[m], "left") - np.searchsorted(re_, T[m], "left")
        reg_starts_24h[m] = _count(rs, T[m], 24)
    f["reg_active_now"] = (reg_active > 0).astype(np.float32)
    f["reg_starts_24h"] = reg_starts_24h

    # ---- Air Force posts published before T ----
    live = posts[~posts["summary"]]
    pt = _ns(live["date"])
    th = live["threats"].to_numpy()
    for c in LIVE_CLASSES:
        tc = pt[np.array([c in x for x in th], bool)] if len(pt) else pt
        for w in (1, 3):
            f[f"tg_{c}_{w}h"] = _count(tc, T, w)
        f[f"tg_since_{c}_h"] = _hours_since(tc, T, cap=24 * 7)
    tn = _ns(live.loc[live["national"], "date"])
    f["tg_national_1h"] = _count(tn, T, 1)
    f["tg_since_national_h"] = _hours_since(tn, T, cap=24 * 7)
    ex = live[["date", "regions", "threats"]].explode("regions").dropna(subset=["regions"])
    for c in LIVE_CLASSES:
        f[f"tg_reg_{c}_1h"] = np.zeros(len(T), np.float32)
    f["tg_reg_any_1h"] = np.zeros(len(T), np.float32)
    for r, g in ex.groupby("regions"):
        m = regions == r
        if not m.any():
            continue
        gt = _ns(g["date"])
        f["tg_reg_any_1h"][m] = _count(gt, T[m], 1)
        gth = g["threats"].to_numpy()
        for c in LIVE_CLASSES:
            tc = gt[np.array([c in x for x in gth], bool)]
            f[f"tg_reg_{c}_1h"][m] = _count(tc, T[m], 1)

    out = pd.DataFrame(f, index=rows.index)
    out["region"] = pd.Categorical(rows["region"])
    return out
