"""Why this forecast: what the served model saw at the origin and how it used it.

For every (region, target, horizon) of a forecast run, from exactly the feature
rows the model predicted from (`x` at the origin, the Telegram rows at the
origin), so nothing after the origin is used:

  * Telegram effect: the history-only probability next to the served one. When
    the history + Telegram model served, `p_hist` is what the history-only model
    (always loaded as the fallback) says for the same row; when it did not serve,
    `text` says why (not selected for this target and horizon, no fresh archive,
    no saved text model, text layer off).
  * Drivers: LightGBM TreeSHAP contributions (`pred_contrib=True`) of the served
    booster, summed into a few groups (GROUPS). They are additive on the
    log-odds scale: base + sum of the groups = logit(p) exactly, where `base` is
    the booster's expected raw score. They are NOT percentage points: a group's
    effect on the probability depends on where the others put it. exp(group) is
    the factor by which that group multiplied the odds.
  * Live signals: a few Telegram feature values of the region at the origin
    (SIGNAL_COLS; counts and minutes, never post text). `signals()` turns them
    into short codes for the UI.

predict_now.py logs `frame()` rows to the point-in-time store (kind
`explanations`, one row per region x target x horizon); the web engine serves
them with the forecast they belong to (`payload()`). Computing them costs a few
milliseconds per run (16 small pred_contrib calls on 24 rows).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

GROUPS = ("alert_now", "own_recent", "other_oblasts", "calendar", "tg_region", "tg_national", "news", "other")
# history features by group (airraid.features); the per-region spatial columns are matched by prefix
ALERT_NOW = {"active_now", "elapsed_min"}
OWN_RECENT = {"since_end_min", "starts_6h", "starts_24h", "starts_7d", "frac_24h", "frac_7d", "frac_28d",
              "onset_last_h"} | {f"lag_{j}h" for j in range(1, 25)}
OTHER_OBLASTS = {"nat_active_now", "nat_starts_1h", "nat_starts_3h", "nat_frac_24h"}
CALENDAR = {"target_hour_local", "target_dow", "hod_clim_7d", "hod_clim_28d", "onset_clim_7d", "onset_clim_28d",
            "region"}
# Telegram feature values kept per region for the live signals (airraid.text.features)
SIGNAL_COLS = (
    "tg_reg_drone_60m", "tg_reg_missile_60m", "tg_reg_ballistic_60m", "tg_reg_kab_60m",
    "tg_reg_drone_180m", "tg_reg_missile_180m", "tg_reg_ballistic_180m", "tg_reg_kab_180m",
    "tg_reg_clear_60m", "tg_reg_any_60m", "tg_reg_any_180m", "tg_reg_min_since_threat",
    "tg_min_since_platform", "tg_min_since_national", "tg_nat_launch_60m", "tg_nat_massed_180m",
    "tg_nat_explosion_60m", "tg_nat_regions_60m", "tg_summary_drones", "tg_summary_missiles", "tg_summary_age_h",
)
THREATS = ("ballistic", "missile", "kab", "drone")  # strongest first, as in the lexicon
RECENT_MIN = 180  # a national event counts as a live signal for 3 h
MAX_SIGNALS = 4


def region_key(region: str) -> str:
    """Suffix of the spatial columns of a region (airraid.features: now__<key>, lag1__<key>)."""
    return region.replace(" oblast", "").replace(" ", "_")


def group_of(col: str, own: str | None = None) -> str:
    """Group of a feature column; `own` is the region_key of the row (its own spatial columns)."""
    if col in ALERT_NOW:
        return "alert_now"
    if col in OWN_RECENT:
        return "own_recent"
    if col in OTHER_OBLASTS:
        return "other_oblasts"
    if col.startswith(("now__", "lag1__")):
        key = col.split("__", 1)[1]
        if own is not None and key == own:
            return "alert_now" if col.startswith("now__") else "own_recent"
        return "other_oblasts"
    if col in CALENDAR:
        return "calendar"
    if col.startswith("tg_reg_"):
        return "tg_region"
    if col.startswith("tg_"):
        return "tg_national"
    if col.startswith("news_"):
        return "news"
    return "other"


def grouped(contrib: np.ndarray, cols: list[str], regions) -> pd.DataFrame:
    """Sum the per-feature contributions [n, len(cols) + 1] (last column: bias) into GROUPS.

    Columns `base` (bias) and one per group; a group without any feature in the
    model is NaN (e.g. Telegram groups for the history-only model)."""
    contrib = np.asarray(contrib, np.float64)
    if contrib.shape[1] != len(cols) + 1:
        raise ValueError("contributions do not match the feature columns")
    base_map = [group_of(c) for c in cols]
    present = set(base_map) | {"alert_now", "own_recent"}  # own spatial columns move there per row
    out = {g: np.zeros(len(contrib)) for g in GROUPS}
    for j, g in enumerate(base_map):
        out[g] += contrib[:, j]
    # a region's own now__/lag1__ column is its own state, not "other oblasts"
    idx = {c: j for j, c in enumerate(cols)}
    for i, r in enumerate(regions):
        k = region_key(r)
        for col, g in ((f"now__{k}", "alert_now"), (f"lag1__{k}", "own_recent")):
            j = idx.get(col)
            if j is not None:
                out["other_oblasts"][i] -= contrib[i, j]
                out[g][i] += contrib[i, j]
    df = pd.DataFrame({"base": contrib[:, -1]})
    for g in GROUPS:
        df[f"g_{g}"] = out[g] if g in present else np.nan
    return df


def _contributions(g, X: pd.DataFrame) -> np.ndarray:
    names = g.model.feature_name()
    if list(X.columns) != names:
        raise RuntimeError("feature order differs from the booster's")
    return g.model.predict(X, num_iteration=g.n_estimators, pred_contrib=True)


def text_reason(text, t: str, h: int, rows: pd.DataFrame | None, served: str) -> str:
    """Why the history + Telegram model did or did not serve this target and horizon (a short code)."""
    if served == "lightgbm_text":
        return "served"
    if text is None:
        return "off"
    if (t, h) not in text.enabled():
        return "not_selected"
    if (t, h) not in text.models:
        return "no_model"
    if rows is None:
        return "no_archive"
    return "feature_mismatch"


def explain(x: pd.DataFrame, t: str, h: int, fallback, p: np.ndarray, served: str, text=None,
            text_rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Explanation rows of one (target, horizon): the rows `x` the models saw at the origin,
    the history-only model `fallback`, the served probability `p` and model name `served`
    (as returned by TextServing.predict / GBM.predict), and the text layer.

    Columns: region, target, horizon, model, p, p_hist, text, base, g_<group>..., and the
    region's SIGNAL_COLS at the origin (NaN without Telegram rows).
    """
    regions = x["region"].to_numpy()
    p_hist = np.asarray(p, np.float64) if served == "lightgbm" else np.asarray(fallback.predict(x), np.float64)
    if served == "lightgbm_text":
        from .text import features as tf

        g = text.models[(t, h)]
        xt = tf.attach(x, text_rows)
        names = [c for c in g.model.feature_name() if c != "region"]
        X = g._X(xt[["origin_hour", "region", *names]])
    else:
        g = fallback
        X = g._X(x)
    contrib = _contributions(g, X)
    out = grouped(contrib, list(X.columns), regions)
    raw = contrib.sum(axis=1)
    p_model = 1 / (1 + np.exp(-raw))
    if not np.allclose(p_model, np.asarray(p, np.float64), atol=1e-6):
        raise RuntimeError("contributions do not add up to the served probability")
    out.insert(0, "region", regions)
    out.insert(1, "target", t)
    out.insert(2, "horizon", h)
    out.insert(3, "model", served)
    out.insert(4, "p", np.asarray(p, np.float64))
    out.insert(5, "p_hist", p_hist)
    out.insert(6, "text", text_reason(text, t, h, text_rows, served))
    sig = signal_values(text_rows, regions)
    for c in SIGNAL_COLS:
        out[c] = sig[c].to_numpy()
    return out


def signal_values(text_rows: pd.DataFrame | None, regions) -> pd.DataFrame:
    """SIGNAL_COLS of each region at the origin (NaN when there are no Telegram rows)."""
    regions = list(regions)
    if text_rows is None or not len(text_rows):
        return pd.DataFrame({c: np.full(len(regions), np.nan) for c in SIGNAL_COLS})
    last = text_rows.sort_values("origin_hour").drop_duplicates("region", keep="last").set_index("region")
    out = {}
    for c in SIGNAL_COLS:
        s = last[c] if c in last else pd.Series(np.nan, index=last.index)
        out[c] = s.reindex(regions).to_numpy(np.float64)
    return pd.DataFrame(out)


def frame(parts: list[pd.DataFrame], origin: pd.Timestamp) -> pd.DataFrame:
    """One run's explanation rows (from `explain`) with the origin, compact for the store."""
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df.insert(0, "origin", origin)
    num = ["p", "p_hist", "base", *(f"g_{g}" for g in GROUPS)]
    df[num] = df[num].astype(np.float64).round(4).astype(np.float32)  # ~30 KB per run in the store
    df[list(SIGNAL_COLS)] = df[list(SIGNAL_COLS)].astype(np.float32)
    return df


# ---------------------------------------------------------------------- serving
def _num(v, nd: int | None = None) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return (round(v, nd) if nd is not None else v) if math.isfinite(v) else None


def signals(row) -> list[dict]:
    """Up to MAX_SIGNALS live Telegram signals of a region from its SIGNAL_COLS values (a mapping).

    Regional mentions first (threat named in the last hour, else the last 3 h; all-clear;
    "not named"), then national ones (take-offs, nationwide threat, launches, massed attack,
    explosions, how many oblasts were named, the morning summary). Empty without Telegram rows.
    """
    v = {c: _num(row.get(c)) for c in SIGNAL_COLS}
    if v["tg_reg_any_180m"] is None:
        return []
    out = []
    named = False
    for th in THREATS:
        n60, n180 = v[f"tg_reg_{th}_60m"] or 0, v[f"tg_reg_{th}_180m"] or 0
        if n60 > 0:
            out.append({"code": "reg_threat", "threat": th, "n": int(n60), "w": 60})
        elif n180 > 0:
            out.append({"code": "reg_threat", "threat": th, "n": int(n180), "w": 180})
    named = bool(out)
    out = out[:2]
    if (v["tg_reg_clear_60m"] or 0) > 0:
        out.append({"code": "reg_clear", "n": int(v["tg_reg_clear_60m"]), "w": 60})
    if not named:
        ms = v["tg_reg_min_since_threat"]
        if ms is not None and ms < 1440:
            out.append({"code": "reg_last", "min": int(round(ms))})
        elif (v["tg_reg_any_180m"] or 0) == 0:
            out.append({"code": "reg_quiet", "w": 180})
    for col, code in (("tg_min_since_platform", "platform"), ("tg_min_since_national", "national")):
        m = v[col]
        if m is not None and m < RECENT_MIN:
            out.append({"code": code, "min": int(round(m))})
    for col, code, w in (("tg_nat_launch_60m", "launch", 60), ("tg_nat_massed_180m", "massed", 180),
                         ("tg_nat_explosion_60m", "explosion", 60)):
        if (v[col] or 0) > 0:
            out.append({"code": code, "n": int(v[col]), "w": w})
    if (v["tg_nat_regions_60m"] or 0) > 0:
        out.append({"code": "nat_regions", "n": int(v["tg_nat_regions_60m"]), "w": 60})
    age = v["tg_summary_age_h"]
    if age is not None and age <= 36 and ((v["tg_summary_drones"] or 0) > 0 or (v["tg_summary_missiles"] or 0) > 0):
        out.append({"code": "summary", "drones": int(v["tg_summary_drones"] or 0),
                    "missiles": int(v["tg_summary_missiles"] or 0), "age_h": int(round(age))})
    return out[:MAX_SIGNALS]


def payload(rows: pd.DataFrame) -> dict | None:
    """The "why" of one region for the API, from its explanation rows (all targets and horizons).

    {"items": {target: {horizon: {model, p, p_hist, text, base, groups: {group: log-odds}}}},
     "signals": [...], "has_text": bool}. Group values are log-odds contributions (see the
    module docstring); groups the served model does not have are left out.
    """
    if rows is None or not len(rows):
        return None
    items: dict = {}
    for r in rows.to_dict("records"):
        groups = {g: round(float(r[f"g_{g}"]), 4) for g in GROUPS
                  if f"g_{g}" in r and _num(r[f"g_{g}"]) is not None}
        items.setdefault(str(r["target"]), {})[str(int(r["horizon"]))] = {
            "model": r["model"], "p": _num(r["p"], 4), "p_hist": _num(r["p_hist"], 4), "text": r["text"],
            "base": _num(r["base"], 4), "groups": groups,
        }
    first = rows.iloc[0].to_dict()
    return {"items": items, "signals": signals(first), "has_text": _num(first.get("tg_reg_any_180m")) is not None}
