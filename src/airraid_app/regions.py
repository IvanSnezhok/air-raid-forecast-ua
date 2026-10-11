"""Region names: dataset (English) names, Ukrainian labels, URL slugs, alerts.in.ua titles."""
from __future__ import annotations

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
ALERTS_IN_UA = {
    "Черкаська область": "Cherkaska oblast", "Чернігівська область": "Chernihivska oblast",
    "Чернівецька область": "Chernivetska oblast", "Дніпропетровська область": "Dnipropetrovska oblast",
    "Донецька область": "Donetska oblast", "Івано-Франківська область": "Ivano-Frankivska oblast",
    "Харківська область": "Kharkivska oblast", "Херсонська область": "Khersonska oblast",
    "Хмельницька область": "Khmelnytska oblast", "Кіровоградська область": "Kirovohradska oblast",
    "м. Київ": "Kyiv City", "Київ": "Kyiv City", "Київська область": "Kyivska oblast",
    "Луганська область": "Luhanska oblast",
    "Львівська область": "Lvivska oblast", "Миколаївська область": "Mykolaivska oblast",
    "Одеська область": "Odeska oblast", "Полтавська область": "Poltavska oblast",
    "Рівненська область": "Rivnenska oblast", "Сумська область": "Sumska oblast",
    "Тернопільська область": "Ternopilska oblast", "Вінницька область": "Vinnytska oblast",
    "Волинська область": "Volynska oblast", "Закарпатська область": "Zakarpatska oblast",
    "Запорізька область": "Zaporizka oblast", "Житомирська область": "Zhytomyrska oblast",
}


def uk_name(region: str) -> str:
    return UK_NAMES.get(region, region)


def slug(region: str) -> str:
    """'Kyiv City' -> 'kyiv-city', 'Ivano-Frankivska oblast' -> 'ivano-frankivska'."""
    return region.removesuffix(" oblast").lower().replace(" ", "-")
