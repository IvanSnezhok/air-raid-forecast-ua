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
    assert P.retrain_reason(ok, end, 6, ["raion", "text"]) == "the bundle has no raion and no text models"


def test_train_blockers_ignore_runs_older_than_a_fallback_bundle():
    # 12 Oct: a UI change touched a hashed file at 07:01, the pipeline's fallback trained an
    # oblast-only bundle at 07:08; the last train run (05:35) was for the previous models key
    no_layers = "the bundle has no raion and no text models"
    now = datetime(2026, 10, 12, 7, 20, tzinfo=timezone.utc)
    fallback = datetime(2026, 10, 12, 7, 8, tzinfo=timezone.utc)
    run = lambda created, status="completed": {"created_at": created, "status": status}  # noqa: E731
    before = run("2026-10-12T05:35:38Z")
    assert P.train_blockers([before], no_layers, 60, fallback, now) == []  # dispatch now
    after = run("2026-10-12T07:12:00Z")  # a train run on this bundle's code still gave no layers
    assert P.train_blockers([after], no_layers, 60, fallback, now) == [after]
    assert P.train_blockers([run("2026-10-12T02:10:00Z")], no_layers, 60, None, now)  # 6 h cooldown kept
    assert P.train_blockers([run("2026-10-12T01:10:00Z")], no_layers, 60, None, now) == []
    running = run("2026-10-12T05:35:38Z", "in_progress")
    assert P.train_blockers([running], no_layers, 60, fallback, now) == [running]
    # other reasons keep the plain cooldown, whatever the bundle's age
    assert P.train_blockers([run("2026-10-12T06:40:00Z")], "no models", 60, fallback, now)
    assert P.train_blockers([run("2026-10-12T06:10:00Z")], "no models", 60, fallback, now) == []


def test_raion_summary_counts_the_methods_served():
    raions = {"models_source": "saved", "tier": "live_state", "n_active": 3, "raions": [
        {"method": {"active": {"1": "lightgbm_state", "3": "oblast_x_share"}, "onset": {"1": "oblast_x_share"}}},
        {"method": {"active": {"1": "oblast"}, "onset": {"1": "oblast_x_share"}}}]}
    s = P.raion_summary(raions)
    assert s["models_source"] == "saved" and s["tier"] == "live_state" and s["n_raions"] == 2
    assert s["methods"] == {"oblast_x_share": 3, "lightgbm_state": 1, "oblast": 1}
    assert P.raion_summary({}) is None


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


def test_text_archive_is_fetched_only_after_the_origin_minute():
    poll = datetime(2026, 10, 12, 1, 45, 22, tzinfo=timezone.utc)
    # origin 01:46:00: from 01:45:23 wait until 01:46:02
    assert P.origin_wait(poll, poll + timedelta(seconds=1)) == 39.0
    assert P.origin_wait(poll, datetime(2026, 10, 12, 1, 46, 30, tzinfo=timezone.utc)) == 0.0
    on_minute = poll.replace(second=0)
    assert P.origin_wait(on_minute, on_minute) == 2.0  # a poll exactly on the minute is its own origin
