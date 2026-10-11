"""Text features at the forecast origin, for the live forecast (predict_now.py, web engine).

    rows, status = text_rows_at(t0, K, regions, root="text-data", fetch=True)

`root` is a checkout of the `text-data` branch (or any folder: it is created if
missing). With fetch=True the newest posts are fetched first and *stored* in the
archive, so every post the live forecast used is kept with the time we saw it.
The rows are built by exactly the code run.py evaluates (features.py); only the
posts from the last few days are read, which cannot change the features (the
longest look-back is 36 h). tests/test_text_leakage.py checks the parity.

When the archive does not cover the origin (fetch failed, collection stale or
missing) the function returns (None, reason) and the caller must fall back to
the history-only model. It never fills the gap with zeros.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import archive, features, telegram

LOOKBACK = pd.Timedelta(days=3)


def recent_months(t_min: pd.Timestamp, t_max: pd.Timestamp) -> list[str]:
    return [m.strftime("%Y-%m") for m in pd.period_range(t_min.tz_convert(None).to_period("M"),
                                                         t_max.tz_convert(None).to_period("M"), freq="M")]


def load_recent_posts(root: Path | str, t_min: pd.Timestamp, t_max: pd.Timestamp,
                      channels: list[str] | None = None) -> pd.DataFrame:
    """Posts published from the month of t_min on (a superset of those available after t_min)."""
    channels = channels or telegram.FEATURE_CHANNELS
    months = recent_months(t_min, t_max + pd.Timedelta(days=1))
    frames = []
    for ch in channels:
        r = archive.read_records(Path(root), "telegram", ch, months)
        if len(r):
            frames.append(r)
    df = pd.concat(frames, ignore_index=True) if frames else archive.read_records(Path(root), "telegram", "_none_")
    df = df.rename(columns={"channel": "source"})
    df["available_at"] = archive.available_at(df, "telegram")
    df["kind"] = "telegram"
    df = df.sort_values("available_at", kind="stable").reset_index(drop=True)
    cov = {ch: archive.coverage(archive.read_state(Path(root), "telegram", ch)) for ch in channels}
    df = features.classify_posts(df)
    df.attrs["coverage"] = cov
    return df


def fetch(root: Path | str, channels: list[str] | None = None, log=print) -> dict[str, str]:
    """Fetch the newest posts into the archive; returns per-channel errors (empty = all fine)."""
    errors = {}
    for ch in channels or telegram.FEATURE_CHANNELS:
        try:
            telegram.update(Path(root), ch, max_pages=60, log=log)
        except Exception as e:
            errors[ch] = repr(e)
            log(f"text: could not fetch {ch}: {e!r}")
    return errors


def text_rows_at(t0: pd.Timestamp, K: np.ndarray, regions: list[str], root: Path | str = "text-data",
                 fetch_new: bool = True, channels: list[str] | None = None, log=print):
    """Text feature rows for origin hours K (region-major), or (None, reason)."""
    root = Path(root)
    if fetch_new:
        errors = fetch(root, channels, log=log)
        if errors:
            log(f"text: fetch errors {errors}")
    K = np.asarray(K, np.int64)
    T = features.origins_ns(t0, K)
    t_min = pd.Timestamp(int(T.min()), tz="UTC") - LOOKBACK
    t_max = pd.Timestamp(int(T.max()), tz="UTC")
    posts = load_recent_posts(root, t_min, t_max, channels)
    rows = features.build_text_features(t0, K, regions, posts)
    if (rows["tg_covered"] == 0).any():
        cov = {k: (str(a), str(b)) for k, (a, b, _) in posts.attrs["coverage"].items()}
        return None, f"text archive does not cover {t_max} (coverage {cov})"
    return rows, "ok"
