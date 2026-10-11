import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import build_text_features, prepare  # noqa: E402
from lexicon import tag  # noqa: E402

TEMPLATES = [
    "Група ударних БпЛА курсом на Харків",
    "Зліт МіГ-31К, загроза Кинджалів",
    "Крилаті ракети в напрямку Києва",
    "КАБи на Сумщину",
    "Відбій тривоги на Київщині",
    "Шахеди над Дніпром, рухаються на Полтаву",
    "Ту-95 в повітрі, можливі пуски",
    "Новини дня без загроз",
]


def synthetic(n=600, seed=0):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2025-01-01", tz="UTC")
    date = t0 + pd.to_timedelta(rng.uniform(0, 72 * 60, n), unit="min")
    edited = rng.random(n) < 0.2
    edit = date + pd.to_timedelta(rng.uniform(0, 30, n), unit="min")
    live = rng.random(n) < 0.3
    df = pd.DataFrame({
        "channel": rng.choice(["a", "b"], n),
        "id": np.arange(n),
        "date": date,
        "edit_date": pd.Series(edit).where(edited, pd.NaT),
        "text": rng.choice(TEMPLATES, n),
        "collected_at": pd.Series(date + pd.to_timedelta(rng.uniform(0, 5, n), unit="min")).where(
            live, pd.Timestamp("2025-02-01", tz="UTC")),
        "mode": np.where(live, "live", "backfill"),
    })
    return prepare(df)


COV = (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-01-04", tz="UTC"))


def test_features_ignore_posts_available_at_or_after_origin():
    msgs = synthetic()
    origins = pd.date_range("2025-01-01 03:00", "2025-01-03 23:00", freq="7h", tz="UTC")
    full = build_text_features(msgs, origins, coverage=COV)
    for T in origins:
        trunc = msgs[msgs["available_at"] < T]
        a = full[full["origin"] == T].reset_index(drop=True)
        b = build_text_features(trunc, [T], coverage=COV).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)


def test_post_at_exactly_origin_is_excluded():
    T = pd.Timestamp("2025-01-01 12:00", tz="UTC")
    msgs = prepare(pd.DataFrame({
        "channel": ["a"], "id": [1], "date": [T], "edit_date": [pd.NaT],
        "text": ["Шахеди курсом на Харків"], "collected_at": [T], "mode": ["backfill"],
    }))
    f = build_text_features(msgs, [T, T + pd.Timedelta(minutes=1)], ["Kharkivska oblast"], coverage=COV)
    assert f["tg_reg_drone_30m"].tolist() == [0, 1]


def test_late_edit_delays_availability():
    d = pd.Timestamp("2025-01-01 12:00", tz="UTC")
    msgs = prepare(pd.DataFrame({
        "channel": ["a"], "id": [1], "date": [d], "edit_date": [d + pd.Timedelta(minutes=20)],
        "text": ["UPD: ракети на Київ"], "collected_at": [d + pd.Timedelta(days=30)], "mode": ["backfill"],
    }))
    assert msgs["available_at"].iloc[0] == d + pd.Timedelta(minutes=20)


def test_uncovered_origins_are_nan_not_zero():
    msgs = synthetic(50)
    f = build_text_features(msgs, [pd.Timestamp("2024-12-31", tz="UTC")], coverage=COV)
    assert f["tg_covered"].eq(0).all()
    assert f["tg_msgs_60m"].isna().all()


@pytest.mark.parametrize("text,region,threat", [
    ("Група ударних БпЛА курсом на Харків", "Kharkivska oblast", "drone"),
    ("Х-101 в напрямку Києва", "Kyiv City", "cruise"),
    ("Відбій тривоги на Київщині", "Kyivska oblast", "all_clear"),
    ("КАБи на Сумщину", "Sumska oblast", "kab"),
    ("Балістика на Миколаїв", "Mykolaivska oblast", "ballistic"),
])
def test_tagging(text, region, threat):
    regions, threats = tag(text)
    assert region in regions and threat in threats


def test_ordinary_words_are_not_regions():
    assert tag("Рівно о 12:00, сума збору зросла")[0] == []
