"""Static (GitHub Pages) build: JSON files replace /api/*, threat layer hidden without data."""
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_static_site  # noqa: E402


def test_build_static_site(settings, tmp_path):
    out = tmp_path / "site"
    build_static_site.build(out, replace(settings, type_forecast=None))
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'name="airraid-static"' in html and "/static/" not in html
    forecast = json.loads((out / "api" / "forecast.json").read_text(encoding="utf-8"))
    assert forecast["threats"] is None  # UI hides the threat layer
    for reg in json.loads((out / "api" / "regions.json").read_text(encoding="utf-8")):
        assert (out / "api" / "regions" / f"{reg['id']}.json").exists()
    assert not (out / "api" / "threats.json").exists()
    assert (out / "static" / "app.js").exists() and (out / "static" / "ukraine-map.json").exists()
