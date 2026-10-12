"""The "why this forecast" data in the API and the static site, and /api/method."""
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from airraid.explain import GROUPS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_static_site  # noqa: E402


def _logit(p):
    return math.log(p / (1 - p))


def test_region_why_matches_its_forecast(client):
    regions = client.get("/api/regions").json()
    for reg in regions:
        d = client.get(f"/api/regions/{reg['id']}").json()
        why = d["why"]
        assert why is not None and set(why["items"]) == {"active", "onset"}
        for t, by_h in d["probs"].items():
            assert set(why["items"][t]) == set(by_h)
            for h, p in by_h.items():
                it = why["items"][t][str(h)]
                assert it["model"] == "lightgbm" and it["text"] == "off"  # no text layer in this app
                assert it["p"] == pytest.approx(p, abs=6e-5) and it["p_hist"] == pytest.approx(it["p"])
                assert set(it["groups"]) <= set(GROUPS) and "tg_region" not in it["groups"]
                # drivers are additive in log-odds: base + groups = logit(p) (both rounded to 4 decimals)
                total = it["base"] + sum(it["groups"].values())
                assert total == pytest.approx(_logit(it["p"]), abs=2e-3 + 1e-3 * len(it["groups"]))
        assert why["signals"] == [] and why["has_text"] is False
    # the map's forecast file stays small: no "why" there
    fc = client.get("/api/forecast").json()
    assert all("why" not in r for r in fc["regions"])


def test_raion_carries_its_oblast_why(client):
    known = {x["name"] for x in client.get("/api/regions").json()}
    r = next(x for x in client.get("/api/raions").json()["raions"] if x["oblast"] in known)
    d = client.get(f"/api/raions/{r['id']}").json()
    reg = client.get(f"/api/regions/{d['oblast_id']}").json()
    assert d["oblast_why"] == reg["why"]


def test_method_endpoint(client):
    m = client.get("/api/method").json()
    rows = m["telegram"]
    assert {(r["target"], r["horizon"]) for r in rows} == {(t, h) for t in ("active", "onset") for h in (1, 2, 3, 6)}
    assert all(isinstance(r["used"], bool) for r in rows)
    assert {"kpszsu", "deraketaua"} <= {c["id"] for c in m["channels"] if c["in_model"]}
    assert m["news"]["in_model"] is False and "gdelt" in m["news"]["sources"]
    assert m["groups"] and "other" not in m["groups"]


def test_method_reads_the_reports(tmp_path):
    """The table follows reports/text_selection.json (the serving rule) and the test-week CIs."""
    from airraid_app.method import telegram_table

    sel = {}
    for t in ("active", "onset"):
        for h in (1, 2, 3, 6):
            sel[f"lightgbm_cov|{t}_h{h}"] = {"trees": 5, "val_brier": 0.10}
            sel[f"lightgbm_text|{t}_h{h}"] = {"trees": 5, "val_brier": 0.11 if (t, h) == ("active", 6) else 0.095}
    (tmp_path / "text_selection.json").write_text(json.dumps(sel))
    (tmp_path / "text_ablation_diff.csv").write_text(
        "target,horizon_h,model,brier_diff_vs_cov,ci95_lo,ci95_hi,rel_change_pct\n"
        "onset,2,lightgbm_text,-0.0018,-0.0037,-0.00005,-1.05\n"
        "active,3,lightgbm_text,0.0004,-0.0004,0.0012,0.25\n")
    rows = {(r["target"], r["horizon"]): r for r in telegram_table(tmp_path)}
    assert rows[("active", 6)]["used"] is False and rows[("active", 6)]["val_change"] == pytest.approx(0.1)
    assert rows[("onset", 2)]["used"] and rows[("onset", 2)]["val_change"] == pytest.approx(-0.05)
    assert rows[("onset", 2)]["test_change"] == pytest.approx(-0.0105) and rows[("onset", 2)]["test_verdict"] == "better"
    assert rows[("active", 3)]["test_verdict"] == "unclear" and rows[("onset", 1)]["test_change"] is None


def test_static_site_has_why_and_method(settings, tmp_path):
    out = tmp_path / "site"
    build_static_site.build(out, replace(settings, type_forecast=None))
    m = json.loads((out / "api" / "method.json").read_text(encoding="utf-8"))
    assert len(m["telegram"]) == 8
    for reg in json.loads((out / "api" / "regions.json").read_text(encoding="utf-8")):
        d = json.loads((out / "api" / "regions" / f"{reg['id']}.json").read_text(encoding="utf-8"))
        assert d["why"]["items"]["onset"]["3"]["groups"]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="api/method.json"' in html
