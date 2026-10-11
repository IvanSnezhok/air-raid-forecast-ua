"""Which threat an Air Force (kpszsu) post announces, and for which oblasts.

Regex rules over lower-cased Ukrainian text, so every label can be explained by
pointing at the post that produced it. Region patterns are shared with the text
features (`text-features/lexicon.py`) so oblast names match the volunteer
dataset.

Threat classes, strongest first (used to resolve posts that name several):
  ballistic  "загроза застосування балістичного озброєння", Іскандер, KN-23,
             Кинджал, Циркон, зліт МіГ-31К, "швидкісна ціль"
  missile    крилаті ракети (КР), КАР, Калібр, Х-101, Онікс, "ракета/ціль курсом
             на", "Бандероль" (jet loitering munition, flies like a missile)
  kab        КАБ, "авіаційних засобів ураження" (guided bombs / tactical aviation)
  drone      БпЛА, шахеди, "ударні дрони", реактивні БпЛА
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "text-features"))
from lexicon import REGION_RE as _BASE_REGION_RE, normalise  # noqa: E402

# Spellings the shared lexicon misses, seen in Air Force posts.
_EXTRA = {
    "Vinnytska oblast": r"\bвінниччин\w*",
    "Kyivska oblast": r"\bвишгород\w*|\bобухів\w*|\bвасильків\w*|\bірпін\w*|\bбуч\w*",
    "Donetska oblast": r"\bдонеччин\w*",
}
REGION_RE = {r: (re.compile(rx.pattern + "|" + _EXTRA[r]) if r in _EXTRA else rx) for r, rx in _BASE_REGION_RE.items()}

CLASSES = ["ballistic", "missile", "kab", "drone"]  # priority order
LEVEL = {"ballistic": "red", "missile": "red", "massed_drone": "red", "kab": "red", "drone": "yellow"}

_P = {
    "ballistic": (
        r"балісти\w*|іскандер\w*|кинджал\w*|\b(kn|кн)-?23\b|\b[сc]-?(300|400)\b"
        r"|\bшвидкісн\w*|\bміг-?31\w*|\bmig-?31\w*|циркон\w*"
    ),
    "missile": (
        r"крилат\w* ракет\w*|\bкр\b|\bкар\b|бандерол\w*|\bціл[ьі]\b|калібр\w*|\b[хx]-?(101|555|59|69|22|31)\w*|онікс\w*"
        r"|\bракет\w*|\bту-?95\w*|\bту-?160\w*|\bту-?22\w*|стратегічн\w* авіац\w*"
    ),
    "kab": r"\bкаб\w*\b|авіабомб\w*|керован\w* авіа\w* бомб\w*|авіаційн\w* засоб\w* ураж\w*|тактичн\w* авіац\w*",
    "drone": r"\bбпла\w*|шахед\w*|shahed|\bдрон\w*|безпілот\w*|геран\w*|мопед\w*",
}
THREAT_RE = {k: re.compile(v) for k, v in _P.items()}

# Posts that are about all of Ukraine rather than a named oblast: a ballistic
# threat "з півдня/сходу", MiG-31K take-off, strategic bombers in the air.
NATIONAL_RE = re.compile(
    r"загроз\w* застосуванн\w* баліст\w*|\bміг-?31\w*|\bmig-?31\w*|\bту-?95\w*|\bту-?160\w*"
    r"|по всій території|на всій території|всією територією|по всій україні"
)
# Morning summaries ("У ніч на 5 жовтня противник атакував ... 98 ударними БпЛА")
# describe the past night, not a live threat: excluded from live labels, used to
# measure the size of the attack instead.
SUMMARY_RE = re.compile(r"у ніч на \d|з \d{1,2}[:.]\d{2} \d{1,2} \w+ (противник|ворог)|станом на \d{1,2}[:.]\d{2}.{0,40}збит|збито/подавлено|збито / подавлено")
ATTACK_SENTENCE_RE = re.compile(r"(?:атакував|застосував)[^\n]*")
DRONE_COUNT_RE = re.compile(r"(\d{1,4})(?:-\w+)?\s+(?:ударн\w*\s+)?(?:бпла|безпілот\w*|дрон\w*|шахед\w*)")
ALL_CLEAR_RE = re.compile(r"\bвідбій\b|загроз\w* минул\w*|загроз\w* відсутн\w*")


def classify(text: str) -> dict:
    """Return {'threats': [...], 'regions': [...], 'national': bool, 'summary': bool, 'drones_reported': int|None}."""
    t = normalise(text)
    summary = bool(SUMMARY_RE.search(t))
    all_clear = bool(ALL_CLEAR_RE.search(t))
    # "Відбій загрози балістики" names a threat but ends it
    threats = [] if all_clear else [k for k in CLASSES if THREAT_RE[k].search(t)]
    regions = [r for r, rx in REGION_RE.items() if rx.search(t)]
    drones = None
    if summary:
        sent = ATTACK_SENTENCE_RE.search(t)
        nums = [int(m.group(1)) for m in DRONE_COUNT_RE.finditer(sent.group(0))] if sent else []
        drones = max(nums) if nums else None
    return {
        "threats": threats,
        "regions": regions,
        "national": bool(NATIONAL_RE.search(t)) and not all_clear,
        "summary": summary,
        "all_clear": all_clear,
        "drones_reported": drones,
    }


def top_threat(threats) -> str | None:
    for k in CLASSES:
        if k in threats:
            return k
    return None
