# parsers/kpt_parser.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable
from io import BytesIO
import zipfile
import gzip
import re

from lxml import etree

Number = float
Point = Tuple[Number, Number]
Polygon = List[Point]          # один контур
MultiPolygon = List[Polygon]   # несколько контуров (дыр не различаем намеренно)


@dataclass
class Zone:
    name: str
    code: Optional[str]
    contours: MultiPolygon     # список контуров, каждый — список (x, y)


# -------------------------- общие утилиты чтения -------------------------- #

def _is_zip(data: bytes) -> bool:
    try:
        return zipfile.is_zipfile(BytesIO(data))
    except Exception:
        return False


def _sanitize_xml_bytes(raw: bytes) -> bytes:
    """Отрезаем мусор до первого '<' (BOM и т.п.)."""
    i = raw.find(b"<")
    if i == -1:
        raise ValueError("В файле отсутствует символ начала XML ('<'). Это не XML.")
    return raw[i:]


def _read_zip_member(zf: zipfile.ZipFile, name: str) -> bytes:
    data = zf.read(name)
    if name.lower().endswith(".xml.gz"):
        data = gzip.decompress(data)
    return _sanitize_xml_bytes(data)


_PRIOR_NAME_RE = re.compile(r"(zone|territor|зон|territorial|zones_and_territories)", re.I)

def _ensure_xml_bytes(data: bytes) -> bytes:
    """Поддержка как XML, так и ZIP с XML/XML.GZ внутри."""
    if _is_zip(data):
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            xmls = [n for n in names if n.lower().endswith((".xml", ".xml.gz"))]
            if not xmls:
                raise ValueError("В ZIP нет файлов .xml/.xml.gz с зонами.")
            # приоритет по «говорящему» имени
            pri = [n for n in xmls if _PRIOR_NAME_RE.search(n)]
            candidates = pri or sorted(xmls, key=lambda n: zf.getinfo(n).file_size, reverse=True)
            last_err = None
            for n in candidates:
                try:
                    xml = _read_zip_member(zf, n)
                    # sanity
                    etree.fromstring(xml)
                    return xml
                except Exception as ex:
                    last_err = ex
            raise ValueError(f"Не удалось извлечь пригодный XML из ZIP. Последняя ошибка: {last_err}")
    return _sanitize_xml_bytes(data)


def _root(xml_bytes: bytes) -> etree._Element:
    try:
        return etree.fromstring(xml_bytes)
    except Exception as ex:
        raise ValueError(f"Некорректный XML КПТ: {ex}")


def _text(node: Optional[etree._Element]) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _first_text(root: etree._Element, xpaths: Iterable[str]) -> Optional[str]:
    for xp in xpaths:
        el = root.xpath(xp)
        if el:
            if isinstance(el[0], etree._Element):
                val = _text(el[0])
            else:
                val = str(el[0]).strip()
            if val:
                return val
    return None


def _to_float(s: str) -> Optional[float]:
    if s is None:
        return None
    t = s.strip().replace(" ", "").replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ------------------------- парсинг геометрии зон -------------------------- #

def _ordinates_to_polygon(ords: List[etree._Element]) -> Polygon:
    """
    Преобразовать список <ordinate>…</ordinate> в один замкнутый контур.
    Координаты ищем по дочерним тегам x/y (без учёта регистров), fallback — по атрибутам X/Y.
    Если последняя точка совпадает с первой — не дублируем; иначе замыкаем.
    """
    pts: Polygon = []
    for o in ords:
        # дочерние теги 'x'/'y' (регистр игнорируем)
        x_txt = _first_text(o, ["./*[translate(local-name(),'XY','xy')='x']/text()"])
        y_txt = _first_text(o, ["./*[translate(local-name(),'XY','xy')='y']/text()"])
        if x_txt is None:
            x_txt = (o.get("X") or o.get("x"))
        if y_txt is None:
            y_txt = (o.get("Y") or o.get("y"))
        x = _to_float(x_txt or "")
        y = _to_float(y_txt or "")
        if x is None or y is None:
            continue
        pts.append((x, y))

    # чистим очевидные артефакты (повторы подряд)
    cleaned: Polygon = []
    last: Optional[Point] = None
    for p in pts:
        if last is None or (p[0] != last[0] or p[1] != last[1]):
            cleaned.append(p)
            last = p

    # замыкаем при необходимости
    if cleaned and (cleaned[0][0] != cleaned[-1][0] or cleaned[0][1] != cleaned[-1][1]):
        cleaned.append(cleaned[0])

    return cleaned


def _extract_polygons_under(node: etree._Element) -> MultiPolygon:
    """
    Собирает все контуры из:
      entity_spatial/spatials_elements/spatial_element/ordinates/ordinate
    Возвращает список контуров (каждый — Polygon).
    """
    polygons: MultiPolygon = []
    spatial_elements = node.xpath(
        "./*[local-name()='entity_spatial']"
        "/*[local-name()='spatials_elements']"
        "/*[local-name()='spatial_element']"
    )
    for se in spatial_elements:
        ords = se.xpath("./*[local-name()='ordinates']/*[local-name()='ordinate']")
        poly = _ordinates_to_polygon(ords)
        if len(poly) >= 4:   # минимум три уникальные точки + дублируемая первая для замыкания
            polygons.append(poly)
    return polygons


def _parse_from_zones_and_territories(root: etree._Element) -> List[Zone]:
    """
    Основной путь для твоего КПТ:
    /zones_and_territories/zones_and_territories_records/zones_and_territories_record
       /b_object_zones_and_territories/b_object/zone_name, zone_code
       /b_object_zones_and_territories/b_boundaries/b_contours_location/...
    """
    zones: List[Zone] = []
    recs = root.xpath(
        "/*[local-name()='extract_cadastral_plan_territory']"
        "/*[local-name()='zones_and_territories']"
        "/*[local-name()='zones_and_territories_records']"
        "/*[local-name()='zones_and_territories_record']"
    )
    for rec in recs:
        # имя/код
        name = _first_text(rec, [
            "./*[local-name()='b_object_zones_and_territories']/*[local-name()='b_object']/*[local-name()='zone_name']/text()",
            "./*[local-name()='b_object_zones_and_territories']/*[local-name()='b_object']/*[local-name()='name']/text()",
        ]) or ""
        code = _first_text(rec, [
            "./*[local-name()='b_object_zones_and_territories']/*[local-name()='b_object']/*[local-name()='zone_code']/text()",
            "./*[local-name()='b_object_zones_and_territories']/*[local-name()='b_object']/*[local-name()='code']/text()",
        ])

        # геометрия
        zones_node = rec.xpath("./*[local-name()='b_object_zones_and_territories']/*[local-name()='b_boundaries']/*[local-name()='b_contours_location']")
        contours: MultiPolygon = []
        for znode in zones_node:
            contours.extend(_extract_polygons_under(znode))

        if contours:
            zones.append(Zone(name=name, code=code, contours=contours))
    return zones


def _parse_from_territorial_zones(root: etree._Element) -> List[Zone]:
    """
    Альтернативный вариант структуры:
    /zones_and_territories/territorial_zones/territorial_zone
       /zone_name, zone_code
       /contours_location/...
    """
    out: List[Zone] = []
    tznodes = root.xpath(
        "/*[local-name()='extract_cadastral_plan_territory']"
        "/*[local-name()='zones_and_territories']"
        "/*[local-name()='territorial_zones']"
        "/*[local-name()='territorial_zone']"
    )
    for z in tznodes:
        name = _first_text(z, ["./*[local-name()='zone_name']/text()", "./*[local-name()='name']/text()"]) or ""
        code = _first_text(z, ["./*[local-name()='zone_code']/text()", "./*[local-name()='code']/text()"])

        contours: MultiPolygon = []
        cl_nodes = z.xpath("./*[local-name()='contours_location']")
        for cl in cl_nodes:
            contours.extend(_extract_polygons_under(cl))

        if contours:
            out.append(Zone(name=name, code=code, contours=contours))
    return out


# ------------------------------- публичное API ------------------------------ #

def parse_kpt_xml(input_bytes: bytes) -> List[Zone]:
    """
    Универсальный вход:
      - XML с КПТ по кварталу, где есть зоны;
      - ZIP с таким XML (и/или .xml.gz) внутри.

    Возвращает список зон: Zone(name, code, contours=[[(x,y),...], ...]).
    """
    xml = _ensure_xml_bytes(input_bytes)
    root = _root(xml)

    zones = _parse_from_zones_and_territories(root)
    if not zones:
        zones = _parse_from_territorial_zones(root)

    # небольшая нормализация кода (верхний регистр, дефисы оставляем)
    normed: List[Zone] = []
    for z in zones:
        code = (z.code or "").strip()
        code = code.upper() if code else None
        normed.append(Zone(name=z.name.strip(), code=code, contours=z.contours))

    return normed
