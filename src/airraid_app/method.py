"""How the forecast is made, as data for the How-it-works page (/api/method).

Where the history + Telegram model serves and what it measurably gains, read from
the same reports the serving code uses, so the page stays true when they change:
  * reports/text_selection.json: validation-month Brier of the Telegram model
    (`lightgbm_text`) and of the history-only model on the same rows
    (`lightgbm_cov`); TextServing.enabled() applies the serving rule to it;
  * reports/text_ablation_diff.csv: the 4 test weeks (walk-forward), Brier
    difference with a 95 % day-block bootstrap interval;
  * the channels and news sources the collector knows (airraid.text.telegram, .news),
    and whether the served Telegram boosters use news features.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from airraid.explain import GROUPS
from airraid.features import HORIZONS
from airraid.training import TARGETS

log = logging.getLogger("airraid_app")


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _rel(a, b) -> float | None:
    """Relative change of a Brier score a vs b (negative: a has smaller errors)."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    return round((a - b) / b, 5) if b > 0 else None


def telegram_table(reports_dir: Path) -> list[dict]:
    """Per target x horizon: is the Telegram model used, and its Brier change vs history-only."""
    from airraid.text.serving import TextServing

    reports_dir = Path(reports_dir)
    sel = _read_json(reports_dir / "text_selection.json")
    enabled = TextServing(reports_dir, reports_dir, reports_dir, allow_train=False).enabled()
    diff = None
    p = reports_dir / "text_ablation_diff.csv"
    if p.exists():
        try:
            diff = pd.read_csv(p)
            diff = diff[diff["model"] == "lightgbm_text"]
        except (OSError, ValueError, KeyError):
            log.warning("could not read %s", p)
            diff = None
    rows = []
    for t in TARGETS:
        for h in HORIZONS:
            txt, cov = sel.get(f"lightgbm_text|{t}_h{h}") or {}, sel.get(f"lightgbm_cov|{t}_h{h}") or {}
            row = {"target": t, "horizon": h, "used": (t, h) in enabled,
                   "val_brier_text": txt.get("val_brier"), "val_brier_hist": cov.get("val_brier"),
                   "val_change": _rel(txt.get("val_brier"), cov.get("val_brier")),
                   "test_change": None, "test_ci": None, "test_verdict": None}
            d = diff[(diff["target"] == t) & (diff["horizon_h"] == h)] if diff is not None else None
            if d is not None and len(d):
                r = d.iloc[0]
                lo, hi = float(r["ci95_lo"]), float(r["ci95_hi"])
                row["test_change"] = round(float(r["rel_change_pct"]) / 100, 5)
                row["test_ci"] = [round(lo, 5), round(hi, 5)]  # Brier difference, absolute
                row["test_verdict"] = "better" if hi < 0 else "worse" if lo > 0 else "unclear"
            rows.append(row)
    return rows


def summary(reports_dir: Path, text=None, text_status: str | None = None) -> dict:
    """/api/method: the Telegram table, sources, the groups of the "why" block and the current status."""
    from airraid.text import news, telegram

    reports_dir = Path(reports_dir)
    split = _read_json(reports_dir / "run_summary.json").get("split", {})
    news_in_model = any(c.startswith("news_") for g in getattr(text, "models", {}).values()
                        for c in g.model.feature_name())
    return {
        "telegram": telegram_table(reports_dir),
        "test_period": split.get("test"),
        "channels": [{"id": k, "in_model": bool(v.get("features"))} for k, v in telegram.CHANNELS.items()],
        "news": {"sources": sorted(news.RSS) + ["gdelt"], "in_model": news_in_model},
        "text_loaded": bool(getattr(text, "models", None)),
        "text_status": text_status,
        "groups": [g for g in GROUPS if g != "other"],
    }
