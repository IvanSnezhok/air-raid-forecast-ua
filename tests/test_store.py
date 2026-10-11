"""Point-in-time store and alerts.in.ua collector (offline, fake API)."""
import json
import urllib.error

import pandas as pd
import pytest

import airraid.collect as collect
from airraid import store
from airraid.live_alerts import OBLAST_UID, store_records

T0 = pd.Timestamp("2026-10-11 10:00", tz="UTC")


def ts(minutes: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=minutes)


def hist_rows(fetched: pd.Timestamp, finished=None, uid="122") -> pd.DataFrame:
    return pd.DataFrame([{"uid": uid, "oblast": "Kharkivska oblast", "level": "raion", "started_at": ts(0),
                          "finished_at": finished, "alert_type": "air_raid", "fetched_at": fetched,
                          "requested_uid": "22"}]).astype({"finished_at": "datetime64[ns, UTC]"})


def test_append_is_immutable_and_partitioned_by_day(tmp_path):
    a = store.append(tmp_path, "polls", pd.DataFrame({"observed_at": [ts(0), ts(24 * 60)], "n_records": [1, 2]}))
    b = store.append(tmp_path, "polls", pd.DataFrame({"observed_at": [ts(10)], "n_records": [3]}))
    assert [p.parent.name for p in a] == ["2026-10-11", "2026-10-12"] and len(set(a + b)) == 3
    assert store.load(tmp_path, "polls")["n_records"].tolist() == [1, 3, 2]
    with pytest.raises(ValueError):
        store.append(tmp_path, "polls", pd.DataFrame({"n_records": [1]}))  # no point-in-time column
    with pytest.raises(ValueError):
        store.append(tmp_path, "nope", pd.DataFrame({"observed_at": [ts(0)]}))


def test_load_returns_only_what_was_known(tmp_path):
    store.append(tmp_path, "forecasts", pd.DataFrame({"issued_at": [ts(0), ts(60)], "p": [0.1, 0.2]}))
    assert store.load(tmp_path, "forecasts", as_of=ts(30))["p"].tolist() == [0.1]
    assert store.load(tmp_path, "forecasts", as_of=ts(-1)).empty
    assert len(store.load(tmp_path, "forecasts")) == 2


def test_history_versions_first_seen_and_dedup(tmp_path):
    store.append(tmp_path, "alerts_history", hist_rows(ts(30)))  # first seen running
    store._KEYS.clear()  # as in a new process: keys come from the files
    assert store.append(tmp_path, "alerts_history", hist_rows(ts(40))) == []  # same content: nothing written
    store.append(tmp_path, "alerts_history", hist_rows(ts(90), finished=ts(45)))  # its end becomes known later
    early = store.load(tmp_path, "alerts_history", as_of=ts(60))
    assert len(early) == 1 and pd.isna(early["finished_at"].iloc[0])  # the end was not known yet
    late = store.load(tmp_path, "alerts_history", as_of=ts(100))
    assert late["finished_at"].iloc[0] == ts(45)
    assert late["first_seen_at"].iloc[0] == ts(30) and late["known_at"].iloc[0] == ts(90)
    assert len(store.load(tmp_path, "alerts_history", raw=True)) == 2


def test_store_records_derive_ends_from_polls_and_coverage(tmp_path):
    rec = {"uid": "122", "oblast": "Kharkivska oblast", "level": "raion", "started_at": ts(-5), "finished_at": pd.NaT,
           "alert_type": "air_raid"}
    for m, present in ((0, True), (10, True), (20, False), (30, False)):
        store.append(tmp_path, "polls", pd.DataFrame({"observed_at": [ts(m)], "n_records": [int(present)]}))
        if present:
            store.append(tmp_path, "alerts_active", pd.DataFrame([{**rec, "observed_at": ts(m)}]))
    r, covered = store_records(tmp_path, ts(15), since=ts(0))
    assert pd.isna(r["finished_at"].iloc[0]) and covered == ts(10)  # at 15 nothing says it ended
    r, covered = store_records(tmp_path, ts(35), since=ts(0))
    assert r["finished_at"].iloc[0] == ts(20) and covered == ts(30)  # gone from the poll at 20
    _, covered = store_records(tmp_path, ts(35), since=ts(-60))
    assert covered is None  # the polls do not reach back to `since`


# ------------------------------------------------------------- collector

SECRET = "tok-SECRET-123"


def fake_api(calls, history_levels=("raion",), fail_uid=None):
    def fetch(url, token, timeout=20, if_modified_since=None):
        assert token == SECRET
        calls.append(url)
        if url.endswith("/alerts/active.json"):
            return 200, {"alerts": [{"id": 1, "location_title": "Чугуївський район", "location_type": "raion",
                                     "location_uid": "122", "location_oblast": "Харківська область",
                                     "started_at": "2026-10-11T09:00:00Z", "alert_type": "air_raid",
                                     "threats": [{"threat_type": "drones"}]}]}, {}
        uid = url.split("/regions/")[1].split("/")[0]
        if uid == fail_uid:
            raise urllib.error.URLError("down")
        return 200, {"alerts": [{"id": int(uid) * 10 + i, "location_title": "x", "location_type": lv,
                                 "location_uid": uid, "location_oblast_uid": uid if uid in OBLAST_UID else "22",
                                 "started_at": "2026-10-01T10:00:00Z", "finished_at": "2026-10-01T11:00:00Z",
                                 "alert_type": "air_raid"} for i, lv in enumerate(history_levels)]}, {}
    return fetch


def test_collect_poll_and_history_resume(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("ALERTS_IN_UA_TOKEN", SECRET)
    monkeypatch.setattr(collect, "fetch", fake_api(calls, fail_uid="9"))
    collect.main(["poll", "--root", str(tmp_path)])
    a = store.load(tmp_path, "alerts_active")
    assert a["level"].tolist() == ["raion"] and json.loads(a["extra"].iloc[0])["threats"][0]["threat_type"] == "drones"
    assert len(store.load(tmp_path, "polls")) == 1
    collect.main(["history", "--root", str(tmp_path), "--max-requests", "5", "--sleep", "0"])
    assert len(calls) == 1 + 5
    collect.main(["history", "--root", str(tmp_path), "--sleep", "0"])  # resumes, skips what is fresh
    n_obl = len([u for u, r in OBLAST_UID.items() if r != "Luhanska oblast"])
    pulled = [c.split("/regions/")[1].split("/")[0] for c in calls if "/regions/" in c]
    # every oblast once per run; the failing one is tried again by the next run, not in a loop
    assert len(set(pulled)) == n_obl and len(pulled) == n_obl + 1 and pulled.count("9") == 2
    state = json.loads((tmp_path / "_state/history.json").read_text())
    assert state["oblast_history_has_subregions"] is True  # raion records came with the oblast history
    assert "9" not in state["uids"]  # failed: will be retried by the next run
    assert len(store.load(tmp_path, "history_pulls")) == n_obl - 1
    assert SECRET not in capsys.readouterr().out  # the token never reaches the logs


def test_collect_fetches_raions_when_oblast_history_lacks_them(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("ALERTS_IN_UA_TOKEN", SECRET)
    monkeypatch.setattr(collect, "fetch", fake_api(calls, history_levels=("oblast",)))
    collect.main(["history", "--root", str(tmp_path), "--sleep", "0"])
    pulled = {c.split("/regions/")[1].split("/")[0] for c in calls}
    assert pulled - set(OBLAST_UID), "raion UIDs should be pulled too"
    assert "16" not in pulled  # Luhansk: excluded from the model


def test_collect_without_token_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("ALERTS_IN_UA_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        collect.main(["poll", "--root", str(tmp_path)])
