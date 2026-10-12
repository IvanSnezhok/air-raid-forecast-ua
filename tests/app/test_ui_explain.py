"""The "why this forecast" block and the How-it-works page in a real browser, in English and Ukrainian.

Same requirements as test_ui_browser.py (Node.js with `playwright`, a Chromium); skipped when missing
unless AIRRAID_UI_REQUIRED=1.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from .test_ui_browser import node_env, site_url  # noqa: F401  (fixtures)

SCRIPT = Path(__file__).with_name("ui_explain_check.js")


def test_why_block_and_how_it_works(node_env, site_url):  # noqa: F811
    r = subprocess.run(["node", str(SCRIPT), site_url], env=node_env, capture_output=True, text=True, timeout=240)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"no result: {r.stdout[-2000:]} {r.stderr[-2000:]}"
    res = json.loads(lines[-1])
    if os.environ.get("AIRRAID_UI_REQUIRED") != "1" and "fatal" in res and "Executable doesn't exist" in res["fatal"]:
        pytest.skip("no browser for playwright")
    assert "fatal" not in res, res.get("fatal")
    titles = {"en": ("Why this forecast", "Telegram & news", "Alert in hour 1", "New alert within 6 h"),
              "uk": ("Чому такий прогноз", "Telegram і новини", "Тривога в годину 1", "Нова тривога протягом 6 год")}
    for lang, (why, tg, a1, o6) in titles.items():
        ob = res[lang]["oblast"]
        assert why in ob["before"]["heading"] and ob["before"]["bars"] > 0, (lang, ob)
        assert ob["before"]["tg"].startswith(tg), (lang, ob)  # the Telegram line is always there (here: off)
        assert ("any type" if lang == "en" else "будь-якого типу") in ob["before"]["firstH3"].lower()
        assert ob["picked"]["question"].startswith(a1) and ob["picked"]["picked"] == ["active1"], (lang, ob)
        assert ob["cell"]["question"].startswith(o6) and ob["cell"]["picked"] == ["onset6"], (lang, ob)
        rn = res[lang]["raion"]
        # a raion panel: the raion's method and its oblast's drivers ("Why: Kharkiv Oblast forecast")
        assert rn and rn["heading"].startswith(why.split()[0] + ":") and rn["bars"] > 0 and rn["method"], (lang, rn)
        ab = res[lang]["about"]
        assert ab["steps"] == 6 and ab["rows"] == 8 and ab["channels"] >= 5, (lang, ab)
        assert ab["details"] >= 5 and ab["openDetails"] == 0 and ab["history"] and ab["api"], (lang, ab)
        assert ab["words"] < 520, (lang, ab)  # visible text, the table included (the old page: ~1200 words)
    assert res["overflow"] == {"about": 0, "oblast": 0}  # no horizontal scroll at 360 px
    assert res["errors"] == []
