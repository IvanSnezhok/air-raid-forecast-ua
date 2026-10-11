"""Decisions of scripts/pipeline.py: run gate, slot timing, keep-alive, retrain trigger, status."""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pipeline_glue", ROOT / "scripts" / "pipeline.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

T = datetime(2026, 10, 11, 22, 7, 30, tzinfo=timezone.utc)


def test_gate_runs_manual_push_keepalive_always_and_skips_duplicates():
    just_now = T - timedelta(minutes=2)
    assert P.gate_decision("push", "push", just_now, T)[0]
    assert P.gate_decision("workflow_dispatch", "manual", just_now, T)[0]
    assert P.gate_decision("workflow_dispatch", "keepalive", just_now, T)[0]  # the only trigger for its slot
    for event, source in (("schedule", "schedule"), ("workflow_dispatch", "pinger")):
        assert not P.gate_decision(event, source, just_now, T)[0]  # second trigger for the same slot
        assert P.gate_decision(event, source, T - timedelta(minutes=8), T)[0]
        assert P.gate_decision(event, source, None, T)[0]


def test_next_slot_matches_the_cron():
    # cron "3-59/10": :03, :13, ..., :53
    assert P.next_slot(T) == T.replace(minute=13, second=0)
    assert P.next_slot(T.replace(minute=3, second=0)) == T.replace(minute=13, second=0)
    assert P.next_slot(T.replace(minute=2, second=59)) == T.replace(minute=3, second=0)
    assert P.next_slot(T.replace(minute=58)) == (T + timedelta(hours=1)).replace(minute=3, second=0)
    for m in range(60):
        s = P.next_slot(T.replace(minute=m))
        assert s.minute % 10 == 3 and timedelta(0) < s - T.replace(minute=m) <= timedelta(minutes=10)


def test_keepalive_is_bounded_and_yields_to_other_triggers():
    assert P.keepalive_decision(True, 0, 72, [])[0]
    assert not P.keepalive_decision(False, 0, 72, [])[0]  # PIPELINE_KEEPALIVE=off
    assert not P.keepalive_decision(True, 72, 72, [])[0]  # chain limit
    assert not P.keepalive_decision(True, 3, 72, [{"event": "schedule", "status": "queued"}])[0]


def test_retrain_reason():
    end = datetime(2026, 10, 11, 4, 36, tzinfo=timezone.utc)
    ok = {"data_end": "2026-10-11T04:00:00+00:00", "holdout_start": "2026-10-04T04:00:00+00:00"}
    assert P.retrain_reason(ok, end, 6) is None
    assert "more data" in P.retrain_reason(ok, end + timedelta(hours=7), 6)
    assert "fallback" in P.retrain_reason({**ok, "holdout_start": None}, end, 6)
    assert P.retrain_reason({}, end, 6) == "no models"


def test_models_key_changes_with_modelling_code(tmp_path):
    for f in P.MODEL_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(f)
    k = P.models_key(tmp_path)
    assert k.startswith("models-v2-") and P.models_key(tmp_path) == k
    (tmp_path / "src/airraid/features.py").write_text("changed")
    assert P.models_key(tmp_path) != k
    assert P.models_key() == P.models_key(ROOT)  # the real files exist


def test_status_and_timings(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("JOB_T0", str(T.timestamp() - 100))
    P.record("forecast", 12.3, True)
    P.record("verify", 4.0, False)
    timings = json.loads((tmp_path / "pipeline-timings.json").read_text())
    assert [s["name"] for s in timings["steps"]] == ["setup", "forecast", "verify"]
    st = P.build_status(T, timings, {"origin_utc": "2026-10-11 22:04", "last_poll_utc": "2026-10-11 22:03:40",
                                     "history_ends_utc": "2026-10-11 04:36"},
                        {"data_end": "2026-10-11T04:00:00+00:00", "holdout_start": None},
                        {"n_verified": 10, "pending": 5, "groups": [
                            {"target": "active", "horizon_h": 1, "window": "24h", "n": 10, "brier_model": 0.1}]},
                        {}, {"JOB_T0": str(T.timestamp() - 100), "GITHUB_RUN_ID": "1", "GITHUB_REPOSITORY": "o/r"})
    assert st["origin"] == "2026-10-11T22:04Z" and st["steps_s"]["forecast"] == 12.3
    assert st["failed_steps"] == ["verify"] and st["elapsed_s"] == 100.0
    assert st["verification"]["windows"]["24h"][0]["n"] == 10 and st["run"]["url"].endswith("/o/r/actions/runs/1")
    assert P.parse_ts(st["updated_at"]) == T


def test_timed_propagates_exit_code(tmp_path):
    env = {"RUNNER_TEMP": str(tmp_path), "PATH": "/usr/bin:/bin"}
    ok = subprocess.run([sys.executable, str(ROOT / "scripts/pipeline.py"), "timed", "a", "--", "true"], env=env)
    bad = subprocess.run([sys.executable, str(ROOT / "scripts/pipeline.py"), "timed", "b", "--", "false"], env=env)
    assert ok.returncode == 0 and bad.returncode == 1
    steps = json.loads((tmp_path / "pipeline-timings.json").read_text())["steps"]
    assert [(s["name"], s["ok"]) for s in steps] == [("a", True), ("b", False)]
