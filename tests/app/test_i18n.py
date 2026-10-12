"""UI translations (static/i18n.js): every key the UI uses exists in English and Ukrainian, with the same placeholders.

English is the default language; the browser check of the rendered page is in test_ui_browser.py.
"""
import json
import re
from pathlib import Path

import pytest

from airraid_app.raion_engine import METHOD_UK
from airraid_app.threats import TYPES

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src/airraid_app/static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CYR = re.compile("[Ѐ-ӿ]")


def _dicts() -> dict:
    src = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = src[src.index("window.I18N =") + len("window.I18N ="):].strip().rstrip(";")
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("//"))  # section comments
    return json.loads(body)


I18N = _dicts()
EN, UK = I18N["en"], I18N["uk"]


def _forms(v) -> list[str]:
    return list(v.values()) if isinstance(v, dict) else [v]


def _placeholders(v) -> set[str]:
    return {p for f in _forms(v) for p in re.findall(r"\{(\w+)\}", f)}


def used_keys() -> set[str]:
    keys = set(re.findall(r'\bt\("([\w.]+)"', APP))
    keys |= set(re.findall(r'data-i18n(?:-html)?="([\w.]+)"', HTML))
    for attr in re.findall(r'data-i18n-attr="([^"]+)"', HTML):
        keys |= {pair.split(":")[1].strip() for pair in attr.split(";")}
    # keys built at run time: t(`family.${...}`)
    keys |= {f"lvl.{i}" for i in range(4)} | {"lvlc.yellow", "lvlc.red"}
    keys |= {f"{fam}.{t}" for fam in ("type", "typeShort", "typePhrase") for t in TYPES}
    keys |= {f"{fam}.{m}" for fam in ("method", "methodLong") for m in METHOD_UK}
    keys |= {f"live.{lv}" for lv in ("oblast", "raion", "hromada")}
    keys |= {"target.active", "target.onset"}
    keys |= {f"hi.state.{r}" for r in ("ok", "no_gain", "collecting")} | {"hi.src.dataset", "hi.src.store"}
    keys |= {f"load.{c}" for c in _engine_codes()} | {f"load.{s}" for s in ("starting", "loading", "training", "ready", "error")}
    return keys


def _engine_codes() -> set[str]:
    src = (ROOT / "src/airraid_app/engine.py").read_text(encoding="utf-8")
    return set(re.findall(r'self\._set\([^)]*\{"code": "(\w+)"', src)) | {"starting"}


def test_dynamic_families_are_used_as_expected():
    for fam in ("lvl.", "lvlc.", "type.", "typeShort.", "typePhrase.", "method.", "methodLong.", "live.", "load.", "target.",
                "hi.state.", "hi.src."):
        assert f"t(`{fam}${{" in APP, fam


def test_every_used_key_exists_in_both_languages():
    keys = used_keys()
    assert len(keys) > 250
    assert sorted(keys - set(EN)) == []
    assert sorted(keys - set(UK)) == []
    assert set(EN) == set(UK)


@pytest.mark.parametrize("key", sorted(EN))
def test_translations_match(key):
    en, uk = EN[key], UK[key]
    assert _placeholders(en) == _placeholders(uk), key
    if isinstance(en, dict):
        assert {"one", "other"} <= set(en), key
    if isinstance(uk, dict):
        assert {"one", "few", "many", "other"} <= set(uk), key
    assert not any(CYR.search(f) for f in _forms(en)), f"Cyrillic in the English text of {key}"


def test_engine_warning_codes_are_translated():
    src = (ROOT / "src/airraid_app/engine.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'"code": "(\w+)"', src)) - _engine_codes() - {"error", "live_error"}
    assert {"no_live", "history_gap", "live_unavailable", "live_stale"} <= codes
    for c in codes:
        assert f'case "{c}"' in APP, c


def test_no_hardcoded_ukrainian_in_app_js():
    """Ukrainian text lives in i18n.js; app.js keeps only place names and transliteration tables."""
    allowed = re.compile(r'^\s*"[\w -]+": \["|^const TR(_INITIAL)? = |^\s*[ль]: |lc === "г"|м\. Києва|області|ої|сільська|область|район|^\s*if \(LANG === "uk"|const normUk|АР Крим|м\\\.')
    bad = [ln for ln in APP.splitlines() if CYR.search(ln) and not allowed.search(ln)]
    assert bad == []


def test_english_is_the_default():
    assert '<html lang="en">' in HTML
    assert 'return LANGS.includes(s) ? s : "en";' in APP
    # the visible static text of index.html is English before the script runs (no flash of Ukrainian)
    for m in re.finditer(r'<(\w+)[^>]*data-i18n="([\w.]+)"[^>]*>([^<]*)<', HTML):
        assert not CYR.search(m.group(3)), m.group(2)


# --- what the percentages mean (the owner's two questions: "a percentage of what?" and "which alert?")
ANY_TYPE = {"en": "any type", "uk": "будь-якого типу"}


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_forecast_labels_say_any_type_of_alert(lang):
    d = I18N[lang]
    for key in ("detail.forecast", "raion.forecast", "detail.note", "raion.note", "rank.note", "rank.noteRaions",
                "legend.probActive", "legend.probOnset", "head.next", "rank.grpActive", "rank.grpRaion",
                "rlist.cap", "stats.histFc", "ctl.activeTitle", "ctl.onsetTitle", "ex.what"):
        assert ANY_TYPE[lang] in d[key], (lang, key)


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_percentages_say_what_they_are(lang):
    d = I18N[lang]
    seven = {"en": "7 in 10", "uk": "7 з 10"}[lang]
    for key in ("detail.note", "raion.note", "rank.note", "rank.noteRaions", "legend.pctHint", "ex.pct"):
        assert "70%" in d[key] and seven in d[key], (lang, key)
    # threat type is conditional on an alert starting and is not the chance of an alert
    not_chance = {"en": "not the chance of an alert", "uk": "не ймовірність тривоги"}[lang]
    for key in ("types.sub", "ex.types", "legend.typeNote", "ctl.typeTitle"):
        assert not_chance in d[key].lower().replace("не є ймовірністю тривоги", "не ймовірність тривоги"), (lang, key)
    # history charts are a share of time, the forecast is a chance
    assert d["stats.histBar"] != d["stats.histFc"]
    # model-quality percentages are scores
    assert "Brier" in d["m.pctNote"]


def test_risk_thresholds_match_the_code():
    """The risk words are generated from LEVEL_CUTS in the "?" explanation; the How-it-works page states them too."""
    cuts = [float(x) for x in re.search(r"const LEVEL_CUTS = \[([^\]]+)\]", APP).group(1).split(",")][:3]
    pcts = [round(c * 100) for c in cuts]
    assert pcts == [15, 40, 70]
    assert f"below {pcts[0]}%, moderate {pcts[0]}–{pcts[1] - 1}%, high {pcts[1]}–{pcts[2] - 1}%" in HTML
    assert f"{pcts[2]}% or more" in HTML
    assert f"менше {pcts[0]}%, помірна {pcts[0]}–{pcts[1] - 1}%, висока {pcts[1]}–{pcts[2] - 1}%" in HTML
