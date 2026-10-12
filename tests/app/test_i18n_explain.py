"""Translations of the "why this forecast" block and the How-it-works page (keys built at run time)."""
import re

from airraid import explain
from airraid.text.telegram import CHANNELS

from .test_i18n import APP, EN, HTML, UK

REASONS = ("not_selected", "no_archive", "no_model", "off", "feature_mismatch")


def test_runtime_keys_exist_in_both_languages():
    keys = {f"grp.{g}" for g in explain.GROUPS}
    keys |= {f"why.tgNot.{r}" for r in REASONS}
    keys |= {f"sigThreat.{th}" for th in explain.THREATS}
    keys |= {f"meth.ch.{c}" for c in CHANNELS}
    keys |= {f"meth.now.{c}" for c in ("ok", "no_archive", "no_model", "off")}
    keys |= {f"meth.{v}" for v in ("better", "unclear", "worse")}
    assert sorted(keys - set(EN)) == [] and sorted(keys - set(UK)) == []
    for fam in ("grp.", "why.tgNot.", "sigThreat.", "meth.ch.", "meth.now.", "meth."):
        assert f"t(`{fam}${{" in APP, fam


def test_reasons_match_the_explainer():
    src = open(explain.__file__, encoding="utf-8").read()
    assert set(re.findall(r'return "(\w+)"', src[src.index("def text_reason"):src.index("def explain(")])) == {"served", *REASONS}


def test_every_signal_code_has_a_phrase():
    src = open(explain.__file__, encoding="utf-8").read()
    codes = ("reg_threat", "reg_clear", "reg_last", "reg_quiet", "platform", "national", "launch", "massed", "explosion",
             "nat_regions", "summary")
    for c in codes:
        assert f'"{c}"' in src and f'case "{c}"' in APP, c


def test_how_it_works_is_short_and_keeps_the_essentials():
    sec = HTML[HTML.index('<section id="view-about"'):HTML.index("</section>", HTML.index('<section id="view-about"'))]
    for lang in ("en", "uk"):
        part = sec[sec.index(f'<div data-lang="{lang}"'):]
        part = part[:part.index('<div data-lang="uk"')] if lang == "en" else part
        visible = re.sub(r"<details.*?</details>", " ", part, flags=re.S)
        words = len(re.sub(r"<[^>]+>", " ", visible).split())
        assert words < 400, (lang, words)  # was ~1200 (EN) before the rewrite
        assert f'id="api-note-{lang}"' in part and "data-method" in part and 'href="#history"' in part
        assert part.count('class="fl ') == 6  # the pipeline diagram
        assert "alerts.in.ua" in part and "Vadimkin/ukrainian-air-raid-sirens-dataset" in part and "CC BY 3.0 IGO" in part
    assert "any type" in sec and "будь-якого типу" in sec
    assert "official" in sec.lower() and "офіційн" in sec.lower()
