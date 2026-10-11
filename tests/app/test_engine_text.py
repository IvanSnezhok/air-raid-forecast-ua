"""Web engine with the text archive: serves the history + Telegram model when the archive
covers the origin, the history-only model when it does not, and says which one served."""
import json

import numpy as np
import pandas as pd
import pytest

from airraid.text import archive
from airraid_app.engine import ForecastEngine

UA = {"Kyiv City": "Київ", "Kharkivska oblast": "Харків", "Lvivska oblast": "Львів", "Odeska oblast": "Одесу"}


def _archive(root, alerts_csv, complete_until):
    a = pd.read_csv(alerts_csv, parse_dates=["started_at"])
    rng = np.random.default_rng(1)
    rows = [{"channel": "kpszsu", "id": i + 1, "edited": False, "text": f"БпЛА курсом на {UA[r]}",
             "date": pd.Timestamp(s).tz_convert("UTC") - pd.Timedelta(minutes=int(rng.integers(20, 40)))}
            for i, (r, s) in enumerate(zip(a["region"], a["started_at"])) if rng.random() < 0.7]
    obs = pd.DataFrame(rows)
    obs["seen_at"] = obs["date"] + pd.Timedelta(minutes=2)
    archive.upsert(root, "telegram", "kpszsu", obs)
    archive.write_state(root, "telegram", "kpszsu", {"complete_from": "2026-07-01T00:00:00Z",
                                                     "complete_until": complete_until})


@pytest.fixture()
def text_settings(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("airraid.text.telegram.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.features.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.live.fetch", lambda *a, **k: {})  # offline
    reports = tmp_path / "reports"
    reports.mkdir()
    sel = {f"lightgbm_text|{t}_h{h}": {"trees": 20} for t in ("active", "onset") for h in (1, 2, 3, 6)}
    (reports / "text_selection.json").write_text(json.dumps(sel))

    def make(complete_until):
        root = tmp_path / f"text-{complete_until[:10]}"
        _archive(root, settings.data_path, complete_until)
        return type(settings)(**{**vars(settings), "text_root": root, "model_dir": tmp_path / "models",
                                  "state_dir": tmp_path / "state", "reports_dir": reports})
    return make


def test_engine_serves_text_model_when_covered(text_settings):
    e = ForecastEngine(text_settings("2030-01-01T00:00:00Z"))
    e.bootstrap()
    assert e.status == "ready", e.message
    f = e.forecast()
    assert f["model"]["served"] == ["lightgbm_text"] and f["model"]["text_status"] == "ok"


def test_engine_falls_back_when_archive_is_stale(text_settings):
    e = ForecastEngine(text_settings("2026-08-01T00:00:00Z"))
    e.bootstrap()
    assert e.status == "ready", e.message
    f = e.forecast()
    assert f["model"]["served"] == ["lightgbm"] and "does not cover" in f["model"]["text_status"]
