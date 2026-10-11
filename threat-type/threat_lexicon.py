"""Which threat an Air Force (kpszsu) post announces, and for which oblasts.

Regex rules over lower-cased Ukrainian text, so every label can be explained by
pointing at the post that produced it. Region patterns are shared with the text
features (`text-features/lexicon.py`) so oblast names match the volunteer
dataset.

Threat classes, strongest first (used to resolve posts that name several):
  ballistic  "загроза застосування балістичного озброєння", Іскандер, KN-23,
             Кинджал, зліт МіГ-31К, "швидкісна ціль"
  missile    крилаті ракети, Калібр, Х-101, "ракета курсом на", Онікс/Циркон
  kab        КАБ, "авіаційних засобів ураження" (guided bombs / tactical aviation)
  drone      БпЛА, шахеди, "ударні дрони", реактивні БпЛА
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "text-features"))
from lexicon import REGION_RE, normalise  # noqa: E402

CLASSES = ["ballistic", "missile", "kab", "drone"]  # priority order
LEVEL = {"ballistic": "red", "missile": "red", "massed_drone": "red", "kab": "red", "drone": "yellow"}

_P = {
    "ballistic": (
        r"балісти\w*|іскандер\w*|кинджал\w*|\b(kn|кн)-?23\b|\b[сc]-?(300|400)\b"
        r"|швидкісн\w* ціл\w*|\bміг-?31\w*|\bmig-?31\w*|циркон\w*"
    ),
    "missile": (
        r"крилат\w* ракет\w*|калібр\w*|\b[хx]-?(101|555|59|69|22|31)\w*|онікс\w*"
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
DRONE_COUNT_RE = re.compile(r"(\d{2,4})\s*(?:ударн\w*\s*)?(?:бпла|безпілот\w*|дрон\w*|шахед\w*)")
ALL_CLEAR_RE = re.compile(r"\bвідбій\b|загроз\w* минул\w*|загроз\w* відсутн\w*")


def classify(text: str) -> dict:
    """Return {'threats': [...], 'regions': [...], 'national': bool, 'summary': bool, 'drones_reported': int|None}."""
    t = normalise(text)
    summary = bool(SUMMARY_RE.search(t))
    threats = [k for k in CLASSES if THREAT_RE[k].search(t)]
    regions = [r for r, rx in REGION_RE.items() if rx.search(t)]
    drones = None
    if summary:
        nums = [int(m.group(1)) for m in DRONE_COUNT_RE.finditer(t)]
        drones = max(nums) if nums else None
    return {
        "threats": threats,
        "regions": regions,
        "national": bool(NATIONAL_RE.search(t)),
        "summary": summary,
        "all_clear": bool(ALL_CLEAR_RE.search(t)),
        "drones_reported": drones,
    }


def top_threat(threats) -> str | None:
    for k in CLASSES:
        if k in threats:
            return k
    return None
