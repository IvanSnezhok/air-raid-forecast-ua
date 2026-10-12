"""The built static site in a real browser: English by default, no Ukrainian left in English mode,
the EN / UK switch works and is remembered, ?lang= overrides it, no horizontal scroll on a phone,
no console errors.

Needs Node.js with the `playwright` package (NODE_PATH from AIRRAID_UI_NODE_PATH or `npm root -g`)
and a Chromium it can launch (AIRRAID_UI_CHANNEL=chrome uses the installed Google Chrome). Skipped
when they are missing, unless AIRRAID_UI_REQUIRED=1 (CI sets it).
"""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_static_site  # noqa: E402

SCRIPT = Path(__file__).with_name("ui_i18n_check.js")


def _node_env() -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    path = os.environ.get("AIRRAID_UI_NODE_PATH")
    if not path:
        npm = shutil.which("npm")
        if not npm:
            return None
        path = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "NODE_PATH": path}
    ok = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True).returncode == 0
    return env if ok else None


@pytest.fixture(scope="module")
def node_env():
    env = _node_env()
    if env is None:
        if os.environ.get("AIRRAID_UI_REQUIRED") == "1":
            pytest.fail("node + playwright are required (AIRRAID_UI_REQUIRED=1) but not available")
        pytest.skip("node + playwright not available")
    return env


@pytest.fixture(scope="module")
def site_url(settings, tmp_path_factory):
    out = tmp_path_factory.mktemp("browser") / "site"
    build_static_site.build(out, settings)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ui_in_a_browser(node_env, site_url):
    r = subprocess.run(["node", str(SCRIPT), site_url], env=node_env, capture_output=True, text=True, timeout=240)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"no result: {r.stdout[-2000:]} {r.stderr[-2000:]}"
    res = json.loads(lines[-1])
    if os.environ.get("AIRRAID_UI_REQUIRED") != "1" and "fatal" in res and "Executable doesn't exist" in res["fatal"]:
        pytest.skip("no browser for playwright")
    assert "fatal" not in res, res.get("fatal")
    assert res["lang0"] == "en"  # the browser asks for Ukrainian; English is the default anyway
    assert {k: v for k, v in res["cyrillic"].items() if v} == {}  # no Ukrainian left in English mode
    assert res["raionRows"] > 0  # the oblast panel lists its raions
    assert res["ukLang"] == "uk" and res["ukText"]
    assert res["remembered"] == "uk" and res["ukMapText"]  # the choice survives a reload
    assert res["override"] == "en"  # ?lang=en wins over the remembered choice
    assert res["overflow"] <= 0  # no horizontal scroll at 390 px
    assert res["errors"] == []
