"""No-leakage checks for the Telegram features of the main model (offline, synthetic posts).

* truncation: features at T computed from the posts available before T equal the
  features computed from the full archive;
* mutation: a post published at T or later never changes the features at T, one
  published a minute before T does, and the check fails if the strict "< T" is
  turned into "<= T";
* edits: a post that was already edited when we first saw it counts only from that
  sighting, and the first-seen text is never overwritten;
* coverage: origins outside the collected period are NaN with tg_covered = 0, never 0;
* parity: run.py's path (whole archive) and the live path (recent partitions) give
  the same rows; row order matches build_features.
"""
import numpy as np
import pandas as pd
import pytest

from airraid.text import archive, features as tf, live
from airraid.text.lexicon import classify

T0 = pd.Timestamp("2026-01-01", tz="UTC")  # grid t0 used by the tests
REGIONS = ["Kharkivska oblast", "Kyiv City", "Odeska oblast", "Sumska oblast"]
TEMPLATES = [
    "Група ударних БпЛА курсом на Харків",
    "Зліт МіГ-31К, загроза застосування балістичного озброєння",
    "Крилаті ракети в напрямку Києва",
    "КАБи на Сумщину",
    "Відбій загрози на Одещині",
    "Продовжує рух на Київ!",
    "Шахеди над Одесою",
    "Ту-95 в повітрі, можливі пуски",
    "Масована атака: пуски шахедів з Брянська",
    "У ніч на 5 січня противник атакував 120 ударними БпЛА та 4 крилатими ракетами",
    "Новини дня без загроз",
]
COMPLETE = ("2026-01-01T00:00:00Z", "2026-01-12T00:00:00Z")


def _obs(n, seed, channel):
    rng = np.random.default_rng(seed)
    # from late December, so the archive spans two month partitions
    date = T0 + pd.to_timedelta(np.sort(rng.uniform(-5 * 24 * 60, 11 * 24 * 60, n)), unit="min").round("s")
    # some posts land exactly on the hour and seconds after it, where an off-by-one in a window would show
    on_hour = rng.random(n) < 0.3
    date = date.where(~on_hour, date.floor("h") + pd.to_timedelta(rng.integers(0, 90, n) * on_hour, unit="s"))
    edited = rng.random(n) < 0.15
    live_ = rng.random(n) < 0.3
    seen = np.where(live_, date + pd.to_timedelta(rng.uniform(0, 50, n), unit="min"),
                    pd.Timestamp("2026-02-01", tz="UTC"))
    return pd.DataFrame({
        "channel": channel, "id": np.arange(1, n + 1), "date": date, "edited": edited,
        "text": rng.choice(TEMPLATES, n), "seen_at": pd.to_datetime(seen, utc=True).round("s"),
    })


@pytest.fixture()
def root(tmp_path):
    for i, ch in enumerate(("kpszsu", "deraketaua")):
        archive.upsert(tmp_path, "telegram", ch, _obs(900, i, ch))
        archive.write_state(tmp_path, "telegram", ch, {"complete_from": COMPLETE[0], "complete_until": COMPLETE[1]})
    return tmp_path


CHANNELS = ["kpszsu", "deraketaua"]


def _features(posts, K):
    return tf.build_text_features(T0, np.asarray(K), REGIONS, posts)


def _origins(n=12, seed=0):
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(np.arange(2 * 24 + 1, 11 * 24), n, replace=False))


def _at(F, k):
    return F[F["origin_hour"] == k].reset_index(drop=True)


def _truncate(posts, T):
    cut = posts[posts["available_at"] < T]
    cut.attrs["coverage"] = posts.attrs["coverage"]
    return cut


@pytest.mark.parametrize("rule", tf.RULES)
def test_features_at_T_use_only_posts_available_before_T(root, rule):
    posts = tf.load_posts(root, CHANNELS, rule=rule)
    K = _origins()
    full = _features(posts, K)
    for k in K:
        T = T0 + pd.Timedelta(hours=int(k))
        # re-classify the truncated archive from scratch, as the live path would
        raw = archive.load(root, "telegram", CHANNELS)
        if rule == "unedited_only":
            raw = raw[~(raw["edited_first"] | raw["edited_seen"])]
        elif rule == "permissive":
            raw = raw.assign(available_at=raw["date"], text_first=raw["text_last"])
        cut = tf.classify_posts(raw[raw["available_at"] < T])
        cut.attrs["coverage"] = posts.attrs["coverage"]
        pd.testing.assert_frame_equal(_at(full, k), _features(cut, [k]))


def _inject(root, minutes, text="Шахеди курсом на Одесу", channel="kpszsu", edited=False, seen_after=0):
    """Add one post published `minutes` after the test origin; return the origin hour."""
    k = 5 * 24 + 7
    T = T0 + pd.Timedelta(hours=k)
    d = T + pd.Timedelta(minutes=minutes)
    obs = pd.DataFrame({"channel": [channel], "id": [10_000], "date": [d], "edited": [edited], "text": [text],
                        "seen_at": [d + pd.Timedelta(minutes=seen_after)]})
    archive.upsert(root, "telegram", channel, obs)
    return k


@pytest.mark.parametrize("minutes", [0, 1, 60])
def test_post_at_or_after_origin_changes_nothing(root, minutes):
    k = 5 * 24 + 7
    before = _features(tf.load_posts(root, CHANNELS), [k])
    _inject(root, minutes)
    after = _features(tf.load_posts(root, CHANNELS), [k])
    pd.testing.assert_frame_equal(before, after)


def test_post_just_before_origin_is_used(root):
    k = 5 * 24 + 7
    before = _features(tf.load_posts(root, CHANNELS), [k])
    _inject(root, -1)
    after = _features(tf.load_posts(root, CHANNELS), [k])
    odesa = after["region"] == "Odeska oblast"
    assert (after.loc[odesa, "tg_reg_drone_30m"].to_numpy() == before.loc[odesa, "tg_reg_drone_30m"].to_numpy() + 1).all()
    assert after.loc[odesa, "tg_reg_min_since_threat"].iloc[0] == 1


def test_mutation_check_catches_a_leaky_window(root, monkeypatch):
    """Turning the strict "< T" into "<= T" must be caught by the check above."""
    def leaky_count(times, T, w):
        return (np.searchsorted(times, T, "right") - np.searchsorted(times, T - w * tf.NS_MIN, "right")).astype(np.float32)

    monkeypatch.setattr(tf, "_count", leaky_count)
    k = 5 * 24 + 7
    before = _features(tf.load_posts(root, CHANNELS), [k])
    _inject(root, 0)
    after = _features(tf.load_posts(root, CHANNELS), [k])
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(before, after)


def test_mutation_check_catches_a_leaky_min_since(root, monkeypatch):
    def leaky_min_since(times, T, cap=tf.CAP_MIN):
        i = np.searchsorted(times, T, "right") - 1
        out = np.full(T.shape, cap, np.float32)
        ok = i >= 0
        out[ok] = np.minimum((T[ok] - times[i[ok]]) / tf.NS_MIN, cap)
        return out

    monkeypatch.setattr(tf, "_min_since", leaky_min_since)
    k = 5 * 24 + 7
    before = _features(tf.load_posts(root, CHANNELS), [k])
    _inject(root, 0)
    after = _features(tf.load_posts(root, CHANNELS), [k])
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(before, after)


def test_post_edited_before_first_sighting_counts_from_the_sighting(root):
    k = 5 * 24 + 7
    before = _features(tf.load_posts(root, CHANNELS), [k])
    # published 30 min before T, but already edited when we saw it 5 min after T
    _inject(root, -30, edited=True, seen_after=35)
    posts = tf.load_posts(root, CHANNELS)
    p = posts[posts["id"] == 10_000].iloc[0]
    assert p["available_at"] == p["first_seen_at"] > T0 + pd.Timedelta(hours=k)
    pd.testing.assert_frame_equal(before, _features(posts, [k]))
    # the permissive variant would use it: that is exactly the leak the rule prevents
    perm = _features(tf.load_posts(root, CHANNELS, rule="permissive"), [k])
    assert (perm["tg_reg_drone_60m"] != before["tg_reg_drone_60m"]).any()


def test_first_seen_text_is_never_overwritten(tmp_path):
    d = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    o1 = pd.DataFrame({"channel": ["c"], "id": [1], "date": [d], "edited": [False], "text": ["БпЛА на Харків"],
                       "seen_at": [d + pd.Timedelta(minutes=3)]})
    archive.upsert(tmp_path, "telegram", "c", o1)
    o2 = o1.assign(edited=True, text="UPD: ракети на Київ", seen_at=d + pd.Timedelta(hours=2))
    archive.upsert(tmp_path, "telegram", "c", o2)
    r = archive.read_records(tmp_path, "telegram", "c").iloc[0]
    assert r["text_first"] == "БпЛА на Харків" and r["text_last"] == "UPD: ракети на Київ"
    assert not r["edited_first"] and r["edited_seen"]
    assert r["first_seen_at"] == d + pd.Timedelta(minutes=3) and r["last_seen_at"] == d + pd.Timedelta(hours=2)
    assert r["collected_mode"] == "live"
    # the original text counts from the publish time; the edit is never used as a feature
    assert archive.available_at(archive.read_records(tmp_path, "telegram", "c")).iloc[0] == d
    # an old sighting arriving late does not roll text_last back
    archive.upsert(tmp_path, "telegram", "c", o1.assign(seen_at=d + pd.Timedelta(minutes=10)))
    r = archive.read_records(tmp_path, "telegram", "c").iloc[0]
    assert r["text_last"] == "UPD: ракети на Київ" and r["text_first"] == "БпЛА на Харків"


def test_uncovered_origins_are_nan_not_zero(root):
    st = archive.read_state(root, "telegram", "kpszsu")
    st["gaps"] = [["2026-01-06T00:00:00Z", "2026-01-06T05:00:00Z"]]
    archive.write_state(root, "telegram", "kpszsu", st)
    posts = tf.load_posts(root, CHANNELS)
    K = np.array([10, 2 * 24 + 1, 5 * 24 + 2, 6 * 24 + 4, 7 * 24 + 6, 11 * 24 + 1, 20 * 24])
    F = _features(posts, K)
    cols = tf.text_columns(F)
    cols.remove("tg_covered")
    expect = {10: 0, 2 * 24 + 1: 1, 5 * 24 + 2: 0, 6 * 24 + 4: 0, 7 * 24 + 6: 1, 11 * 24 + 1: 0, 20 * 24: 0}
    for k, ok in expect.items():
        row = _at(F, k)
        assert (row["tg_covered"] == ok).all(), k
        if ok:
            assert row[cols].notna().all().all()
        else:
            assert row[cols].isna().all().all()
    # a channel with no state at all covers nothing
    empty = tf.load_posts(root, CHANNELS + ["never_collected"])
    assert _features(empty, [3 * 24])["tg_covered"].eq(0).all()


def test_live_path_matches_evaluation_path(root):
    posts = tf.load_posts(root, CHANNELS)
    K = _origins(8, seed=3)
    full = _features(posts, K)
    for k in K:
        rows, status = live.text_rows_at(T0, np.array([k]), REGIONS, root, fetch_new=False, channels=CHANNELS)
        assert status == "ok"
        pd.testing.assert_frame_equal(_at(full, k), rows.reset_index(drop=True))
    rows, status = live.text_rows_at(T0, np.array([30 * 24]), REGIONS, root, fetch_new=False, channels=CHANNELS)
    assert rows is None and "does not cover" in status


def test_row_order_matches_build_features():
    from airraid.data import build_grid
    from airraid.features import build_features

    alerts = pd.DataFrame({"region": ["Kyiv City", "Odeska oblast"] * 40,
                           "start": T0 + pd.to_timedelta(np.arange(80) * 7, unit="h"),
                           "naive": False})
    alerts["end"] = alerts["start"] + pd.Timedelta(minutes=40)
    grid = build_grid(alerts, data_end=T0 + pd.Timedelta(days=40))
    K = np.arange(29 * 24, 30 * 24)
    base = build_features(grid, K)
    text = tf.build_text_features(grid.t0, K, grid.regions, tf.classify_posts(archive.load(".", "telegram", [])))
    out = tf.attach(base, text)
    assert len(out) == len(base) and out["tg_covered"].eq(0).all()
    with pytest.raises(ValueError):
        tf.attach(base, text.iloc[::-1].reset_index(drop=True))


@pytest.mark.parametrize("text,region,threat", [
    ("Група ударних БпЛА курсом на Харків", "Kharkivska oblast", "drone"),
    ("Х-101 в напрямку Києва", "Kyiv City", "missile"),
    ("КАБи на Сумщину", "Sumska oblast", "kab"),
    ("Балістика на Миколаїв", "Mykolaivska oblast", "ballistic"),
    ("2 реактиви на Димер, Київщина", "Kyivska oblast", "drone"),
])
def test_lexicon(text, region, threat):
    c = classify(text)
    assert region in c["regions"] and threat in c["threats"]


def test_lexicon_edge_cases():
    assert classify("Рівно о 12:00, сума збору зросла")["regions"] == []
    c = classify("Відбій загрози балістики на Київщині")
    assert c["all_clear"] and c["threats"] == []
    s = classify("У ніч на 5 жовтня противник атакував 98 ударними БпЛА та 4 крилатими ракетами Х-101")
    assert s["summary"] and s["drones_reported"] == 98 and s["missiles_reported"] == 4
    assert classify("Зліт МіГ-31К")["platform"]
