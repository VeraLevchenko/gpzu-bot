from dataclasses import dataclass
from typing import List, Optional
from lxml import etree

@dataclass
class Coord:
    num: Optional[str]  # номер точки (как в XML)
    x: str              # X (десятичная точка; в DOCX отобразим запятой)
    y: str              # Y (десятичная точка; в DOCX отобразим запятой)

@dataclass
class EGRNData:
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None
    coordinates: List[Coord] = None
    is_land: bool = False        # это выписка по ЗУ?
    has_coords: bool = False     # есть ли координаты границ?
    capital_objects: List[str] = None  # КН ОКС внутри границ

# ---------- helpers ----------
def _text(nodes) -> Optional[str]:
    for n in nodes or []:
        t = (n if isinstance(n, str) else getattr(n, "text", None))
        if t and t.strip():
            return t.strip()
    return None

def _norm_num_str(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    return s if s else None

def _norm_coord_number(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return s.strip().replace(" ", "").replace(",", ".")

# ---------- main ----------
def parse_egrn_xml(xml_bytes: bytes) -> EGRNData:
    """
    Извлекаем:
      - КН, адрес, площадь;
      - координаты контура (порядок как в XML), номер ПОСЛЕДНЕЙ точки = номер ПЕРВОЙ;
      - КН ОКС, расположенных в границах ЗУ:
        //land_record/cad_links/included_objects/included_object/cad_number
    """
    root = etree.fromstring(xml_bytes)

    cadnum   = _text(root.xpath(".//land_record/object/common_data/cad_number/text()"))
    area     = _text(root.xpath(".//land_record/params/area/value/text()"))
    address  = _text(root.xpath(".//land_record/address_location/address/readable_address/text()"))
    region   = _text(root.xpath(".//land_record/address_location//address_fias/level_settlement/region/value/text()"))
    mun_name = _text(root.xpath(".//land_record/address_location//address_fias/level_settlement/city/name_city/text()"))

    # Это выписка про ЗУ?
    is_land = bool(root.xpath(".//land_record"))

    # Точки — строго в порядке следования узлов <ordinate>
    ord_nodes = root.xpath(
        ".//land_record/contours_location/contours/contour/"
        "entity_spatial/spatials_elements/spatial_element/ordinates/ordinate"
    )

    coords: List[Coord] = []
    for o in ord_nodes:
        # Координаты из дочерних тегов x/y (или X/Y)
        x = _text(o.xpath("./x/text()")) or _text(o.xpath("./X/text()"))
        y = _text(o.xpath("./y/text()")) or _text(o.xpath("./Y/text()"))
        if not (x and y):
            continue

        # Номер точки — как в XML (строка) по нескольким возможным тегам
        num_txt = (
            _text(o.xpath("./ord_nmb/text()")) or
            _text(o.xpath("./ord_numb/text()")) or
            _text(o.xpath("./num_geopoint/text()")) or
            _text(o.xpath("./NumGeopoint/text()")) or
            _text(o.xpath("./Order/text()"))
        )
        num_txt = _norm_num_str(num_txt)

        coords.append(
            Coord(
                num=num_txt,
                x=_norm_coord_number(x) or "",
                y=_norm_coord_number(y) or ""
            )
        )

    # Номер последней точки должен быть таким же, как у первой (координаты не меняем)
    if coords:
        first_num = (coords[0].num or "").strip()
        coords[-1].num = first_num

    has_coords = bool(coords)

    # ОКС в границах ЗУ (может быть несколько)
    capital_objects: List[str] = []
    for n in root.xpath(".//land_record/cad_links/included_objects/included_object/cad_number/text()"):
        n = (n or "").strip()
        if n:
            capital_objects.append(n)

    return EGRNData(
        cadnum=cadnum,
        address=address,
        area=area,
        region=region,
        municipality=mun_name,
        settlement=None,
        coordinates=coords,
        is_land=is_land,
        has_coords=has_coords,
        capital_objects=capital_objects,
    )
