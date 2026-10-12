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
    # the History tab's files (lazy-loaded by the UI)
    hist = json.loads((out / "api" / "history.json").read_text(encoding="utf-8"))
    assert {"windows", "blocks", "reliability", "calibration"} <= set(hist)
    assert "series" in json.loads((out / "api" / "history" / "timeline.json").read_text(encoding="utf-8"))
    assert (out / "static" / "app.js").exists() and (out / "static" / "ukraine-map.json").exists()
    assert (out / "static" / "ukraine-map-detail.json").exists() and (out / "static" / "i18n.js").exists()
    # the "API" paragraph of both language versions points to the JSON files
    for lang in ("en", "uk"):
        start = html.index(f'<p id="api-note-{lang}">')
        assert 'href="api/raions.json"' in html[start:html.index("</p>", start)]
    assert 'href="/docs"' not in html
