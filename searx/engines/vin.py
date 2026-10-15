# SPDX-License-Identifier: AGPL-3.0-or-later
"""VIN decoder -- a fully *offline* engine.

The engine detects a 17 character Vehicle Identification Number (VIN) in the
query and decodes it locally: no request ever leaves the instance.

Two VIN layouts are known and both are supported:

**European layout** (most Mercedes-Benz, BMW, VW, ... sold in the EU)

===== ==========================================================
Pos.  Content
===== ==========================================================
1-3   WMI (manufacturer, country, division)
4-9   vehicle *type* (Mercedes: ``Baureihe.Nachnummer``, e.g.
      ``156912`` -> 156.912 -> GLA 180 d)
10    1 = left hand drive, 2 = right hand drive
11    plant / factory code
12-17 production serial number
===== ==========================================================

**North American layout** (USA/Canada, 49 CFR 565)

===== ==========================================================
Pos.  Content
===== ==========================================================
1-3   WMI
4-5   series (e.g. ``DA`` -> W166 M-Class/GLE-Class)
6-7   model within the series (e.g. ``5J`` -> ML 350 4MATIC)
8     restraint system
9     check digit (mandatory in North America)
10    model year
11    plant
12-17 production serial number
===== ==========================================================

Configuration
=============

.. code:: yaml

  - name: vin
    engine: vin
    shortcut: vin
    categories: [general]
    disabled: false

"""

import re
import typing as t

from searx.result_types import EngineResults
from searx.enginelib import EngineAbout

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"
categories = ["general"]
timeout = 2.0

about = EngineAbout(
    results="HTML",
    description="Offline decoder of vehicle identification numbers (VIN).",
)

VIN_RE = re.compile(r"(?<![A-Z0-9])([A-HJ-NPR-Z0-9]{17})(?![A-Z0-9])", re.IGNORECASE)

# ---------------------------------------------------------------------------
# ISO 3779 helpers
# ---------------------------------------------------------------------------

TRANSLITERATION = {
    **{c: str(i + 1) for i, c in enumerate("ABCDEFGH")},
    **{c: str(i + 1) for i, c in enumerate("JKLMN")},
    "P": "6", "Q": "7", "R": "8", "S": "9",
    "T": "1", "U": "2", "V": "3", "W": "4", "X": "5", "Y": "6", "Z": "7",
}
WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# position 1: country of assembly
COUNTRY = {
    "1": "USA", "2": "Canada", "3": "Mexico / Central America", "4": "USA",
    "5": "USA (manufacturer)", "6": "Australia", "7": "New Zealand", "8": "South America",
    "9": "South America (Brazil, Argentina, ...)", "A": "South Africa", "B": "England", "C": "Europe (BE, LU, DE)",
    "D": "Europe (AT, BG, CZ, DE, DK, ES, FI, FR, GR, HU, IT, NL, NO, PL, PT, RO, SE, SI, SK, ...)",
    "E": "Europe (FR, DE, IT)", "F": "Europe (BE, DE)", "G": "Europe (DE, GB)", "H": "Europe (TR)", "J": "Japan",
    "K": "Europe (CZ, SK)", "L": "China", "M": "India / Thailand", "N": "Iran", "P": "North America (Mexico)",
    "R": "Taiwan", "S": "England", "T": "Europe (CH, DE)", "U": "Europe (IE)", "V": "Europe (FR, ES)",
    "W": "Germany", "X": "Russia", "Y": "Belgium", "Z": "Italy",
}

# position 1-3: world manufacturer identifier
WMI = {
    "WBA": "BMW (Cars, Germany)", "WBS": "BMW (M, Germany)", "WBY": "BMW (i, Germany)",
    "WBK": "BMW (Motorcycles)", "W1K": "Mercedes-Benz (passenger cars, Germany)",
    "W1N": "Mercedes-Benz (SUV / vans, Germany)", "W1Y": "Mercedes-Benz (special series)",
    "WDB": "Mercedes-Benz (passenger cars, Germany)", "WDC": "Mercedes-Benz (SUV / off-road, Germany)",
    "WDD": "Mercedes-Benz (passenger cars, Germany)", "WDF": "Mercedes-Benz (vans / MPV)",
    "W1W": "Mercedes-Benz AG (special series)", "WD3": "Mercedes-Benz AG (special series)",
    "WD4": "Mercedes-Benz AG (special series)", "WMX": "Mercedes-AMG GmbH",
    "4JG": "Mercedes-Benz U.S. International (Tuscaloosa, Alabama)",
    "55S": "Mercedes-Benz U.S. International (USA)", "9BM": "Mercedes-Benz Brazil",
    "VSA": "Mercedes-Benz Spain (Vitoria-Gasteiz)", "LB1": "Beijing Benz (China)",
    "WAU": "Audi (Germany)", "W8A": "Audi (China)", "WVW": "Volkswagen (Germany)",
    "WV1": "Volkswagen (commercial vehicles)", "WVG": "Volkswagen SUV", "WVG2": "Volkswagen (SUV)",
    "TMT": "Tesla (USA)", "7SA": "Tesla (USA)", "5YJ": "Tesla (USA)",
    "JT1": "Toyota (Japan)", "JT2": "Toyota (Japan)", "JT3": "Toyota (Japan)",
    "JTD": "Toyota (Japan)", "JTE": "Toyota (Japan)", "JTF": "Toyota (Japan)",
    "JTK": "Toyota (Japan)", "JTL": "Toyota (Japan)", "JTN": "Toyota (Japan)", "JTR": "Toyota (Japan)",
    "JTY": "Toyota (Japan)", "JTZ": "Toyota (Japan)", "JHM": "Honda (Japan)",
    "JHL": "Honda (Japan)", "JHF": "Honda (Japan)", "JHG": "Honda (Japan)", "JHH": "Honda (Japan)",
    "1HG": "Honda (USA)", "2HG": "Honda (USA)", "3HG": "Honda (USA)", "4HG": "Honda (USA)",
    "JH4": "Honda (USA)",
    "KMH": "Hyundai (Korea)", "KMY": "Kia (Korea)", "Z94": "Kia (Korea)", "U5Y": "Kia (Korea)",
    "1FT": "Ford (USA)", "1FA": "Ford (USA)", "WF0": "Ford (Germany)", "SB1": "Ford (UK)",
    "1G1": "Chevrolet (USA)", "1GC": "Chevrolet (USA)", "3G1": "Chevrolet (Mexico)",
    "X4X": "BMW Russia", "XW8": "Volkswagen Russia", "XW1": "Volkswagen Russia",
    "YV3": "Volvo (Sweden)", "YV2": "Volvo (Sweden)", "XU": "Dacia (Romania)",
    "UU1": "Renault (France)", "VF1": "Renault (France)", "VF3": "Peugeot (France)",
    "VF6": "Renault Trucks (France)", "VNE": "Iveco (Italy)", "TMT2": "Toyota",
    "S3T": "SsangYong (Korea)", "Z63": "Smart (Germany)", "MB1": "Mercedes (Hungary)",
    "MA3": "Mazda (Japan)", "JM3": "Mazda (Japan)", "LSV": "Volkswagen (Slovakia)",
    "TRU": "Audi (Hungary)", "WAU3": "Audi (Germany)", "X4X2": "BMW (Russia)",
    "KN3": "Kia (Slovakia)", "TMA": "Hyundai (Czech Republic)", "TMB": "Škoda (Czech Republic)",
    "UU0": "Opel (Germany)", "W0L": "Opel (Germany)", "W0V": "Opel (Germany)",
    "WA1": "Audi (Slovakia)", "WA2": "Audi (Germany)", "TMB1": "Škoda (Czech Republic)",
    "PE1": "Ford (Belgium)", "S1AL": "Jaguar Land Rover (UK)", "SAL": "Land Rover (UK)",
    "SCA": "Rolls-Royce (UK)", "SCF": "Aston Martin (UK)", "SB1": "Ford (UK)",
    "WV2": "Volkswagen (bus)", "WMA": "MAN (Germany)", "WMAN": "MAN (Germany)",
    "WDB0": "Mercedes-Benz (Germany)", "JN8": "Nissan (Japan)", "JN1": "Nissan (Japan)",
    "JHM1": "Honda (USA)", "4T1": "Toyota (USA)", "5TB": "Toyota (USA)", "JT3": "Toyota (Japan)",
}

# Mercedes-Benz: plant code (position 11 of the European layout)
MB_PLANT = {
    "A": "Tuscaloosa (Alabama, USA)",
    "B": "Bremen (Germany)",
    "D": "Düsseldorf (Germany)",
    "H": "Hanau (Germany)",
    "J": "Sindelfingen (Germany)",
    "K": "Köthen (Germany)",
    "L": "Ludwigsfelde (Germany)",
    "M": "Metten (Germany)",
    "N": "Bremen (Germany)",
    "S": "Stuttgart-Untertürkheim (Germany)",
    "U": "Untertürkheim (Germany)",
    "W": "Stuttgart (Germany)",
    "X": "Graz (Austria)",
    "C": "Obregón, Mexico",
}

# Mercedes-Benz: European type numbers (positions 4-9) -> model / engine
MB_TYPE = {
    # X156 GLA
    "156912": "GLA 180 d — 1.5 l turbo-diesel OM607.951, 109 hp, передний привод (07.2014–05.2018)",
    "156905": "GLA 220 CDI 4MATIC — 2.1 l turbodiesel OM651, 170 hp",
    "156902": "GLA 200 — 2.0 l petrol M271, передний привод",
    "156943": "GLA 200 d — 2.1 l turbodiesel OM651, 4MATIC",
    "156946": "GLA 250 d 4MATIC — 2.1 l turbodiesel OM651",
    "156952": "GLA 250 4MATIC — 2.0 l petrol M271",
    # W163 / W164 / W166 M-Class
    "163154": "ML 320 — 3.2 l petrol M112 (W163)",
    "163157": "ML 350 — 3.5 l petrol M112 (W163)",
    "163128": "ML 400 CDI — 4.0 l turbodiesel OM628 (W163)",
    "164115": "ML 320 — 3.2 l petrol M112 (W164)",
    "164118": "ML 350 — 3.5 l petrol M272 (W164)",
    "164120": "ML 300 CDI 4MATIC — 3.0 l turbodiesel OM642 (W164)",
    "164121": "ML 350 CDI 4MATIC (W164)",
    "164124": "ML 350 BlueTec 4MATIC — 3.0 l turbodiesel OM642 (W164)",
    "164125": "ML 350 BlueTec 4MATIC (W164)",
    "164126": "ML 350 4MATIC — 3.5 l petrol M272 (W164)",
    "164128": "ML 450 CDI 4MATIC (W164)",
    "164156": "ML 350 — 3.5 l petrol M272 (W164)",
    "164172": "ML 500 4MATIC — 5.5 l petrol M273 (W164)",
    "164175": "ML 500 (W164)",
    "164177": "ML 63 AMG 4MATIC (W164)",
    "164186": "ML 350 4MATIC — 3.5 l petrol M272 (W164)",
    "166023": "ML 350 BlueTec 4MATIC — 3.0 l turbodiesel OM642 (W166)",
    "166024": "ML 350 BlueTec 4MATIC — 3.0 l turbodiesel OM642 (W166)",
    "166056": "ML/GLE 400 4MATIC — 3.0 l petrol M276 (W166)",
    "166057": "ML 350 4MATIC — 3.5 l petrol M276.955 (W166)",
    "166058": "ML 350 — 3.5 l petrol M276, задний привод (W166)",
    # W204 / W205 C-Class
    "204049": "C 220 CDI (W204)", "204054": "C 280 (W204)", "204056": "C 350 (W204)",
    "204057": "C 350 4MATIC (W204)", "204087": "C 220 CDI 4MATIC (W204)",
    "204089": "C 250 CGI (W204)", "204094": "C 200 CGI (W204)",
    "205023": "C 180 BlueTec (W205)", "205024": "C 200 BlueTec (W205)",
    "205029": "C 220 BlueTec (W205)", "205033": "C 250 (W205)", "205035": "C 300 (W205)",
    "205037": "C 300 4MATIC (W205)", "205040": "C 220 CDI (W205)",
    # W211 / W212 / W213 E-Class
    "211026": "E 320 CDI (W211)", "211056": "E 350 (W211)", "211065": "E 320 (W211)",
    "211070": "E 430 (W211)", "211082": "E 220 CDI (W211)", "211087": "E 350 4MATIC (W211)",
    "211090": "E 550 4MATIC (W211)",
    "212002": "E 200 (W212)", "212026": "E 220 CDI (W212)", "212027": "E 300 (W212)",
    "212029": "E 350 4MATIC (W212)", "212037": "E 350 BlueTec 4MATIC (W212)",
    "212094": "E 250 BlueTec 4MATIC (W212)", "212096": "E 350 (W212)", "212197": "E 250 (W212)",
    "213004": "E 300 (W213)", "213015": "E 350 4MATIC (W213)", "213043": "E 220 d (W213)",
    # W221 / W222 S-Class
    "221120": "S 350 (W221)", "221122": "S 400 Hybrid (W221)", "221125": "S 500 (W221)",
    "221126": "S 600 (W221)", "221155": "S 350 4MATIC (W221)", "221173": "S 63 AMG (W221)",
    "222033": "S 400 Hybrid (W222)", "222035": "S 450 (W222)", "222050": "S 500 (W222)",
    # other series
    "117342": "SLK 200 Kompressor (R171)", "117343": "SLK 250 (R171)", "117344": "SLK 350 (R171)",
    "117347": "SLK 55 AMG (R171)",
    "246040": "B 180 (W246)", "246042": "B 200 (W246)", "246043": "B 220 CDI (W246)",
    "176024": "C 250 (W176)",
    "253923": "GLK 220 CDI 4MATIC (X253)", "253945": "GLK 220 CDI (X253)",
    "253964": "GLK 220 CDI (X253)", "253946": "GLK 300 4MATIC (X253)",
}

# Mercedes-Benz: North American layout, positions 4-5 (series) and 6-7 (model)
MB_SERIES = {
    "4J": "M-Class (W164) ML",
    "DA": "M-Class / GLE-Class (W166)",
    "BB": "M-Class / GLE-Class (W166, USA)",
    "GF": "C-Class (W204)",
    "WF": "C-Class (W205)",
    "RF": "C-Class (W203)",
    "JF": "E-Class (W210)",
    "UF": "E-Class (W211)",
    "HF": "E-Class (W212)",
    "ZF": "E-Class (W213)",
    "NG": "S-Class (W221)",
    "NF": "S-Class (W220)",
    "5J": "CLA (C117)", "SJ": "CLA (C117)", "LJ": "CLS (C218)", "DJ": "CLS (C219)",
    "7X": "AMG GT (X290)", "4H": "GLA (X247)", "4X": "GLB (X247)", "0X": "GLK / GLC (X253)",
    "0G": "GLK (X204)", "3F": "A-Class (W177)", "WF2": "A-Class (W177)",
    "2J": "CLS (C257)", "PJ": "CL-Class (C215)", "NG2": "S-Class (W222)", "UH": "E-Class (W212) wagon",
}

MB_MODEL = {
    "8G": "ML 350 4MATIC (3.5 l M272), 2010–2011",
    "2E": "ML 350 BlueTec 4MATIC (3.0 l OM642), 2012–2014",
    "5J": "ML 350 4MATIC (3.5 l M276), 2012–2015",
    "5H": "ML 350 4MATIC (3.5 l M276), 2012–2015",
    "5K": "ML 350 4MATIC (3.5 l M276), 2012–2015",
    "8B": "C 300 4MATIC (3.0 l M272), 2010–2012",
    "56": "C 350 (3.5 l M272), 2008–2009",
    "8A": "C 250 4MATIC (1.8 l M271), 2013–2014",
    "5G": "C 350 (3.5 l M272), 2010–2011",
    "4H": "C 250 (1.8 l M271), 2012–2014",
    "4J": "C 300 (2.0 l M274), 2017–2019",
    "4K": "C 300 4MATIC (2.0 l M274), 2017–2019",
    "4G": "C 250 (2.0 l M270), 2014–2019",
    "8D": "C 300 (2.0 l M264), 2019–2021",
    "8E": "C 300 4MATIC (2.0 l M264), 2019–2023",
    "5H2": "E 350 (3.5 l M276), 2012–2016",
    "8H": "E 350 4MATIC (3.5 l M272), 2010–2011",
    "8J": "E 350 4MATIC (3.5 l M276), 2012–2016",
    "9H": "E 250 BlueTec 4MATIC (2.1 l OM651), 2014–2016",
    "9A": "E 550 4MATIC (5.5 l M273), 2010–2011",
    "9B": "E 550 4MATIC (4.7 l M278), 2012–2014",
    "7D": "S 550 (4.7 l M278), 2012–2013",
    "7H": "S 63 AMG (6.2 l M156), 2010–2011",
    "4E": "A 220 (2.0 l M260), 2019–2022",
    "5B": "AMG A 35 4MATIC (2.0 l M260), 2020–2022",
    "4E2": "CLA 250 (2.0 l M270), 2014–2019",
    "5C": "CLA 45 AMG 4MATIC (2.0 l M133), 2017–2021",
}

# model year codes, position 10 (North American layout)
YEAR_1980 = {
    **{str(i): 1980 + i for i in range(10)},
    **{c: 1980 + i for i, c in enumerate("ABCDEFGHJKLMNPRSTVWXY", start=10)},
}
YEAR_2010 = {
    **{str(i): 2010 + i for i in range(10)},
    **{c: 2010 + i for i, c in enumerate("ABCDEFGHJKLMNPRSTVWXY", start=10)},
}

LINK_NHTSA = "https://vpic.nhtsa.dot.gov/decoder/"
LINK_MB = "https://www.mercedes-benz.com/vin-recall"
LINK_UOKIK = "https://uokik.gov.pl/powiadomienia.php"
LINK_HISTORIA = "https://historiaserwisowamercedes.pl/"


def check_digit(vin: str) -> str:
    """Calculate the ISO 3779 check digit of a VIN."""
    total = 0
    for char, weight in zip(vin, WEIGHTS):
        value = int(char) if char.isdigit() else int(TRANSLITERATION[char])
        total += value * weight
    rest = total % 11
    return "X" if rest == 10 else str(rest)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _row(position: str, meaning: str, value: str) -> str:
    return (
        f"<tr><td class='vin-pos'>{_esc(position)}</td>"
        f"<td class='vin-meaning'>{_esc(meaning)}</td>"
        f"<td class='vin-value'>{value}</td></tr>"
    )


def decode(vin: str) -> str:
    """Decode a VIN and return an HTML fragment."""
    vin = vin.upper()
    wmi = vin[0:3]
    series = vin[3:9]
    plant = vin[10]
    serial = vin[11:17]

    rows = []
    headline = []
    notes = []

    # --- 1..3 ------------------------------------------------------------
    manufacturer = WMI.get(wmi)
    rows.append(_row("1", "Страна сборки", _esc(COUNTRY.get(vin[0], "неизвестно"))))
    rows.append(_row("2–3", "Производитель / подразделение", _esc(manufacturer or "неизвестно")))

    is_mercedes = wmi[:2] in ("WD", "W1", "4J", "55", "9B", "WM") or wmi in ("LB1", "VSA")

    # --- 4..9 ------------------------------------------------------------
    if is_mercedes and vin[3:9].isdigit():
        # European layout: positions 4-9 carry the Mercedes type number
        typ = vin[3:9]
        model = MB_TYPE.get(typ)
        headline.append("Mercedes-Benz")
        rows.append(
            _row(
                "4–9",
                "Тип автомобиля (Baureihe.Nachnummer)",
                f"{_esc(typ[:3])}.{_esc(typ[3:])}" + (f" — {_esc(model)}" if model else ""),
            )
        )
        if model:
            headline.append(model.split(" — ")[0])
    elif is_mercedes:
        ser = MB_SERIES.get(vin[3:5], "")
        mod = MB_MODEL.get(vin[5:7], "")
        rows.append(_row("4–5", "Серия (North American layout)", _esc(ser or "неизвестно")))
        rows.append(_row("6–7", "Модель в серии", _esc(mod or "неизвестно")))
        if ser:
            headline.append(ser)
    else:
        rows.append(_row("4–8", "Vehicle Descriptor Section", _esc(series)))

    # --- 9 / 10 ----------------------------------------------------------
    calculated = check_digit(vin)
    if calculated == vin[8]:
        rows.append(_row("9", "Контрольная цифра", f"{_esc(vin[8])} — совпадает с расчётной"))
    else:
        rows.append(
            _row(
                "9",
                "Контрольная цифра",
                f"в VIN: {_esc(vin[8])}, расчёт по ISO 3779: {_esc(calculated)}",
            )
        )
        notes.append(
            "Контрольная цифра по ISO 3779 обязательна только для автомобилей "
            "Северной Америки (49 CFR 565). Для европейских VIN её отсутствие или несовпадение "
            "<strong>не является признаком подделки</strong> — то же самое показывает и официальный "
            "декодер NHTSA (ошибка \"Check Digit does not calculate properly\")."
        )

    if is_mercedes and vin[3:9].isdigit():
        rows.append(
            _row("10", "Рулевое управление", "левостороннее (LHD)" if vin[9] == "1" else "правостороннее (RHD)")
        )
        notes.append(
            "В европейском формате Mercedes-Benz VIN <strong>не содержит</strong> года выпуска и контрольной цифры: "
            "год, цвет, комплектация и т.д. фиксируются в DataCard (заводской паспорт) и в документах. "
            "Приблизительно датировать выпуск можно по производственному номеру (позиции 12–17) — "
            "он растёт в пределах заводской серии."
        )
    else:
        year = YEAR_2010.get(vin[9]) or YEAR_1980.get(vin[9])
        rows.append(_row("10", "Год выпуска", _esc(str(year) if year else "неизвестно")))

    # --- 11 --------------------------------------------------------------
    if is_mercedes:
        plant_name = MB_PLANT.get(plant, "неизвестный завод")
        rows.append(_row("11", "Завод сборки", f"{_esc(plant)} — {_esc(plant_name)}"))
        if plant == "J":
            headline.append("завод Sindelfingen (Германия)")
    else:
        rows.append(_row("11", "Завод сборки", _esc(plant)))

    rows.append(_row("12–17", "Производственный номер (серия)", _esc(serial)))

    if headline:
        summary = " · ".join(headline)
    else:
        summary = f"{_esc(wmi)} · тип {series}"

    html = [
        "<div class='vin-card'>",
        f"<h3 class='vin-headline'>{_esc(summary)}</h3>",
        f"<p class='vin-vin'>{_esc(vin)}</p>",
        "<table class='vin-table'>",
        "<tr><th>Позиция</th><th>Что кодирует</th><th>Значение</th></tr>",
        *rows,
        "</table>",
    ]

    if notes:
        html.append("<ul class='vin-notes'>")
        for note in notes:
            html.append(f"<li>{note}</li>")
        html.append("</ul>")

    html.append("<p class='vin-links'>Проверить: ")
    html.append(f"<a href='{LINK_NHTSA}'>NHTSA vPIC</a> · ")
    html.append(f"<a href='{LINK_MB}'>отзывы Mercedes-Benz</a> · ")
    html.append(f"<a href='{LINK_UOKIK}'>списки VIN кампаний UOKiK (PL)</a> · ")
    html.append(f"<a href='{LINK_HISTORIA}'>история сервиса Mercedes (PL)</a>")
    html.append("</p></div>")

    return "".join(html)


def search(query: str, params: "RequestParams") -> EngineResults:  # pylint: disable=unused-argument
    """Decode a VIN found in the query."""
    res = EngineResults()

    match = VIN_RE.search(query)
    if not match:
        return res

    vin = match.group(1)
    res.add(res.types.Answer(answer=decode(vin), template="answer/vin.html"))
    return res
