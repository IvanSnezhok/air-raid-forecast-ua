"""Which threat an Air Force (kpszsu) post announces, and for which oblasts.

The rules live in one place for the whole project, `src/airraid/text/lexicon.py`
(also used by the main model's Telegram features and the news filter); this
module re-exports them and adds the yellow/red level of each threat type.

Threat classes, strongest first (used to resolve posts that name several):
  ballistic  "загроза застосування балістичного озброєння", Іскандер, KN-23,
             Кинджал, Циркон, зліт МіГ-31К, "швидкісна ціль"
  missile    крилаті ракети (КР), КАР, Калібр, Х-101, Онікс, "ракета/ціль курсом
             на", "Бандероль" (jet loitering munition, flies like a missile)
  kab        КАБ, "авіаційних засобів ураження" (guided bombs / tactical aviation)
  drone      БпЛА, шахеди, "ударні дрони", реактивні БпЛА
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from airraid.text.lexicon import (  # noqa: E402,F401
    ALL_CLEAR_RE, ATTACK_SENTENCE_RE, CLASSES, DRONE_COUNT_RE, NATIONAL_RE, REGION_RE, SUMMARY_RE, THREAT_RE,
    classify, normalise, top_threat,
)

LEVEL = {"ballistic": "red", "missile": "red", "massed_drone": "red", "kab": "red", "drone": "yellow"}
