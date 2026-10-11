"""Type-model features at origin T must not change when everything after T is removed.

Synthetic Air Force posts are generated around real alert starts, then for
several origins T we cut the data the way it looked at T (posts published at or
after T dropped, alerts started at or after T dropped, ends after T unknown,
labels known only from start + AFTER) and compare features.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
from airraid.data import load_alerts  # noqa: E402
from features import build  # noqa: E402
from labels import AFTER, label_onsets, onsets  # noqa: E402
from threat_lexicon import classify  # noqa: E402

UA = {"Kyiv City": "Київ", "Odeska oblast": "Одещини", "Sumska oblast": "Сумщини", "Kharkivska oblast": "Харківщини"}
TEXTS = ["БпЛА на {}", "Ракета на {}", "Пуски КАБ на {}", "Швидкісна ціль на {}"]


def _posts(alerts, rng):
    rows = []
    for r, s in zip(alerts["region"], alerts["start"]):
        if r in UA:
            t = TEXTS[rng.integers(len(TEXTS))].format(UA[r])
            rows.append({"date": s + pd.Timedelta(minutes=int(rng.integers(-25, 15))), "text": t})
    rows.append({"date": alerts["start"].iloc[len(alerts) // 2], "text": "Загроза застосування балістичного озброєння з півдня"})
    # morning summaries are published after the night they describe
    for d in pd.date_range(alerts["start"].min().normalize(), alerts["start"].max(), freq="D"):
        rows.append({"date": d + pd.Timedelta(hours=6), "text": f"У ніч на 5 жовтня противник атакував {int(rng.integers(20, 300))} ударними БпЛА"})
    p = pd.DataFrame(rows)
    info = p["text"].map(classify)
    for k in ("threats", "regions", "national", "summary", "all_clear", "drones_reported"):
        p[k] = info.map(lambda d: d[k])
    return p.sort_values("date").reset_index(drop=True)


@pytest.fixture(scope="module")
def data():
    a = load_alerts()
    a = a[a["start"] >= a["start"].max() - pd.Timedelta(days=60)].reset_index(drop=True)
    return a, _posts(a, np.random.default_rng(0))


def _as_of(alerts, posts, T):
    a = alerts[alerts["start"] < T].copy()
    a.loc[a["end"] >= T, "end"] = pd.Timestamp("2100-01-01", tz="UTC")  # not yet known
    p = posts[posts["date"] < T]
    lab = label_onsets(onsets(a), p)
    lab = lab[lab["start"] + AFTER < T]
    return a, p, lab


@pytest.mark.parametrize("seed", range(5))
def test_features_do_not_see_the_future(data, seed):
    alerts, posts = data
    rng = np.random.default_rng(seed)
    T = (alerts["start"].min() + pd.Timedelta(days=int(rng.integers(20, 55)), hours=int(rng.integers(0, 24)))).floor("h")
    rows = pd.DataFrame({"origin": [T] * len(UA), "region": list(UA)})
    full_lab = label_onsets(onsets(alerts), posts)
    full = build(rows, full_lab, alerts, posts)
    a, p, lab = _as_of(alerts, posts, T)
    cut = build(rows, lab, a, p)
    pd.testing.assert_frame_equal(full.drop(columns="region"), cut.drop(columns="region"), check_exact=False, rtol=1e-6)
