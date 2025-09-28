# parsers/kpt_parser.py
from dataclasses import dataclass
from typing import List, Optional, Tuple
from lxml import etree
import logging

log = logging.getLogger("gpzu-bot")

@dataclass
class Zone:
    name: str                 # Читаемое имя зоны
    index: Optional[str]      # Индекс/код зоны (например, "П-2")
    type_value: Optional[str] # Тип зоны из <type_zone><value>...
    contours: List[List[Tuple[float, float]]]  # Список контуров (x,y) float

def _text(nodes) -> Optional[str]:
    for n in nodes or []:
        t = (n if isinstance(n, str) else getattr(n, "text", None))
        if t and t.strip():
            return t.strip()
    return None

def _num(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    try:
        return float(v.replace(" ", "").replace(",", "."))
    except Exception:
        return None

def _xy_from_ordinate(ord_node) -> Optional[Tuple[float, float]]:
    x = _text(ord_node.xpath("./x/text()")) or _text(ord_node.xpath("./X/text()")) or _text(ord_node.xpath("./@x|./@X"))
    y = _text(ord_node.xpath("./y/text()")) or _text(ord_node.xpath("./Y/text()")) or _text(ord_node.xpath("./@y|./@Y"))
    xf, yf = _num(x), _num(y)
    if xf is None or yf is None:
        return None
    return (xf, yf)

def _extract_contours_from(parent) -> List[List[Tuple[float, float]]]:
    """
    Пытаемся извлечь контуры по нескольким вариантам веток:
      - b_contours_location/contours/contour/.../ordinates/ordinate
      - c_contours_location/...
      - contours_location/...
      - просто поиск ordinates/ordinate ниже записи
    """
    contours: List[List[Tuple[float, float]]] = []
    candidates = parent.xpath(
        "./b_contours_location/contours/contour | "
        "./c_contours_location/contours/contour | "
        "./contours_location/contours/contour | "
        ".//contours/contour"
    )
    if not candidates:
        candidates = parent.xpath(".//contour")

    for c in candidates:
        ord_nodes = c.xpath(".//ordinates/ordinate")
        pts: List[Tuple[float, float]] = []
        for o in ord_nodes:
            p = _xy_from_ordinate(o)
            if p:
                pts.append(p)
        if len(pts) >= 3:
            contours.append(pts)

    # Как запасной вариант — GML posList/coordinates под записью
    if not contours:
        pts: List[Tuple[float, float]] = []
        for pl in parent.xpath(".//*[local-name()='posList']"):
            text = (pl.text or "").strip()
            if not text:
                continue
            vals = [s for s in text.replace(",", " ").split() if s]
            pair: List[float] = []
            for s in vals:
                v = _num(s)
                if v is None:
                    pair = []
                    break
                pair.append(v)
                if len(pair) == 2:
                    pts.append((pair[0], pair[1])); pair = []
        if not pts:
            for gc in parent.xpath(".//*[local-name()='coordinates']"):
                text = (gc.text or "").strip()
                if not text:
                    continue
                tuples = [t for t in text.split() if t]
                for t in tuples:
                    if "," in t:
                        x, y = t.split(",", 1)
                        xf, yf = _num(x), _num(y)
                        if xf is not None and yf is not None:
                            pts.append((xf, yf))
        if len(pts) >= 3:
            contours.append(pts)

    return contours

def parse_kpt_xml(xml_bytes: bytes) -> List[Zone]:
    """
    КПТ (карта-план территории) по примеру:
      /extract_cadastral_plan_territory/cadastral_blocks/cadastral_block/
         zones_and_territories_boundaries/(zones_and_territories_record | zone_and_territories_record)
    Отбираем записи, у которых объект — "Территориальная зона".
    Затем читаем реквизиты и контуры (ordinates/ordinate).
    """
    root = etree.fromstring(xml_bytes)

    # Находим записи с границами зон/территорий (оба варианта имен узла)
    records = root.xpath(
        ".//zones_and_territories_boundaries/zones_and_territories_record | "
        ".//zones_and_territories_boundaries/zone_and_territories_record"
    )

    zones: List[Zone] = []
    skipped_no_tz = 0
    skipped_no_geom = 0
    for rec in records:
        # Проверяем, что это именно территориальная зона
        # b_object_zones_and_territories/b_object/type_boundary/value == "Территориальная зона"
        type_boundary = _text(rec.xpath("./b_object_zones_and_territories/b_object/type_boundary/value/text()")) \
                     or _text(rec.xpath(".//type_boundary/value/text()"))
        if not type_boundary or "территориаль" not in type_boundary.lower():
            skipped_no_tz += 1
            continue

        type_value = _text(rec.xpath("./type_zone/value/text()")) or _text(rec.xpath(".//type_zone/value/text()"))
        index      = _text(rec.xpath("./index/text()")) or _text(rec.xpath(".//index/text()"))
        name_by_doc = _text(rec.xpath("./name_by_doc/text()")) or _text(rec.xpath(".//name_by_doc/text()"))
        description = _text(rec.xpath("./description/text()")) or _text(rec.xpath(".//description/text()"))

        if name_by_doc:
            name = name_by_doc
        elif description:
            name = description
        elif type_value and index:
            name = f"{type_value} ({index})"
        elif type_value:
            name = type_value
        elif index:
            name = index
        else:
            # без имени и индекса смысла нет
            skipped_no_tz += 1
            continue

        contours = _extract_contours_from(rec)
        if not contours:
            skipped_no_geom += 1
            continue

        zones.append(Zone(name=name, index=index, type_value=type_value, contours=contours))

    log.info("KPT parse: records=%d, zones=%d, skipped_no_tz=%d, skipped_no_geom=%d",
             len(records), len(zones), skipped_no_tz, skipped_no_geom)
    return zones
