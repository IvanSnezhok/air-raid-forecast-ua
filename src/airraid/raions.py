"""Raions (districts) of Ukraine after the 2020 reform: ids, names and alert-source mappings.

One row per map unit: 136 raions plus Kyiv City and Sevastopol (cities with special
status). Geometry: src/airraid_app/static/raions-map.json, built by
scripts/build_map.py from the OCHA/HDX COD-AB boundaries (CC BY-IGO, State
Scientific Production Enterprise "Kartographia", valid on 2025-09-01). The
Chornobyl exclusion zone is drawn as part of Vyshhorodskyi raion, which it
belongs to administratively since 2020.

Columns: id, HDX/KATOTTG pcode, oblast (name as in the alert datasets), English
name (as in the "official" alert file), Ukrainian name (as in alerts.in.ua),
alerts.in.ua location UID. The UIDs are the published alerts.in.ua location
ids (also listed in the MIT-licensed alerts_in_ua Python client). Crimea is a
single location in alerts.in.ua, so its raions have no UID.

`tests/test_raions.py` checks that every raion of the official file and every
alerts.in.ua raion UID resolves to a row, and that every row has geometry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Raion:
    id: str
    pcode: str
    oblast: str
    name: str  # English, e.g. "Kharkivskyi raion"
    name_uk: str  # Ukrainian, e.g. "Харківський район"
    uid: int | None  # alerts.in.ua location UID


_ROWS = [
    ('bakhchysaraiskyi', 'UA0102', 'Avtonomna Respublika Krym', 'Bakhchysaraiskyi raion', 'Бахчисарайський район', None),
    ('bilohirskyi', 'UA0104', 'Avtonomna Respublika Krym', 'Bilohirskyi raion', 'Білогірський район', None),
    ('dzhankoiskyi', 'UA0106', 'Avtonomna Respublika Krym', 'Dzhankoiskyi raion', 'Джанкойський район', None),
    ('yevpatoriiskyi', 'UA0108', 'Avtonomna Respublika Krym', 'Yevpatoriiskyi raion', 'Євпаторійський район', None),
    ('kerchenskyi', 'UA0110', 'Avtonomna Respublika Krym', 'Kerchenskyi raion', 'Керченський район', None),
    ('kurmanskyi', 'UA0112', 'Avtonomna Respublika Krym', 'Kurmanskyi raion', 'Курманський район', None),
    ('perekopskyi', 'UA0114', 'Avtonomna Respublika Krym', 'Perekopskyi raion', 'Перекопський район', None),
    ('simferopolskyi', 'UA0116', 'Avtonomna Respublika Krym', 'Simferopolskyi raion', 'Сімферопольський район', None),
    ('feodosiiskyi', 'UA0118', 'Avtonomna Respublika Krym', 'Feodosiiskyi raion', 'Феодосійський район', None),
    ('yaltynskyi', 'UA0120', 'Avtonomna Respublika Krym', 'Yaltynskyi raion', 'Ялтинський район', None),
    ('vinnytskyi', 'UA0502', 'Vinnytska oblast', 'Vinnytskyi raion', 'Вінницький район', 36),
    ('haisynskyi', 'UA0504', 'Vinnytska oblast', 'Haisynskyi raion', 'Гайсинський район', 37),
    ('zhmerynskyi', 'UA0506', 'Vinnytska oblast', 'Zhmerynskyi raion', 'Жмеринський район', 35),
    ('mohyliv-podilskyi', 'UA0508', 'Vinnytska oblast', 'Mohyliv-Podilskyi raion', 'Могилів-Подільський район', 33),
    ('tulchynskyi', 'UA0510', 'Vinnytska oblast', 'Tulchynskyi raion', 'Тульчинський район', 32),
    ('khmilnytskyi', 'UA0512', 'Vinnytska oblast', 'Khmilnytskyi raion', 'Хмільницький район', 34),
    ('volodymyrskyi', 'UA0702', 'Volynska oblast', 'Volodymyrskyi raion', 'Володимирський район', 38),
    ('kamin-kashyrskyi', 'UA0704', 'Volynska oblast', 'Kamin-Kashyrskyi raion', 'Камінь-Каширський район', 41),
    ('kovelskyi', 'UA0706', 'Volynska oblast', 'Kovelskyi raion', 'Ковельський район', 40),
    ('lutskyi', 'UA0708', 'Volynska oblast', 'Lutskyi raion', 'Луцький район', 39),
    ('dniprovskyi', 'UA1202', 'Dnipropetrovska oblast', 'Dniprovskyi raion', 'Дніпровський район', 44),
    ('kamianskyi', 'UA1204', 'Dnipropetrovska oblast', 'Kamianskyi raion', "Кам'янський район", 42),
    ('kryvorizkyi', 'UA1206', 'Dnipropetrovska oblast', 'Kryvorizkyi raion', 'Криворізький район', 46),
    ('nikopolskyi', 'UA1208', 'Dnipropetrovska oblast', 'Nikopolskyi raion', 'Нікопольський район', 47),
    ('samarivskyi', 'UA1210', 'Dnipropetrovska oblast', 'Samarivskyi raion', 'Самарівський район', 43),
    ('pavlohradskyi', 'UA1212', 'Dnipropetrovska oblast', 'Pavlohradskyi raion', 'Павлоградський район', 45),
    ('synelnykivskyi', 'UA1214', 'Dnipropetrovska oblast', 'Synelnykivskyi raion', 'Синельниківський район', 48),
    ('bakhmutskyi', 'UA1402', 'Donetska oblast', 'Bakhmutskyi raion', 'Бахмутський район', 54),
    ('volnovaskyi', 'UA1404', 'Donetska oblast', 'Volnovaskyi raion', 'Волноваський район', 55),
    ('horlivskyi', 'UA1406', 'Donetska oblast', 'Horlivskyi raion', 'Горлівський район', 51),
    ('donetskyi', 'UA1408', 'Donetska oblast', 'Donetskyi raion', 'Донецький район', 53),
    ('kalmiuskyi', 'UA1410', 'Donetska oblast', 'Kalmiuskyi raion', 'Кальміуський район', 49),
    ('kramatorskyi', 'UA1412', 'Donetska oblast', 'Kramatorskyi raion', 'Краматорський район', 50),
    ('mariupolskyi', 'UA1414', 'Donetska oblast', 'Mariupolskyi raion', 'Маріупольський район', 52),
    ('pokrovskyi', 'UA1416', 'Donetska oblast', 'Pokrovskyi raion', 'Покровський район', 56),
    ('berdychivskyi', 'UA1802', 'Zhytomyrska oblast', 'Berdychivskyi raion', 'Бердичівський район', 57),
    ('zhytomyrskyi', 'UA1804', 'Zhytomyrska oblast', 'Zhytomyrskyi raion', 'Житомирський район', 59),
    ('korostenskyi', 'UA1806', 'Zhytomyrska oblast', 'Korostenskyi raion', 'Коростенський район', 58),
    ('zviahelskyi', 'UA1808', 'Zhytomyrska oblast', 'Zviahelskyi raion', 'Звягельський район', 60),
    ('berehivskyi', 'UA2102', 'Zakarpatska oblast', 'Berehivskyi raion', 'Берегівський район', 61),
    ('mukachivskyi', 'UA2104', 'Zakarpatska oblast', 'Mukachivskyi raion', 'Мукачівський район', 65),
    ('rakhivskyi', 'UA2106', 'Zakarpatska oblast', 'Rakhivskyi raion', 'Рахівський район', 63),
    ('tiachivskyi', 'UA2108', 'Zakarpatska oblast', 'Tiachivskyi raion', 'Тячівський район', 64),
    ('uzhhorodskyi', 'UA2110', 'Zakarpatska oblast', 'Uzhhorodskyi raion', 'Ужгородський район', 66),
    ('khustskyi', 'UA2112', 'Zakarpatska oblast', 'Khustskyi raion', 'Хустський район', 62),
    ('berdianskyi', 'UA2302', 'Zaporizka oblast', 'Berdianskyi raion', 'Бердянський район', 147),
    ('vasylivskyi', 'UA2304', 'Zaporizka oblast', 'Vasylivskyi raion', 'Василівський район', 146),
    ('zaporizkyi', 'UA2306', 'Zaporizka oblast', 'Zaporizkyi raion', 'Запорізький район', 149),
    ('melitopolskyi', 'UA2308', 'Zaporizka oblast', 'Melitopolskyi raion', 'Мелітопольський район', 148),
    ('polohivskyi', 'UA2310', 'Zaporizka oblast', 'Polohivskyi raion', 'Пологівський район', 145),
    ('verkhovynskyi', 'UA2602', 'Ivano-Frankivska oblast', 'Verkhovynskyi raion', 'Верховинський район', 67),
    ('ivano-frankivskyi', 'UA2604', 'Ivano-Frankivska oblast', 'Ivano-Frankivskyi raion', 'Івано-Франківський район', 68),
    ('kaluskyi', 'UA2606', 'Ivano-Frankivska oblast', 'Kaluskyi raion', 'Калуський район', 71),
    ('kolomyiskyi', 'UA2608', 'Ivano-Frankivska oblast', 'Kolomyiskyi raion', 'Коломийський район', 70),
    ('kosivskyi', 'UA2610', 'Ivano-Frankivska oblast', 'Kosivskyi raion', 'Косівський район', 69),
    ('nadvirnianskyi', 'UA2612', 'Ivano-Frankivska oblast', 'Nadvirnianskyi raion', 'Надвірнянський район', 72),
    ('bilotserkivskyi', 'UA3202', 'Kyivska oblast', 'Bilotserkivskyi raion', 'Білоцерківський район', 73),
    ('boryspilskyi', 'UA3204', 'Kyivska oblast', 'Boryspilskyi raion', 'Бориспільський район', 78),
    ('brovarskyi', 'UA3206', 'Kyivska oblast', 'Brovarskyi raion', 'Броварський район', 79),
    ('buchanskyi', 'UA3208', 'Kyivska oblast', 'Buchanskyi raion', 'Бучанський район', 75),
    ('vyshhorodskyi', 'UA3210', 'Kyivska oblast', 'Vyshhorodskyi raion', 'Вишгородський район', 74),
    ('obukhivskyi', 'UA3212', 'Kyivska oblast', 'Obukhivskyi raion', 'Обухівський район', 76),
    ('fastivskyi', 'UA3214', 'Kyivska oblast', 'Fastivskyi raion', 'Фастівський район', 77),
    ('holovanivskyi', 'UA3502', 'Kirovohradska oblast', 'Holovanivskyi raion', 'Голованівський район', 82),
    ('kropyvnytskyi', 'UA3504', 'Kirovohradska oblast', 'Kropyvnytskyi raion', 'Кропивницький район', 81),
    ('novoukrainskyi', 'UA3506', 'Kirovohradska oblast', 'Novoukrainskyi raion', 'Новоукраїнський район', 83),
    ('oleksandriiskyi', 'UA3508', 'Kirovohradska oblast', 'Oleksandriiskyi raion', 'Олександрійський район', 80),
    ('alchevskyi', 'UA4402', 'Luhanska oblast', 'Alchevskyi raion', 'Алчевський район', 1803),
    ('dovzhanskyi', 'UA4404', 'Luhanska oblast', 'Dovzhanskyi raion', 'Довжанський район', 1804),
    ('luhanskyi', 'UA4406', 'Luhanska oblast', 'Luhanskyi raion', 'Луганський район', 1801),
    ('rovenkivskyi', 'UA4408', 'Luhanska oblast', 'Rovenkivskyi raion', 'Ровеньківський район', 1802),
    ('svativskyi', 'UA4410', 'Luhanska oblast', 'Svativskyi raion', 'Сватівський район', 85),
    ('siverskodonetskyi', 'UA4412', 'Luhanska oblast', 'Siverskodonetskyi raion', 'Сіверськодонецький район', 84),
    ('starobilskyi', 'UA4414', 'Luhanska oblast', 'Starobilskyi raion', 'Старобільський район', 86),
    ('shchastynskyi', 'UA4416', 'Luhanska oblast', 'Shchastynskyi raion', 'Щастинський район', 87),
    ('drohobytskyi', 'UA4602', 'Lvivska oblast', 'Drohobytskyi raion', 'Дрогобицький район', 91),
    ('zolochivskyi', 'UA4604', 'Lvivska oblast', 'Zolochivskyi raion', 'Золочівський район', 94),
    ('lvivskyi', 'UA4606', 'Lvivska oblast', 'Lvivskyi raion', 'Львівський район', 90),
    ('sambirskyi', 'UA4608', 'Lvivska oblast', 'Sambirskyi raion', 'Самбірський район', 88),
    ('stryiskyi', 'UA4610', 'Lvivska oblast', 'Stryiskyi raion', 'Стрийський район', 89),
    ('sheptytskyi', 'UA4612', 'Lvivska oblast', 'Sheptytskyi raion', 'Шептицький район', 92),
    ('yavorivskyi', 'UA4614', 'Lvivska oblast', 'Yavorivskyi raion', 'Яворівський район', 93),
    ('bashtanskyi', 'UA4802', 'Mykolaivska oblast', 'Bashtanskyi raion', 'Баштанський район', 96),
    ('voznesenskyi', 'UA4804', 'Mykolaivska oblast', 'Voznesenskyi raion', 'Вознесенський район', 95),
    ('mykolaivskyi', 'UA4806', 'Mykolaivska oblast', 'Mykolaivskyi raion', 'Миколаївський район', 98),
    ('pervomaiskyi', 'UA4808', 'Mykolaivska oblast', 'Pervomaiskyi raion', 'Первомайський район', 97),
    ('berezivskyi', 'UA5102', 'Odeska oblast', 'Berezivskyi raion', 'Березівський район', 100),
    ('bilhorod-dnistrovskyi', 'UA5104', 'Odeska oblast', 'Bilhorod-Dnistrovskyi raion', 'Білгород-Дністровський район', 102),
    ('bolhradskyi', 'UA5106', 'Odeska oblast', 'Bolhradskyi raion', 'Болградський район', 105),
    ('izmailskyi', 'UA5108', 'Odeska oblast', 'Izmailskyi raion', 'Ізмаїльський район', 101),
    ('odeskyi', 'UA5110', 'Odeska oblast', 'Odeskyi raion', 'Одеський район', 104),
    ('podilskyi', 'UA5112', 'Odeska oblast', 'Podilskyi raion', 'Подільський район', 99),
    ('rozdilnianskyi', 'UA5114', 'Odeska oblast', 'Rozdilnianskyi raion', 'Роздільнянський район', 103),
    ('kremenchutskyi', 'UA5302', 'Poltavska oblast', 'Kremenchutskyi raion', 'Кременчуцький район', 107),
    ('lubenskyi', 'UA5304', 'Poltavska oblast', 'Lubenskyi raion', 'Лубенський район', 106),
    ('myrhorodskyi', 'UA5306', 'Poltavska oblast', 'Myrhorodskyi raion', 'Миргородський район', 108),
    ('poltavskyi', 'UA5308', 'Poltavska oblast', 'Poltavskyi raion', 'Полтавський район', 109),
    ('varaskyi', 'UA5602', 'Rivnenska oblast', 'Varaskyi raion', 'Вараський район', 110),
    ('dubenskyi', 'UA5604', 'Rivnenska oblast', 'Dubenskyi raion', 'Дубенський район', 111),
    ('rivnenskyi', 'UA5606', 'Rivnenska oblast', 'Rivnenskyi raion', 'Рівненський район', 112),
    ('sarnenskyi', 'UA5608', 'Rivnenska oblast', 'Sarnenskyi raion', 'Сарненський район', 113),
    ('konotopskyi', 'UA5902', 'Sumska oblast', 'Konotopskyi raion', 'Конотопський район', 117),
    ('okhtyrskyi', 'UA5904', 'Sumska oblast', 'Okhtyrskyi raion', 'Охтирський район', 118),
    ('romenskyi', 'UA5906', 'Sumska oblast', 'Romenskyi raion', 'Роменський район', 116),
    ('sumskyi', 'UA5908', 'Sumska oblast', 'Sumskyi raion', 'Сумський район', 114),
    ('shostkynskyi', 'UA5910', 'Sumska oblast', 'Shostkynskyi raion', 'Шосткинський район', 115),
    ('kremenetskyi', 'UA6102', 'Ternopilska oblast', 'Kremenetskyi raion', 'Кременецький район', 120),
    ('ternopilskyi', 'UA6104', 'Ternopilska oblast', 'Ternopilskyi raion', 'Тернопільський район', 119),
    ('chortkivskyi', 'UA6106', 'Ternopilska oblast', 'Chortkivskyi raion', 'Чортківський район', 121),
    ('bohodukhivskyi', 'UA6302', 'Kharkivska oblast', 'Bohodukhivskyi raion', 'Богодухівський район', 126),
    ('iziumskyi', 'UA6304', 'Kharkivska oblast', 'Iziumskyi raion', 'Ізюмський район', 125),
    ('berestynskyi', 'UA6306', 'Kharkivska oblast', 'Berestynskyi raion', 'Берестинський район', 127),
    ('kupianskyi', 'UA6308', 'Kharkivska oblast', 'Kupianskyi raion', "Куп'янський район", 123),
    ('lozivskyi', 'UA6310', 'Kharkivska oblast', 'Lozivskyi raion', 'Лозівський район', 128),
    ('kharkivskyi', 'UA6312', 'Kharkivska oblast', 'Kharkivskyi raion', 'Харківський район', 124),
    ('chuhuivskyi', 'UA6314', 'Kharkivska oblast', 'Chuhuivskyi raion', 'Чугуївський район', 122),
    ('beryslavskyi', 'UA6502', 'Khersonska oblast', 'Beryslavskyi raion', 'Бериславський район', 129),
    ('henicheskyi', 'UA6504', 'Khersonska oblast', 'Henicheskyi raion', 'Генічеський район', 133),
    ('kakhovskyi', 'UA6506', 'Khersonska oblast', 'Kakhovskyi raion', 'Каховський район', 131),
    ('skadovskyi', 'UA6508', 'Khersonska oblast', 'Skadovskyi raion', 'Скадовський район', 130),
    ('khersonskyi', 'UA6510', 'Khersonska oblast', 'Khersonskyi raion', 'Херсонський район', 132),
    ('kamianets-podilskyi', 'UA6802', 'Khmelnytska oblast', 'Kamianets-Podilskyi raion', "Кам'янець-Подільський район", 135),
    ('khmelnytskyi', 'UA6804', 'Khmelnytska oblast', 'Khmelnytskyi raion', 'Хмельницький район', 134),
    ('shepetivskyi', 'UA6806', 'Khmelnytska oblast', 'Shepetivskyi raion', 'Шепетівський район', 136),
    ('zvenyhorodskyi', 'UA7102', 'Cherkaska oblast', 'Zvenyhorodskyi raion', 'Звенигородський район', 150),
    ('zolotoniskyi', 'UA7104', 'Cherkaska oblast', 'Zolotoniskyi raion', 'Золотоніський район', 153),
    ('umanskyi', 'UA7106', 'Cherkaska oblast', 'Umanskyi raion', 'Уманський район', 151),
    ('cherkaskyi', 'UA7108', 'Cherkaska oblast', 'Cherkaskyi raion', 'Черкаський район', 152),
    ('vyzhnytskyi', 'UA7302', 'Chernivetska oblast', 'Vyzhnytskyi raion', 'Вижницький район', 138),
    ('dnistrovskyi', 'UA7304', 'Chernivetska oblast', 'Dnistrovskyi raion', 'Дністровський район', 139),
    ('chernivetskyi', 'UA7306', 'Chernivetska oblast', 'Chernivetskyi raion', 'Чернівецький район', 137),
    ('koriukivskyi', 'UA7402', 'Chernihivska oblast', 'Koriukivskyi raion', 'Корюківський район', 144),
    ('nizhynskyi', 'UA7404', 'Chernihivska oblast', 'Nizhynskyi raion', 'Ніжинський район', 142),
    ('novhorod-siverskyi', 'UA7406', 'Chernihivska oblast', 'Novhorod-Siverskyi raion', 'Новгород-Сіверський район', 141),
    ('prylutskyi', 'UA7408', 'Chernihivska oblast', 'Prylutskyi raion', 'Прилуцький район', 143),
    ('chernihivskyi', 'UA7410', 'Chernihivska oblast', 'Chernihivskyi raion', 'Чернігівський район', 140),
    ('kyiv-city', 'UA8000', 'Kyiv City', 'Kyiv City', 'м. Київ', 31),
    ('sevastopol', 'UA8500', 'Sevastopol City', 'Sevastopol City', 'м. Севастополь', 30),
]

RAIONS: list[Raion] = [Raion(*r) for r in _ROWS]
BY_ID: dict[str, Raion] = {r.id: r for r in RAIONS}
BY_UID: dict[int, Raion] = {r.uid: r for r in RAIONS if r.uid is not None}

# Units for which no alert data exists: occupied Crimea and Sevastopol are not in
# any alert source; Luhansk oblast has had a permanent alert since April 2022 and
# is excluded from the oblast model as well (see airraid.data.EXCLUDED_REGIONS).
NO_DATA_OBLASTS = {"Avtonomna Respublika Krym", "Sevastopol City", "Luhanska oblast"}

# Raions renamed after 2020; the official file uses both names over time.
OFFICIAL_ALIASES = {
    "Krasnohradskyi raion": "berestynskyi",  # renamed 2024
    "Chervonohradskyi raion": "sheptytskyi",  # renamed 2024
    "Volodymyr-Volynskyi raion": "volodymyrskyi",  # renamed 2021
    "Novomoskovskyi raion": "samarivskyi",  # renamed 2024
    "Novohrad-Volynskyi raion": "zviahelskyi",  # renamed 2022
    "Sievierodonetskyi raion": "siverskodonetskyi",
}
_BY_NAME = {(r.oblast, r.name): r for r in RAIONS}

# alerts.in.ua city locations that are not raions: the raion they lie in.
CITY_UID_RAION = {1293: "kharkivskyi", 564: "zaporizkyi"}


def oblast_raions(oblast: str) -> list[Raion]:
    return [r for r in RAIONS if r.oblast == oblast]


def forecastable(r: Raion) -> bool:
    return r.oblast not in NO_DATA_OBLASTS


def from_official(oblast: str, raion: str) -> Raion | None:
    """Row of the official file (oblast, raion) -> Raion."""
    if oblast == "Kyiv City":
        return BY_ID["kyiv-city"]
    r = _BY_NAME.get((oblast, raion))
    if r is None and raion in OFFICIAL_ALIASES:
        r = BY_ID[OFFICIAL_ALIASES[raion]]
        if r.oblast != oblast:
            return None
    return r


_APOS = re.compile("[\u02bc\u2019\u2018`\u00b4]")


def norm_uk(title: str) -> str:
    """Normalise a Ukrainian location title for matching: apostrophes, case, spaces."""
    return " ".join(_APOS.sub("'", title).lower().split())


_BY_UK = {norm_uk(r.name_uk): r for r in RAIONS}
_BY_UK[norm_uk("Київ")] = BY_ID["kyiv-city"]


def from_alerts_in_ua(uid: int | str | None = None, title: str | None = None, raion: str | None = None) -> Raion | None:
    """Resolve an alerts.in.ua location to a raion.

    Tries, in order: the location UID (raions, Kyiv, Sevastopol, big cities),
    the location title when it is a raion, then the raion the location lies in
    (`location_raion`, present for hromadas and cities). Returns None for
    oblast-level and unknown locations.
    """
    if uid is not None:
        try:
            u = int(uid)
        except (TypeError, ValueError):
            u = None
        if u in BY_UID:
            return BY_UID[u]
        if u in CITY_UID_RAION:
            return BY_ID[CITY_UID_RAION[u]]
    for t in (title, raion):
        if t:
            r = _BY_UK.get(norm_uk(t))
            if r is not None:
                return r
    return None
