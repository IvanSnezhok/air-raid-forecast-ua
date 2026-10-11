"""Probabilistic forecast metrics."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

EPS = 1e-3  # clip so a 0/1 persistence forecast gets a finite (but harsh) log loss


def scores(y: np.ndarray, p: np.ndarray, p_ref: np.ndarray | None = None) -> dict:
    p = np.clip(p, EPS, 1 - EPS)
    out = {
        "brier": brier_score_loss(y, p),
        "log_loss": log_loss(y, p, labels=[0, 1]),
        "roc_auc": roc_auc_score(y, p) if 0 < y.mean() < 1 else np.nan,
        "pr_auc": average_precision_score(y, p) if y.sum() else np.nan,
        "base_rate": float(y.mean()),
        "mean_pred": float(p.mean()),
    }
    if p_ref is not None:
        out["bss_vs_clim"] = 1 - out["brier"] / brier_score_loss(y, np.clip(p_ref, EPS, 1 - EPS))
    return out


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 50:
            rows.append((p[m].mean(), y[m].mean(), int(m.sum())))
    return np.array(rows)
