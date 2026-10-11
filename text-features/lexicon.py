"""Keyword rules: which oblasts and which threat types a post mentions.

Deliberately simple regexes over lower-cased Ukrainian text, so every feature is
explainable and reproducible without a model. Region names match the `region`
column of the volunteer alert dataset used by the main pipeline.

Known limits (heuristic, documented rather than hidden):
  * "Київ" (city) vs "Київщина/Київська" (oblast) are split, but posts often
    use them loosely;
  * "Дніпро" can mean the river; "Рівне" can be an adjective;
  * a post that mentions a region "безпечно" or in a past-tense summary still
    counts as a mention. The all-clear class partially compensates.
"""
from __future__ import annotations

import re

# Stems/patterns per oblast. Major cities and frequent targets are included
# where they identify the oblast unambiguously.
_REGION_PATTERNS: dict[str, str] = {
    "Kyiv City": r"\bки[їє]в(?!щин|ськ)\w*|\bстолиц\w*",
    "Kyivska oblast": r"\bкиївщин\w*|\bкиївськ\w* обл\w*|\bбіла церкв\w*|\bбровар\w*|\bбориспол\w*|\bфастів\w*",
    "Kharkivska oblast": r"\bхарк[іо]в\w*|\bкуп.янськ\w*|\bчугу[єї]в\w*|\bізюм\w*",
    "Dnipropetrovska oblast": r"\bдніпр\w*|\bкривий ріг|\bкривого рогу|\bкривор\w*|\bнікопол\w*|\bпавлоград\w*|\bкам.янськ\w*",
    "Zaporizka oblast": r"\bзапор[іо]ж\w*",
    "Sumska oblast": r"\bсум(и|ах|ам|ами|щин\w*|ськ\w*)\b|\bшостк\w*|\bконотоп\w*|\bохтирк\w*",
    "Donetska oblast": r"\bдонеч\w*|\bдонецьк\w*|\bкраматорськ\w*|\bслов.янськ\w*|\bпокровськ\w*|\bкостянтинівк\w*",
    "Mykolaivska oblast": r"\bмикола[їє]в\w*|\bвознесенськ\w*",
    "Poltavska oblast": r"\bполтав\w*|\bкременчу\w*|\bмиргород\w*|\bлубн\w*",
    "Khersonska oblast": r"\bхерсон\w*",
    "Kirovohradska oblast": r"\bкіровоград\w*|\bкропивниц\w*|\bолександрі\w*",
    "Chernihivska oblast": r"\bчерніг\w*|\bніжин\w*|\bприлук\w*",
    "Odeska oblast": r"\bодес\w*|\bодещин\w*|\bізмаїл\w*|\bчорноморськ\w*",
    "Cherkaska oblast": r"\bчеркас\w*|\bчеркащин\w*|\bуман\w*|\bсміл\w*",
    "Zhytomyrska oblast": r"\bжитомир\w*|\bкоростен\w*|\bбердич\w*",
    "Volynska oblast": r"\bволин\w*|\bлуцьк\w*|\bковел\w*",
    "Vinnytska oblast": r"\bвінниц\w*|\bвінничин\w*|\bкозятин\w*",
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

# Threat classes. "launch_platform" (strategic bombers, MiG-31K take-offs) is
# the most useful leading signal: it is typically posted before missiles fly.
_THREAT_PATTERNS: dict[str, str] = {
    "drone": r"шахед\w*|shahed|\bбпла\b|\bбпла\w*|\bдрон\w*|мопед\w*|геран\w*|безпілот\w*",
    "cruise": r"крилат\w*|калібр\w*|\b[хx]-?(101|555|59|69|22)\b",
    "ballistic": r"балісти\w*|іскандер\w*|кинджал\w*|\b(kn|кн)-?23\b|\b[сc]-?(300|400)\b|швидкісн\w* ціл\w*",
    "missile": r"ракет\w*",
    "kab": r"\bкаб\w*\b|авіабомб\w*|керован\w* авіаційн\w* бомб\w*",
    "launch_platform": r"\bту-?95\w*|\bту-?160\w*|\bту-?22\w*|стратегічн\w* авіац\w*|\bміг-?31\w*|\bmig-?31\w*|\bборт\w*",
    "launch": r"\bпуск\w*|\bзліт\w*|злет\w*|запуск\w*",
    "direction": r"\bкурс\w*|\bнапрям\w*|\bрухаєт\w*|\bлетить\b|\bлетять\b",
    "all_clear": r"\bвідбій\b|\bвідбою\b|\bчисто\b|загроз\w* минул\w*|загроз\w* відсутн\w*",
}
THREAT_RE = {t: re.compile(p) for t, p in _THREAT_PATTERNS.items()}
THREATS = list(_THREAT_PATTERNS)


def normalise(text: str) -> str:
    # lower-case and unify apostrophes (', ’, ʼ) used in names like Слов'янськ
    return text.lower().replace("’", "'").replace("ʼ", "'").replace("`", "'")


def tag(text: str) -> tuple[list[str], list[str]]:
    """Return (regions mentioned, threat classes mentioned) for one post."""
    t = normalise(text)
    regions = [r for r, rx in REGION_RE.items() if rx.search(t)]
    threats = [k for k, rx in THREAT_RE.items() if rx.search(t)]
    return regions, threats
