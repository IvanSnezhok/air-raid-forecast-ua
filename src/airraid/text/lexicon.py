"""One lexicon for every text signal: which oblasts and which threats a post names.

Regular expressions over lower-cased Ukrainian text, so every feature and every
threat-type label can be explained by pointing at the post that produced it.
Region names match the `region` column of the volunteer alert dataset.

Used by the main model's text features (features.py), the news filter (news.py)
and the threat-type labels (threat-type/threat_lexicon.py re-exports from here).

Threat classes, strongest first (used to resolve posts that name several):
  ballistic  "загроза застосування балістичного озброєння", Іскандер, KN-23,
             Кинджал, Циркон, зліт МіГ-31К, "швидкісна ціль"
  missile    крилаті ракети (КР), КАР, Калібр, Х-101, Онікс, "ракета/ціль курсом
             на", "Бандероль", strategic bombers
  kab        КАБ, "авіаційних засобів ураження" (guided bombs / tactical aviation)
  drone      БпЛА, шахеди, "ударні дрони", реактивні БпЛА

Known limits (heuristic, documented rather than hidden):
  * "Київ" (city) vs "Київщина" (oblast) are split, but posts often use them loosely;
  * "Дніпро" can mean the river; a past-tense mention still counts as a mention;
  * "Відбій загрози балістики" names a threat but ends it: such posts carry no threat.
"""
from __future__ import annotations

import re

# Stems per oblast; major cities and frequent targets where they identify the oblast.
_REGION_PATTERNS: dict[str, str] = {
    "Kyiv City": r"\bки[їє]в(?!щин|ськ)\w*|\bстолиц\w*",
    "Kyivska oblast": r"\bкиївщин\w*|\bкиївськ\w* обл\w*|\bбіла церкв\w*|\bбровар\w*|\bбориспол\w*|\bфастів\w*"
                      r"|\bвишгород\w*|\bобухів\w*|\bвасильків\w*|\bірпін\w*|\bбуч\w*",
    "Kharkivska oblast": r"\bхарк[іо]в\w*|\bкуп.янськ\w*|\bчугу[єї]в\w*|\bізюм\w*",
    "Dnipropetrovska oblast": r"\bдніпр\w*|\bкривий ріг|\bкривого рогу|\bкривор\w*|\bнікопол\w*|\bпавлоград\w*|\bкам.янськ\w*",
    "Zaporizka oblast": r"\bзапор[іо]ж\w*",
    "Sumska oblast": r"\bсум(и|ах|ам|ами|щин\w*|ськ\w*)\b|\bшостк\w*|\bконотоп\w*|\bохтирк\w*",
    "Donetska oblast": r"\bдонеч\w*|\bдонецьк\w*|\bкраматорськ\w*|\bслов.янськ\w*|\bпокровськ\w*|\bкостянтинівк\w*"
                       r"|\bдонеччин\w*",
    "Mykolaivska oblast": r"\bмикола[їє]в\w*|\bвознесенськ\w*",
    "Poltavska oblast": r"\bполтав\w*|\bкременчу\w*|\bмиргород\w*|\bлубн\w*",
    "Khersonska oblast": r"\bхерсон\w*",
    "Kirovohradska oblast": r"\bкіровоград\w*|\bкропивниц\w*|\bолександрі\w*",
    "Chernihivska oblast": r"\bчерніг\w*|\bніжин\w*|\bприлук\w*",
    "Odeska oblast": r"\bодес\w*|\bодещин\w*|\bізмаїл\w*|\bчорноморськ\w*",
    "Cherkaska oblast": r"\bчеркас\w*|\bчеркащин\w*|\bуман\w*|\bсміл\w*",
    "Zhytomyrska oblast": r"\bжитомир\w*|\bкоростен\w*|\bбердич\w*",
    "Volynska oblast": r"\bволин\w*|\bлуцьк\w*|\bковел\w*",
    "Vinnytska oblast": r"\bвінниц\w*|\bвінничин\w*|\bкозятин\w*|\bвінниччин\w*",
    "Lvivska oblast": r"\bльв[іо]в\w*",
    "Khmelnytska oblast": r"\bхмельниц\w*|\bхмельниччин\w*|\bстарокостянтин\w*|\bшепетівк\w*",
    "Rivnenska oblast": r"\bрівн(е|ого|ому|енськ\w*|енщин\w*)\b|\bрівненщин\w*",
    "Ternopilska oblast": r"\bтерноп\w*",
    "Chernivetska oblast": r"\bчернів(ц|еч)\w*|\bбуковин\w*",
    "Ivano-Frankivska oblast": r"\bфранків\w*|\bприкарпат\w*|\bбурштин\w*",
    "Zakarpatska oblast": r"\bзакарпат\w*|\bужгород\w*|\bмукачев\w*",
}
REGION_RE = {r: re.compile(p) for r, p in _REGION_PATTERNS.items()}
REGIONS = list(_REGION_PATTERNS)

CLASSES = ["ballistic", "missile", "kab", "drone"]  # priority order
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
    "drone": r"\bбпла\w*|шахед\w*|shahed|\bдрон\w*|безпілот\w*|геран\w*|мопед\w*|\bреактив(и|ів|ом|ами)?\b",
}
THREAT_RE = {k: re.compile(v) for k, v in _P.items()}

# Posts about all of Ukraine rather than a named oblast: a ballistic threat
# "з півдня/сходу", MiG-31K take-off, strategic bombers in the air.
NATIONAL_RE = re.compile(
    r"загроз\w* застосуванн\w* баліст\w*|\bміг-?31\w*|\bmig-?31\w*|\bту-?95\w*|\bту-?160\w*"
    r"|по всій території|на всій території|всією територією|по всій україні"
)
# Launch platforms in the air: strategic bombers, MiG-31K (Kinzhal carrier), ships.
PLATFORM_RE = re.compile(
    r"\bту-?95\w*|\bту-?160\w*|\bту-?22\w*|стратегічн\w* авіац\w*|\bміг-?31\w*|\bmig-?31\w*"
    r"|носі[ії]\w* (калібр|ракет)\w*|\bвиліт\w*|\bзліт\w*|\bзлет\w*"
)
# Launches seen (e.g. "пуски шахедів з Брянська"): drones arrive hours later.
LAUNCH_RE = re.compile(r"\bпуск\w*|\bзапуск\w*|\bстарт\w*|\bвилеті\w*|з (району|аеродром\w*)")
MASSED_RE = re.compile(r"масован\w*|масштабн\w* атак\w*|комбінован\w* атак\w*|велик\w* кільк\w*|хвил\w* (шахед|бпла|дрон)\w*")
EXPLOSION_RE = re.compile(r"\bвибух\w*|\bуламк\w*|працює ппо|робота ппо|\bппо\b|протиповітрян\w*")
# Morning summaries ("У ніч на 5 жовтня противник атакував ... 98 ударними БпЛА")
# describe the past night, not a live threat.
SUMMARY_RE = re.compile(r"у ніч на \d|з \d{1,2}[:.]\d{2} \d{1,2} \w+ (противник|ворог)|станом на \d{1,2}[:.]\d{2}.{0,40}збит|збито/подавлено|збито / подавлено")
ATTACK_SENTENCE_RE = re.compile(r"(?:атакував|застосував)[^\n]*")
DRONE_COUNT_RE = re.compile(r"(\d{1,4})(?:-\w+)?\s+(?:ударн\w*\s+)?(?:бпла|безпілот\w*|дрон\w*|шахед\w*)")
MISSILE_COUNT_RE = re.compile(r"(\d{1,3})(?:-\w+)?\s+(?:\w+\s+){0,2}(?:ракет\w*)")
ALL_CLEAR_RE = re.compile(r"\bвідбій\b|загроз\w* минул\w*|загроз\w* відсутн\w*")
ATTACK_NEWS_RE = re.compile(r"повітрян\w* тривог\w*|\bатак\w*|обстріл\w*|\bудар\w*|\bвлучан\w*")


def normalise(text: str) -> str:
    # lower-case and unify apostrophes (', ’, ʼ) used in names like Слов'янськ
    return text.lower().replace("’", "'").replace("ʼ", "'").replace("`", "'")


def regions_in(t: str) -> list[str]:
    return [r for r, rx in REGION_RE.items() if rx.search(t)]


def classify(text: str) -> dict:
    """Everything the features and labels need from one post (text is normalised here)."""
    t = normalise(text)
    summary = bool(SUMMARY_RE.search(t))
    all_clear = bool(ALL_CLEAR_RE.search(t))
    threats = [] if all_clear else [k for k in CLASSES if THREAT_RE[k].search(t)]
    drones = missiles = None
    if summary:
        sent = ATTACK_SENTENCE_RE.search(t)
        s = sent.group(0) if sent else ""
        nums = [int(m.group(1)) for m in DRONE_COUNT_RE.finditer(s)]
        drones = max(nums) if nums else None
        nums = [int(m.group(1)) for m in MISSILE_COUNT_RE.finditer(s)]
        missiles = sum(nums) if nums else None
    return {
        "threats": threats,
        "regions": regions_in(t),
        "national": bool(NATIONAL_RE.search(t)) and not all_clear,
        "summary": summary,
        "all_clear": all_clear,
        "drones_reported": drones,
        "missiles_reported": missiles,
        "platform": bool(PLATFORM_RE.search(t)) and not all_clear and not summary,
        "launch": bool(LAUNCH_RE.search(t)) and not all_clear and not summary,
        "massed": bool(MASSED_RE.search(t)) and not summary,
        "explosion": bool(EXPLOSION_RE.search(t)) and not summary,
    }


def top_threat(threats) -> str | None:
    for k in CLASSES:
        if k in threats:
            return k
    return None


def is_attack_news(text: str) -> bool:
    """Is a news headline about an air attack (as opposed to politics, sport, ...)?"""
    t = normalise(text)
    return any(rx.search(t) for rx in THREAT_RE.values()) or bool(EXPLOSION_RE.search(t) or ATTACK_NEWS_RE.search(t))
