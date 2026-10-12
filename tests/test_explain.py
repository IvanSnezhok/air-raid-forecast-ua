"""Why this forecast (airraid.explain): the grouped drivers add up to the served model's
log-odds, the Telegram effect is the difference of the two models' own predictions, the
reason is given when the Telegram model does not serve, and nothing after the origin is used."""
import json

import numpy as np
import pandas as pd
import pytest

from airraid import explain as why
from airraid import nowcast
from airraid.data import build_grid
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels
from airraid.models import GBM, feature_columns
from airraid.text import archive, live
from airraid.text.serving import TextServing
from airraid.training import TARGETS

UA = {"Kyiv City": "Київ", "Kharkivska oblast": "Харків", "Lvivska oblast": "Львів", "Odeska oblast": "Одесу"}


def synthetic_alerts(days=70, seed=3):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-07-01", tz="UTC")
    rate = {"Kyiv City": 0.08, "Kharkivska oblast": 0.15, "Lvivska oblast": 0.03, "Odeska oblast": 0.07}
    rows = []
    for hour in range(days * 24):
        t = start + pd.Timedelta(hours=hour)
        for region, p in rate.items():
            if rng.random() < p * (2 if t.hour in (22, 23, 0, 1, 2, 3) else 1):
                s = t + pd.Timedelta(minutes=int(rng.integers(0, 60)))
                rows.append((region, s, s + pd.Timedelta(minutes=int(rng.integers(10, 150))), False))
    return pd.DataFrame(rows, columns=["region", "start", "end", "naive"])


def _posts(alerts, rng, first_id=1):
    rows = [{"channel": "kpszsu", "id": first_id + i, "edited": False,
             "text": f"БпЛА курсом на {UA[r]}" if i % 3 else f"Відбій загрози, {UA[r]}",
             "date": s - pd.Timedelta(minutes=int(rng.integers(20, 40)))}
            for i, (r, s) in enumerate(zip(alerts["region"], alerts["start"])) if rng.random() < 0.7]
    rows.append({"channel": "kpszsu", "id": first_id + len(alerts) + 5, "edited": False,
                 "text": "Зліт МіГ-31К, загроза застосування балістичного озброєння",
                 "date": alerts["start"].max() - pd.Timedelta(hours=3)})
    obs = pd.DataFrame(rows)
    obs["seen_at"] = obs["date"] + pd.Timedelta(minutes=2)
    return obs


def _archive(root, obs):
    archive.upsert(root, "telegram", "kpszsu", obs)
    archive.write_state(root, "telegram", "kpszsu", {"complete_from": "2026-07-01T00:00:00Z",
                                                     "complete_until": "2030-01-01T00:00:00Z"})


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr("airraid.text.telegram.FEATURE_CHANNELS", ["kpszsu"])
    mp.setattr("airraid.text.features.FEATURE_CHANNELS", ["kpszsu"])
    d = tmp_path_factory.mktemp("explain")
    alerts = synthetic_alerts()
    sel = {f"lightgbm_text|{t}_h{h}": {"trees": 15, "val_brier": 0.1} for t in TARGETS for h in HORIZONS}
    sel["lightgbm_cov|active_h6"] = {"trees": 15, "val_brier": 0.05}  # history-only model better here
    (d / "text_selection.json").write_text(json.dumps(sel))
    obs = _posts(alerts, np.random.default_rng(0))
    _archive(d / "text", obs)
    hist_end = alerts["start"].max().floor("h") - pd.Timedelta(hours=12)
    tgrid = build_grid(alerts[alerts["start"] < hist_end], data_end=hist_end)
    H, S = hourly_fraction(tgrid), hourly_starts(tgrid)
    K = np.arange(tgrid.n_minutes // 60 - 24 * 20, tgrid.n_minutes // 60 - 6)
    base = build_features(tgrid, K, H)
    models = {}
    for h in HORIZONS:
        df = add_horizon_features(base, tgrid, H, h, S)
        for t in TARGETS:
            y = labels(tgrid, K, h, H, t, S)
            models[(t, h)] = GBM(n_estimators=15, params={"num_threads": 2}).fit(df, y)
    text = TextServing(d / "text", d / "models", reports_dir=d, fetch=False, allow_train=True)
    assert text.ensure_models(tgrid, H, S, hist_end)
    origin = hist_end + pd.Timedelta(hours=4, minutes=17)  # a sub-hour origin after the training data
    yield {"d": d, "alerts": alerts, "models": models, "text": text, "origin": origin, "obs": obs}
    mp.undo()


def _run(s, alerts=None, root=None, text=True):
    grid, _ = nowcast.feature_grid(s["alerts"] if alerts is None else alerts, s["origin"])
    rows = None
    if text:
        rows, status = live.text_rows_at(grid.t0, np.array([grid.n_minutes // 60]), grid.regions,
                                         root or s["d"] / "text", fetch_new=False, channels=["kpszsu"])
        assert status == "ok"
    parts = []
    log = nowcast.predict(s["models"], grid, s["text"] if text else None, rows, explain=parts)
    return grid, rows, log, pd.concat(parts, ignore_index=True)


def test_groups_add_up_to_the_served_log_odds(setup):
    grid, rows, log, E = _run(setup)
    assert len(E) == len(log) == len(grid.regions) * len(TARGETS) * len(HORIZONS)
    groups = E[[f"g_{g}" for g in why.GROUPS]].fillna(0).sum(axis=1)
    logit = np.log(E["p"] / (1 - E["p"]))
    np.testing.assert_allclose(E["base"] + groups, logit, atol=1e-6)
    # every feature of both models has a named group ("other" stays empty)
    for g in [*setup["models"].values(), *setup["text"].models.values()]:
        assert {why.group_of(c) for c in g.model.feature_name()} <= set(why.GROUPS) - {"other"}
    assert (E["g_other"].isna() | (E["g_other"] == 0)).all()
    # Telegram groups only when the Telegram model served; history groups always
    txt = E["model"] == "lightgbm_text"
    assert txt.any() and (~txt).any()
    assert E.loc[txt, "g_tg_region"].notna().all() and E.loc[~txt, "g_tg_region"].isna().all()
    assert E["g_alert_now"].notna().all() and E["g_calendar"].notna().all()


def test_a_region_s_own_state_counts_as_its_own(setup):
    """now__<own region> is the region's own alert, not "other oblasts"."""
    cols = ["now__Kharkivska", "now__Kyiv_City", "active_now"]
    c = np.array([[1.0, 2.0, 4.0, 0.5], [1.0, 2.0, 4.0, 0.5]])
    g = why.grouped(c, cols, ["Kharkivska oblast", "Lvivska oblast"])
    assert g.loc[0, "g_alert_now"] == 5.0 and g.loc[0, "g_other_oblasts"] == 2.0
    assert g.loc[1, "g_alert_now"] == 4.0 and g.loc[1, "g_other_oblasts"] == 3.0
    assert (g["base"] == 0.5).all()


def test_telegram_effect_is_the_two_models_predictions(setup):
    grid, rows, log, E = _run(setup)
    text, models = setup["text"], setup["models"]
    H, S = hourly_fraction(grid), hourly_starts(grid)
    base = build_features(grid, np.array([grid.n_minutes // 60]), H)
    from airraid.text import features as tf
    for (t, h), part in E.groupby(["target", "horizon"]):
        x = add_horizon_features(base, grid, H, h, S)
        p_hist = models[(t, h)].predict(x)
        np.testing.assert_allclose(part["p_hist"], p_hist, atol=1e-12)
        served = log[(log["target"] == t) & (log["horizon"] == h)]
        np.testing.assert_allclose(part["p"].round(4), served["p"], atol=1e-12)
        if (t, h) == ("active", 6):  # lost on validation: history-only serves, and we say why
            assert set(part["model"]) == {"lightgbm"} and set(part["text"]) == {"not_selected"}
            np.testing.assert_allclose(part["p"], part["p_hist"])
        else:
            assert set(part["model"]) == {"lightgbm_text"} and set(part["text"]) == {"served"}
            g = text.models[(t, h)]
            xt = tf.attach(x, rows)
            names = [c for c in g.model.feature_name() if c != "region"]
            np.testing.assert_allclose(part["p"], g.predict(xt[["origin_hour", "region", *names]]), atol=1e-12)
    # no Telegram rows (archive behind the origin): the history model serves, reason "no_archive"
    grid, _ = nowcast.feature_grid(setup["alerts"], setup["origin"])
    parts = []
    nowcast.predict(models, grid, text, None, explain=parts)
    E2 = pd.concat(parts)
    assert set(E2["model"]) == {"lightgbm"}
    assert set(E2["text"]) == {"no_archive", "not_selected"}
    assert E2[list(why.SIGNAL_COLS)].isna().all().all()
    # no text layer at all
    _, _, _, E3 = _run(setup, text=False)
    assert set(E3["text"]) == {"off"}


def test_nothing_after_the_origin_is_used(setup, tmp_path):
    origin = setup["origin"]
    _, _, _, E = _run(setup)
    # the future changes: alerts after the origin are reshuffled, a running alert ends differently,
    # and new Telegram posts appear after the origin
    a = setup["alerts"].copy()
    after = a["start"] >= origin
    a.loc[after, "start"] = a.loc[after, "start"] + pd.Timedelta(minutes=37)
    a.loc[after, "region"] = a.loc[after, "region"].sample(frac=1, random_state=1).to_numpy()
    running = (a["start"] < origin) & (a["end"] > origin - pd.Timedelta(hours=2))
    a.loc[running, "end"] = origin + pd.Timedelta(hours=5)
    a = pd.concat([a, pd.DataFrame({"region": ["Lvivska oblast"], "start": [origin + pd.Timedelta(minutes=1)],
                                    "end": [origin + pd.Timedelta(hours=1)], "naive": [False]})], ignore_index=True)
    root = tmp_path / "text"
    future = pd.DataFrame({"channel": "kpszsu", "id": [10_000_001, 10_000_002], "edited": False,
                           "text": ["Шахеди курсом на Львів", "Зліт МіГ-31К, загроза балістики"],
                           "date": [origin, origin + pd.Timedelta(minutes=5)]})
    future["seen_at"] = future["date"] + pd.Timedelta(seconds=30)
    _archive(root, pd.concat([setup["obs"], future], ignore_index=True))
    _, _, _, E2 = _run(setup, alerts=a, root=root)
    pd.testing.assert_frame_equal(E, E2)
    # ... while a post one minute before the origin does change the signals
    early = future.assign(date=future["date"] - pd.Timedelta(minutes=10), seen_at=future["date"] - pd.Timedelta(minutes=9))
    root2 = tmp_path / "text2"
    _archive(root2, pd.concat([setup["obs"], early], ignore_index=True))
    _, _, _, E3 = _run(setup, root=root2)
    lviv = E3["region"] == "Lvivska oblast"
    assert (E3.loc[lviv, "tg_reg_drone_60m"] > E.loc[lviv, "tg_reg_drone_60m"]).all()


def test_store_frame_and_payload(setup):
    _, _, log, E = _run(setup)
    F = why.frame([E], setup["origin"])
    assert (F["origin"] == setup["origin"]).all() and len(F) == len(E)
    one = F[F["region"] == "Kharkivska oblast"]
    p = why.payload(one)
    assert set(p["items"]) == set(TARGETS) and set(p["items"]["onset"]) == {str(h) for h in HORIZONS}
    it = p["items"]["onset"]["3"]
    assert it["model"] == "lightgbm_text" and it["text"] == "served" and "tg_region" in it["groups"]
    assert "news" not in it["groups"] and "other" not in it["groups"]  # not in the served model
    assert p["has_text"] and isinstance(p["signals"], list)
    json.dumps(p)  # JSON-safe


def test_signals_from_feature_values():
    row = {c: 0.0 for c in why.SIGNAL_COLS} | {
        "tg_reg_drone_60m": 3, "tg_reg_drone_180m": 5, "tg_reg_any_60m": 3, "tg_reg_any_180m": 5,
        "tg_reg_min_since_threat": 12, "tg_min_since_platform": 40, "tg_min_since_national": 1440,
        "tg_nat_launch_60m": 2, "tg_summary_age_h": 72}
    s = why.signals(row)
    assert s[0] == {"code": "reg_threat", "threat": "drone", "n": 3, "w": 60}
    assert {"code": "platform", "min": 40} in s and {"code": "launch", "n": 2, "w": 60} in s
    assert len(s) <= why.MAX_SIGNALS and not any(x["code"] == "national" for x in s)
    quiet = {c: 0.0 for c in why.SIGNAL_COLS} | {"tg_reg_min_since_threat": 1440, "tg_min_since_platform": 1440,
                                                  "tg_min_since_national": 1440, "tg_summary_age_h": 72}
    assert why.signals(quiet) == [{"code": "reg_quiet", "w": 180}]
    assert why.signals({c: np.nan for c in why.SIGNAL_COLS}) == []  # no Telegram rows: nothing claimed


def test_feature_columns_are_unchanged_by_explaining(setup):
    """Explaining reuses the rows the model saw; it must not add or reorder model inputs."""
    grid, rows, log, E = _run(setup)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    x = add_horizon_features(build_features(grid, np.array([grid.n_minutes // 60]), H), grid, H, 1, S)
    m = setup["models"][("active", 1)]
    assert list(m._X(x).columns) == m.model.feature_name()
    assert sorted(m.model.feature_name()) == sorted(feature_columns(x) + ["region"])
