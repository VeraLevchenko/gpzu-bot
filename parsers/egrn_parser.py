# parsers/egrn_parser.py
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple, Iterable
import re
import zipfile
import gzip

from lxml import etree


@dataclass
class Coord:
    num: str
    x: str
    y: str


@dataclass
class EGRNData:
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None  # строкой, как в исходном XML
    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None
    coordinates: Optional[List[Coord]] = None
    is_land: bool = False
    has_coords: bool = False
    capital_objects: Optional[List[str]] = None


# ------------------------------- helpers ------------------------------- #

def _is_zip(data: bytes) -> bool:
    try:
        return zipfile.is_zipfile(BytesIO(data))
    except Exception:
        return False


def _sanitize_xml_bytes(raw: bytes) -> bytes:
    """
    Отрезаем всё до первого символа '<' (на случай BOM/мусора).
    """
    i = raw.find(b"<")
    if i == -1:
        raise ValueError("В файле отсутствует символ начала XML ('<'). Это не XML-выписка.")
    return raw[i:]


_PRIOR_NAME_RE = re.compile(r"(land|parcel|record|выпис|egrn)", re.I)


def _read_zip_member(zf: zipfile.ZipFile, name: str) -> bytes:
    data = zf.read(name)
    if name.lower().endswith(".xml.gz"):
        try:
            data = gzip.decompress(data)
        except Exception as ex:
            raise ValueError(f"Не удалось распаковать {name}: {ex}")
    return _sanitize_xml_bytes(data)


def _pick_xml_from_zip(zf: zipfile.ZipFile) -> bytes:
    all_names = [n for n in zf.namelist() if not n.endswith("/")]
    xml_like = [n for n in all_names if n.lower().endswith((".xml", ".xml.gz"))]
    if not xml_like:
        raise ValueError("В ZIP-архиве не найдено ни одного файла .xml или .xml.gz.")

    priority = [n for n in xml_like if _PRIOR_NAME_RE.search(n)]
    candidates = priority or sorted(xml_like, key=lambda n: zf.getinfo(n).file_size, reverse=True)

    last_err: Optional[Exception] = None
    for name in candidates:
        try:
            data = _read_zip_member(zf, name)
            etree.fromstring(data)  # простая проверка валидности
            return data
        except Exception as ex:
            last_err = ex
            continue

    raise ValueError(f"Не удалось извлечь пригодный XML из ZIP. Последняя ошибка: {last_err}")


def _ensure_xml_bytes(input_bytes: bytes) -> bytes:
    if _is_zip(input_bytes):
        with zipfile.ZipFile(BytesIO(input_bytes)) as zf:
            return _pick_xml_from_zip(zf)
    return _sanitize_xml_bytes(input_bytes)


def _root(xml_bytes: bytes) -> etree._Element:
    try:
        return etree.fromstring(xml_bytes)
    except Exception as ex:
        raise ValueError(f"Некорректный XML: {ex}")


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


def _collect_texts(root: etree._Element, xpaths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for xp in xpaths:
        for el in root.xpath(xp):
            if isinstance(el, etree._Element):
                val = _text(el)
            else:
                val = str(el).strip()
            if val:
                out.append(val)
    return out


# ---------------------------- field extractors ---------------------------- #

_CADNUM_PAT = re.compile(r"\d{2}:\d{2}:\d{6,}:\d+")

def _extract_cadnum(root: etree._Element) -> Optional[str]:
    return _first_text(root, [
        "//*[local-name()='cad_number']/text()",
        "//*[local-name()='cadnum']/text()",
        "//*[local-name()='cadastreNumber']/text()",
        "//*[local-name()='CadastralNumber']/text()",
        "//*[local-name()='object']/@cadastralNumber",
    ])


def _extract_address(root: etree._Element) -> Optional[str]:
    return _first_text(root, [
        "//*[local-name()='readable_address']/text()",
        "//*[local-name()='address']/text()",
        "//*[local-name()='Address']/text()",
        "//*[local-name()='location']/text()",
    ])


def _extract_area(root: etree._Element) -> Optional[str]:
    # В твоём примере: /land_record/params/area/value
    texts = _collect_texts(root, [
        "//*[local-name()='params']/*[local-name()='area']/*[local-name()='value']/text()",
        "//*[local-name()='area']/*[local-name()='value']/text()",
        "//*[local-name()='area_value']/text()",
        "//*[local-name()='AreaValue']/text()",
        "//*[local-name()='area']/text()",   # общий запасной вариант
        "//*[local-name()='Area']/text()",
    ])
    for t in texts:
        s = re.sub(r"[^\d,\.]", "", t).replace(",", ".")
        if s:
            return re.sub(r"\.0+$", "", s)
    return None


def _extract_is_land(root: etree._Element) -> bool:
    # корневой элемент extract_about_property_land или наличие land_record
    if root.tag.lower().endswith("extract_about_property_land"):
        return True
    nodes = root.xpath("//*[local-name()='land_record' or local-name()='parcel' or local-name()='LandRecord']")
    return bool(nodes)


def _extract_capital_objects(root: etree._Element, own_cad: Optional[str]) -> List[str]:
    """
    Берём только included_objects (объекты в границах ЗУ),
    исключая сам кадастровый номер участка.
    """
    objs = _collect_texts(root, [
        "//*[local-name()='cad_links']/*[local-name()='included_objects']/*[local-name()='included_object']/*[local-name()='cad_number']/text()"
    ])
    out: List[str] = []
    seen = set()
    for cad in objs:
        cad = cad.strip()
        if not cad or not _CADNUM_PAT.fullmatch(cad):
            continue
        if own_cad and cad == own_cad:
            continue
        if cad in seen:
            continue
        seen.add(cad)
        out.append(cad)
    return out


def _coords_from_ordinates(nodes: List[etree._Element]) -> List[Coord]:
    coords: List[Coord] = []
    for o in nodes:
        # дочерние элементы x/y/ord_nmb (регистр может быть любой)
        x = _first_text(o, ["./*[translate(local-name(),'XY','xy')='x']/text()"])
        y = _first_text(o, ["./*[translate(local-name(),'XY','xy')='y']/text()"])
        num = _first_text(o, ["./*[translate(local-name(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='ord_nmb']/text()"])
        # на всякий случай — попробуем атрибуты, если есть
        if not x:
            x = (o.get("X") or o.get("x") or "").strip()
        if not y:
            y = (o.get("Y") or o.get("y") or "").strip()
        if not num:
            num = (o.get("Num") or o.get("num") or "").strip()
        if x or y or num:
            coords.append(Coord(num=num or "", x=(x or ""), y=(y or "")))
    return coords


def _extract_coords(root: etree._Element) -> List[Coord]:
    """
    Приоритет: основной контур из contours_location (границы ЗУ),
    затем — контуры из object_parts (если вдруг основных нет).
    Никакого переупорядочивания — оставляем как в XML.
    """
    # 1) Основные границы участка:
    ords_main = root.xpath(
        "//*[local-name()='contours_location']"
        "/*[local-name()='contours']"
        "/*[local-name()='contour']"
        "/*[local-name()='entity_spatial']"
        "/*[local-name()='spatials_elements']"
        "/*[local-name()='spatial_element']"
        "/*[local-name()='ordinates']"
        "/*[local-name()='ordinate']"
    )
    coords = _coords_from_ordinates(ords_main)

    # 2) Если нет — попробуем по object_parts (части объекта/контуры):
    if not coords:
        ords_parts = root.xpath(
            "//*[local-name()='object_parts']"
            "/*[local-name()='object_part']"
            "/*[local-name()='contours']"
            "/*[local-name()='contour']"
            "/*[local-name()='entity_spatial']"
            "/*[local-name()='spatials_elements']"
            "/*[local-name()='spatial_element']"
            "/*[local-name()='ordinates']"
            "/*[local-name()='ordinate']"
        )
        coords = _coords_from_ordinates(ords_parts)

    # 3) В последнюю очередь — старые варианты ЕГРН (Ordinate/X/Y/Num как атрибуты):
    if not coords:
        ords_old = root.xpath(
            "//*[local-name()='EntitySpatial']"
            "//*[local-name()='SpelementUnit']"
            "//*[local-name()='Ordinate']"
        )
        for o in ords_old:
            num = (o.get("Num") or o.get("num") or "").strip()
            x = (o.get("X") or o.get("x") or "").strip()
            y = (o.get("Y") or o.get("y") or "").strip()
            if x or y or num:
                coords.append(Coord(num=num, x=x, y=y))

    return coords


def _extract_admins(root: etree._Element) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    region = _first_text(root, [
        "//*[local-name()='region']/text()",
        "//*[local-name()='Region']/text()",
        "//*[local-name()='subject']/text()",
    ])
    municipality = _first_text(root, [
        "//*[local-name()='municipality']/text()",
        "//*[local-name()='Municipality']/text()",
    ])
    settlement = _first_text(root, [
        "//*[local-name()='settlement']/text()",
        "//*[local-name()='Settlement']/text()",
        "//*[local-name()='locality']/text()",
    ])
    return region, municipality, settlement


# ------------------------------- public api ------------------------------- #

def parse_egrn_xml(input_bytes: bytes) -> EGRNData:
    """
    Универсальный вход:
      - raw XML (bytes)
      - ZIP с XML/XML.GZ внутри
    """
    xml_bytes = _ensure_xml_bytes(input_bytes)
    root = _root(xml_bytes)

    cadnum = _extract_cadnum(root)
    address = _extract_address(root)
    area = _extract_area(root)
    is_land = _extract_is_land(root)
    coords = _extract_coords(root)
    has_coords = bool(coords)
    capital_objects = _extract_capital_objects(root, cadnum)
    region, municipality, settlement = _extract_admins(root)

    return EGRNData(
        cadnum=cadnum,
        address=address,
        area=area,
        region=region,
        municipality=municipality,
        settlement=settlement,
        coordinates=coords,
        is_land=is_land,
        has_coords=has_coords,
        capital_objects=capital_objects,
    )
