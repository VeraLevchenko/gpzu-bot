from dataclasses import dataclass
from typing import List, Optional
from lxml import etree

@dataclass
class Coord:
    x: str
    y: str

@dataclass
class EGRNData:
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None
    coordinates: List[Coord] = None

def _text(nodes) -> Optional[str]:
    for n in nodes or []:
        t = (n if isinstance(n, str) else (n.text if hasattr(n, 'text') else None))
        if t and t.strip():
            return t.strip()
    return None

def parse_egrn_xml(xml_bytes: bytes) -> EGRNData:
    """
    Универсальный разбор ЕГРН-XML для раздела 1 ГПЗУ:
    - кадастровый номер
    - площадь
    - адрес (по возможности)
    - регион / муниципалитет / поселение (если в XML)
    - таблица координат характерных точек (X/Y)
    """
    root = etree.fromstring(xml_bytes)

    # Кадастровый номер
    cadnum = _text(root.xpath(".//*[local-name()='cad_number' or local-name()='cadastralNumber']/text()"))

    # Площадь (пробуем атрибуты и текст)
    area = _text(root.xpath(
        ".//*[local-name()='area' or local-name()='Area']/text() | "
        ".//*[local-name()='area']/@*"
    ))

    # Адрес
    address = _text(root.xpath(
        ".//*[local-name()='address' or local-name()='Address']//*[text()][1]/text()"
    ))

    # Регион/МО/поселение (могут отсутствовать)
    region = _text(root.xpath(".//*[local-name()='region' or local-name()='Region']/text()"))
    municipality = _text(root.xpath(".//*[local-name()='municipality' or local-name()='Municipality']/text()"))
    settlement = _text(root.xpath(".//*[local-name()='locality' or local-name()='Settlement' or local-name()='City']/text()"))

    # Координаты: 1) EntitySpatial/Ordinate @X/@Y; 2) теги X/Y
    coords: List[Coord] = []
    for o in root.xpath(".//*[local-name()='EntitySpatial']//*[local-name()='Ordinate']"):
        x = o.get('X') or o.get('x') or _text(o.xpath("./@X | ./@x"))
        y = o.get('Y') or o.get('y') or _text(o.xpath("./@Y | ./@y"))
        if x and y:
            coords.append(Coord(x=str(x).replace(",", "."), y=str(y).replace(",", ".")))

    if not coords:
        points = root.xpath(".//*[local-name()='EntitySpatial']//*[local-name()='point' or local-name()='SpelementUnit']")
        for p in points:
            x = _text(p.xpath(".//*[local-name()='X' or local-name()='x']/text()"))
            y = _text(p.xpath(".//*[local-name()='Y' or local-name()='y']/text()"))
            if x and y:
                coords.append(Coord(x=x.replace(",", "."), y=y.replace(",", ".")))

    return EGRNData(
        cadnum=cadnum,
        address=address,
        area=area,
        region=region,
        municipality=municipality,
        settlement=settlement,
        coordinates=coords
    )
