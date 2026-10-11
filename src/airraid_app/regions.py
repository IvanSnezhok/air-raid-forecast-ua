"""Region names: dataset (English) names, Ukrainian labels, URL slugs, alerts.in.ua titles."""
from __future__ import annotations

from airraid.live_alerts import OBLAST_EN

# Dataset region name -> Ukrainian label shown in the UI.
UK_NAMES = {
    "Cherkaska oblast": "Черкаська", "Chernihivska oblast": "Чернігівська",
    "Chernivetska oblast": "Чернівецька", "Dnipropetrovska oblast": "Дніпропетровська",
    "Donetska oblast": "Донецька", "Ivano-Frankivska oblast": "Івано-Франківська",
    "Kharkivska oblast": "Харківська", "Khersonska oblast": "Херсонська",
    "Khmelnytska oblast": "Хмельницька", "Kirovohradska oblast": "Кіровоградська",
    "Kyiv City": "м. Київ", "Kyivska oblast": "Київська", "Luhanska oblast": "Луганська",
    "Lvivska oblast": "Львівська", "Mykolaivska oblast": "Миколаївська",
    "Odeska oblast": "Одеська", "Poltavska oblast": "Полтавська",
    "Rivnenska oblast": "Рівненська", "Sumska oblast": "Сумська",
    "Ternopilska oblast": "Тернопільська", "Vinnytska oblast": "Вінницька",
    "Volynska oblast": "Волинська", "Zakarpatska oblast": "Закарпатська",
    "Zaporizka oblast": "Запорізька", "Zhytomyrska oblast": "Житомирська",
}

# alerts.in.ua `location_oblast` / `location_title` -> dataset region name.
# Kept under the old name; the single source of truth is airraid.live_alerts.
ALERTS_IN_UA = OBLAST_EN


def uk_name(region: str) -> str:
    return UK_NAMES.get(region, region)


def slug(region: str) -> str:
    """'Kyiv City' -> 'kyiv-city', 'Ivano-Frankivska oblast' -> 'ivano-frankivska'."""
    return region.removesuffix(" oblast").lower().replace(" ", "-")
