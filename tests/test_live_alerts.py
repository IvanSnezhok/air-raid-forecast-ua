"""Live alerts.in.ua records must enter the model the way the training data does.

Regression tests for the live merge bug: a raion/hromada alert that
alerts.in.ua keeps open for weeks used to mark the whole oblast "under alert
since then", which (a) pushed live features far outside the training range and
(b) leaked into the training grid as fake "alert running, no new onset" rows.
"""
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airraid.data import MAX_ALERT_HOURS, build_grid
from airraid.features import HORIZONS, build_features, hourly_fraction, hourly_starts, labels
from airraid.live_alerts import (
    LIVE_EXPIRY_HOURS, OBLAST_EN, LiveAlert, after_dataset, model_alerts, oblast_intervals, parse_active, to_frame,
)
from airraid_app.journal import LiveRecorder
from airraid_app.live import state_from_alerts
from predict_now import UK_TO_EN, grids

REGIONS = ["Dnipropetrovska oblast", "Kharkivska oblast", "Kyiv City", "Lvivska oblast"]
HIST_END = pd.Timestamp("2026-10-11 04:36", tz="UTC")
ORIGIN = pd.Timestamp("2026-10-11 20:00", tz="UTC")
NOW = ORIGIN + pd.Timedelta(minutes=7)  # a poll a few minutes after the origin


def dataset(days: int = 40, seed: int = 0) -> pd.DataFrame:
    """Oblast-level alerts up to HIST_END: several short alerts a day, like the volunteer file."""
    rng = np.random.default_rng(seed)
    rows = []
    for r, rate in zip(REGIONS, (0.5, 0.6, 0.1, 0.05)):
        t = HIST_END - pd.Timedelta(days=days)
        while t < HIST_END:
            t += pd.Timedelta(minutes=int(rng.exponential(60 / rate)))
            if t < HIST_END:
                rows.append((r, t, t + pd.Timedelta(minutes=int(rng.integers(5, 90))), False))
    rows.append(("Kharkivska oblast", HIST_END, HIST_END + pd.Timedelta(minutes=3), False))
    df = pd.DataFrame(rows, columns=["region", "start", "end", "naive"])
    return df[df["start"] <= HIST_END].sort_values(["region", "start"]).reset_index(drop=True)


def record(oblast_uk: str, started: pd.Timestamp, level: str = "raion", title: str = "Чугуївський район",
           alert_type: str = "air_raid", **kw) -> dict:
    return {"id": zlib.crc32(f"{oblast_uk}{started}{title}".encode()), "location_title": title, "location_type": level,
            "location_uid": "122", "location_oblast": oblast_uk, "started_at": started.isoformat(),
            "finished_at": None, "alert_type": alert_type, **kw}


def live(payload: dict, origin: pd.Timestamp = ORIGIN) -> pd.DataFrame:
    """Records of a poll that started before the origin, as predict_now.live_records keeps them."""
    df = to_frame(parse_active(payload))
    return df[df["started_at"] < origin].assign(finished_at=pd.NaT)


@pytest.fixture(scope="module")
def alerts():
    return dataset()


def features(grid):
    return build_features(grid, np.array([grid.n_minutes // 60]), hourly_fraction(grid)).set_index("region")


# --------------------------------------------------------------- parsing


def test_parse_keeps_all_levels_and_types_and_maps_oblasts():
    payload = {"alerts": [
        record("Харківська область", ORIGIN, "hromada", "Липецька територіальна громада", location_uid="1284"),
        record("Харківська область", ORIGIN, "raion", alert_type="artillery_shelling"),
        record("м. Київ", ORIGIN, "city", "м. Київ"),
        record("Автономна Республіка Крим", ORIGIN, "oblast", "Автономна Республіка Крим",
               location_uid="29"),  # not in the dataset
        {"alert_type": "air_raid", "location_title": "Лозівський район", "location_type": "raion",
         "location_oblast_uid": "22", "started_at": "2026-10-11T20:17:28.786Z"},  # oblast only by UID
    ]}
    a = parse_active(payload)
    assert [x.oblast for x in a] == ["Kharkivska oblast", "Kharkivska oblast", "Kyiv City", "Kharkivska oblast"]
    assert {x.level for x in a} == {"hromada", "raion", "city"}
    assert a[0].raion == "Харківський район"  # derived from the location list
    assert a[3].started_at == pd.Timestamp("2026-10-11 20:17:28.786", tz="UTC")
    assert all(isinstance(x, LiveAlert) for x in a)


def test_one_oblast_name_map():
    from airraid_app.regions import ALERTS_IN_UA

    assert UK_TO_EN is OBLAST_EN and ALERTS_IN_UA is OBLAST_EN


# ------------------------------------------------- definition and 12 h cap


def test_old_raion_alert_does_not_make_the_oblast_active(alerts):
    """The bug: Kharkivska showed "under alert since 2026-03-10" from one hromada."""
    old = ORIGIN - pd.Timedelta(days=5)
    payload = {"alerts": [record("Харківська область", old, "raion"),
                          record("Дніпропетровська область", ORIGIN - pd.Timedelta(days=24), "hromada", "Покровська")]}
    assert model_alerts(parse_active(payload), NOW).empty
    tgrid, fgrid, gap = grids(alerts, HIST_END, ORIGIN, live(payload))
    plain = build_grid(alerts, data_end=ORIGIN)
    np.testing.assert_array_equal(fgrid.active, plain.active)  # nothing added, nothing rewritten
    x, ref = features(fgrid), features(plain)
    assert x.loc["Kharkivska oblast", "active_now"] == 0
    # every feature stays inside what the model saw in training
    K = np.arange(28 * 24, tgrid.n_minutes // 60)
    tr = build_features(tgrid, K, hourly_fraction(tgrid))
    for c in ("frac_24h", "frac_7d", "elapsed_min", "starts_24h", "nat_active_now"):
        assert (x[c].fillna(0) <= np.nanmax(tr[c].to_numpy()) + 1e-6).all(), c
    assert x["frac_24h"].max() < 1
    pd.testing.assert_frame_equal(x, ref)


def test_recent_oblast_alert_is_active_with_correct_elapsed(alerts):
    start = ORIGIN - pd.Timedelta(minutes=50)
    payload = {"alerts": [record("Львівська область", start, "oblast", "Львівська область")]}
    cur = model_alerts(parse_active(payload), NOW)
    assert cur.to_dict("records") == [{"region": "Lvivska oblast", "start": start}]
    _, fgrid, gap = grids(alerts, HIST_END, ORIGIN, live(payload))
    x = features(fgrid)
    assert x.loc["Lvivska oblast", "active_now"] == 1
    assert x.loc["Lvivska oblast", "elapsed_min"] == 50
    assert x.loc[[r for r in REGIONS if r != "Lvivska oblast"], "active_now"].sum() == features(
        build_grid(alerts, data_end=ORIGIN)).drop(index="Lvivska oblast")["active_now"].sum()


@pytest.mark.parametrize("age_h", [3, MAX_ALERT_HOURS + 1])
def test_alert_without_new_activation_expires(alerts, age_h):
    """No new activation for LIVE_EXPIRY_HOURS (chosen from data, <= the 12 h cap
    of load_alerts): over. alerts.in.ua still reports it, for display only."""
    assert LIVE_EXPIRY_HOURS <= MAX_ALERT_HOURS
    start = ORIGIN - pd.Timedelta(hours=age_h)
    payload = {"alerts": [record("Львівська область", start, "raion")]}
    assert model_alerts(parse_active(payload), NOW).empty
    st = state_from_alerts(parse_active(payload), REGIONS, NOW)
    assert not st.regions["Lvivska oblast"].active and st.regions["Lvivska oblast"].reported
    _, fgrid, gap = grids(alerts, HIST_END, ORIGIN, live(payload))
    assert features(fgrid).loc["Lvivska oblast", "active_now"] == 0
    # what is left in the history is the record cut at the expiry, like load_alerts' cap
    lv = gap[gap["region"] == "Lvivska oblast"]
    assert len(lv) == 1 and lv["end"].iloc[0] == start + pd.Timedelta(hours=LIVE_EXPIRY_HOURS)


def test_latest_activation_and_all_clears():
    t = ORIGIN - pd.Timedelta(minutes=50)
    a = parse_active({"alerts": [record("Харківська область", t, title="A"),
                                 record("Харківська область", t + pd.Timedelta(minutes=40), title="B")]})
    assert model_alerts(a, NOW)["start"].tolist() == [t + pd.Timedelta(minutes=40)]
    # an all-clear for any part of the oblast after the latest activation ends the oblast alert
    clr = pd.DataFrame({"region": ["Kharkivska oblast"], "end": [t + pd.Timedelta(minutes=50)]})
    assert model_alerts(a, NOW, cleared=clr).empty
    # ...but not one from before it
    clr["end"] = t + pd.Timedelta(minutes=30)
    assert len(model_alerts(a, NOW, cleared=clr)) == 1


def test_oblast_intervals_follow_the_volunteer_processor():
    T = lambda m: ORIGIN + pd.Timedelta(minutes=m)  # noqa: E731
    rec = pd.DataFrame({"oblast": "X", "alert_type": "air_raid",
                        "started_at": [T(0), T(5), T(60), T(300), T(600)],
                        "finished_at": [T(30), T(20), pd.NaT, pd.NaT, pd.NaT]})
    iv = oblast_intervals(rec, T(700), cap_hours=MAX_ALERT_HOURS)
    got = [((s - ORIGIN) / pd.Timedelta(minutes=1), (e - ORIGIN) / pd.Timedelta(minutes=1), o)
           for s, e, o in zip(iv["start"], iv["end"], iv["open"])]
    # 0 is replaced by 5 (no all-clear in between), which the all-clear at 20 ends;
    # 60 and 300 get no all-clear and the next activation comes > 3 h later, so each
    # becomes a 30-min "naive" alert; 600 is still running (its end is only the cap).
    assert got == [(5, 20, False), (60, 90, False), (300, 330, False), (600, 600 + 12 * 60, True)]
    # with the live expiry (default) the last one is over an hour after it started
    iv = oblast_intervals(rec, T(700))
    assert iv["end"].iloc[-1] == T(600 + 60 * LIVE_EXPIRY_HOURS) and not iv["open"].any()
    # only what was known at as_of: the end at 30 is unknown at 25, the one at 20 is known
    iv = oblast_intervals(rec, T(25))
    assert iv["end"].tolist() == [T(20)] and not iv["open"].any()


def test_live_records_never_rewrite_the_dataset(alerts):
    """A live alert that started before the dataset end is clipped to the dataset end."""
    early = HIST_END - pd.Timedelta(hours=2)
    iv = pd.DataFrame({"region": ["Kharkivska oblast", "Lvivska oblast"], "start": [early, early],
                       "end": [ORIGIN, HIST_END - pd.Timedelta(hours=1)], "naive": False})
    out = after_dataset(iv, alerts)
    cut = max(HIST_END, alerts[alerts["region"] == "Kharkivska oblast"]["end"].max())
    assert out["region"].tolist() == ["Kharkivska oblast"] and out["start"].iloc[0] == cut
    payload = {"alerts": [record("Харківська область", early)]}
    _, fgrid, _ = grids(alerts, HIST_END, ORIGIN, live(payload))
    plain = build_grid(alerts, data_end=ORIGIN)
    m = fgrid.minute(cut)
    np.testing.assert_array_equal(fgrid.active[:, :m], plain.active[:, :m])


# ------------------------------------------------ training never sees live


def test_training_labels_do_not_depend_on_live_records(alerts):
    payload = {"alerts": [record("Харківська область", ORIGIN - pd.Timedelta(days=5)),
                          record("Львівська область", ORIGIN - pd.Timedelta(hours=3), "oblast", "Львівська область"),
                          record("Дніпропетровська область", ORIGIN - pd.Timedelta(minutes=50))]}
    t0, f0, _ = grids(alerts, HIST_END, ORIGIN, None)
    t1, f1, gap = grids(alerts, HIST_END, ORIGIN, live(payload))
    assert len(gap) and not np.array_equal(f0.active, f1.active)  # the live records do change the features
    np.testing.assert_array_equal(t0.active, t1.active)
    K = np.arange(28 * 24, t0.n_minutes // 60)
    H0, H1, S0, S1 = hourly_fraction(t0), hourly_fraction(t1), hourly_starts(t0), hourly_starts(t1)
    for h in HORIZONS:
        for target in ("active", "onset"):
            np.testing.assert_array_equal(labels(t0, K, h, H0, target, S0), labels(t1, K, h, H1, target, S1))
    assert t1.t0 + pd.Timedelta(minutes=t1.n_minutes) == HIST_END.floor("h")


# --------------------------------------------------------- web app recorder


def _poll(rec, minute, payload):
    """What the engine does on every poll."""
    now = ORIGIN + pd.Timedelta(minutes=minute)
    alerts = parse_active(payload)
    st = state_from_alerts(alerts, REGIONS, now, cleared=rec.cleared(alerts, now))
    rec.observe(st)
    return st


def test_recorder_applies_the_same_rules(tmp_path):
    rec = LiveRecorder(tmp_path / "live.csv")
    old = {"alerts": [record("Харківська область", ORIGIN - pd.Timedelta(days=200), "hromada", "Липецька")]}
    st = _poll(rec, 0, old)
    assert not st.regions["Kharkivska oblast"].active
    assert st.regions["Kharkivska oblast"].locations == ["Липецька"]  # still shown, never modelled
    assert rec.alerts().empty
    a = record("Харківська область", ORIGIN - pd.Timedelta(minutes=30), title="A")
    b = record("Харківська область", ORIGIN + pd.Timedelta(minutes=1), title="B")
    _poll(rec, 2, {"alerts": old["alerts"] + [a, b]})
    f = rec.frame()
    assert f["start"].tolist() == [ORIGIN + pd.Timedelta(minutes=1)] and f["open"].all()  # latest activation
    _poll(rec, 3, {"alerts": old["alerts"] + [a]})  # B cleared -> the oblast alert is over, A or not
    f = rec.frame()
    assert not f["open"].any() and f["end"].tolist() == [ORIGIN + pd.Timedelta(minutes=3)]
    assert f["observed_at"].tolist() == [ORIGIN + pd.Timedelta(minutes=3)]
    # without a new activation the recorded alert ends at the expiry, however long alerts.in.ua keeps it
    rec2 = LiveRecorder(tmp_path / "live2.csv")
    start = ORIGIN - pd.Timedelta(minutes=55)
    c = record("Львівська область", start, "oblast", "Львівська область")
    _poll(rec2, 0, {"alerts": [c]})
    assert rec2.frame()["open"].all()
    _poll(rec2, 10, {"alerts": [c]})  # 65 min old, still reported by alerts.in.ua
    f = rec2.frame()
    assert not f["open"].any() and f["end"].tolist() == [start + pd.Timedelta(hours=LIVE_EXPIRY_HOURS)]
