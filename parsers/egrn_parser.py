# parsers/egrn_parser.py
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Optional, Tuple
import zipfile
import gzip

from lxml import etree


# ----------------------------- МОДЕЛИ ----------------------------- #

@dataclass
class Coord:
    """
    Одна точка контура ЗУ из ЕГРН.
    num – номер точки (ord_nmb / индекс),
    x, y – координаты, как есть из XML (строки).
    """
    num: str
    x: str
    y: str


@dataclass
class EGRNData:
    """
    Универсальная модель данных выписки ЕГРН.

    Все поля имеют значения по умолчанию, чтобы можно было:
      - создавать объект после парсинга XML (parse_egrn_xml),
      - создавать вручную в других местах (например, в flow по ТУ)
        только с частью полей:

        EGRNData(cadnum="42:30:...", address="...", area="...", permitted_use="...")
    """
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None

    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None

    # Плоский список всех точек
    coordinates: List[Coord] = field(default_factory=list)

    # Список контуров (каждый контур — список Coord)
    contours: List[List[Coord]] = field(default_factory=list)

    is_land: bool = False
    has_coords: bool = False

    capital_objects: List[str] = field(default_factory=list)

    # ВРИ / вид разрешённого использования
    permitted_use: Optional[str] = None


# ----------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------------- #

def _extract_xml_bytes(raw: bytes) -> bytes:
    """
    Принимает bytes исходного файла (XML / ZIP / GZ) и возвращает bytes XML.

    - Если это ZIP, берём первый подходящий XML (кроме proto_.xml).
    - Если это GZIP, распаковываем.
    - Иначе считаем, что это обычный XML.
    """
    data = raw

    # GZIP?
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        data = gzip.decompress(data)

    # ZIP?
    bio = BytesIO(data)
    if zipfile.is_zipfile(bio):
        with zipfile.ZipFile(bio, "r") as zf:
            xml_names = [
                name
                for name in zf.namelist()
                if name.lower().endswith(".xml")
                and not name.lower().startswith("proto_")
            ]
            if not xml_names:
                raise ValueError(
                    "В ZIP-архиве не найден подходящий XML (кроме proto_.xml)."
                )
            with zf.open(xml_names[0], "r") as xf:
                return xf.read()

    return data


def _parse_root(xml_bytes: bytes) -> etree._Element:
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    return etree.fromstring(xml_bytes, parser=parser)


def _text_or_none(elem: Optional[etree._Element]) -> Optional[str]:
    if elem is None:
        return None
    txt = "".join(elem.itertext()).strip()
    return txt or None


def _xpath_first(root: etree._Element, xpath: str) -> Optional[etree._Element]:
    res = root.xpath(xpath)
    if not res:
        return None
    return res[0]


# ----------------------- ИЗВЛЕЧЕНИЕ ПОЛЕЙ ----------------------- #

def _extract_cadnum(root: etree._Element) -> Optional[str]:
    el = _xpath_first(root, "//*[local-name()='cad_number'][1]")
    if el is None:
        el = _xpath_first(root, "//*[local-name()='cadnum'][1]")
    return _text_or_none(el)


def _extract_area(root: etree._Element) -> Optional[str]:
    el = _xpath_first(root, "//*[local-name()='area']/*[local-name()='value'][1]")
    return _text_or_none(el)


def _extract_address(root: etree._Element) -> Optional[str]:
    # 1) читаемый адрес, если есть
    el = _xpath_first(root, "//*[local-name()='readable_address'][1]")
    if el is not None:
        txt = _text_or_none(el)
        if txt:
            return txt

    # 2) address_location/address
    el = _xpath_first(
        root,
        "//*[local-name()='address_location']/*[local-name()='address'][1]",
    )
    if el is not None:
        txt = _text_or_none(el)
        if txt:
            return txt

    # 3) первый попавшийся address
    el = _xpath_first(root, "//*[local-name()='address'][1]")
    return _text_or_none(el)


def _extract_admins(root: etree._Element) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Регион, муниципальное образование, населённый пункт (если есть).
    """
    region = None
    municipality = None
    settlement = None

    el_region = _xpath_first(root, "//*[local-name()='region']/*[local-name()='value'][1]")
    region = _text_or_none(el_region)

    el_city = _xpath_first(root, "//*[local-name()='name_city'][1]")
    municipality = _text_or_none(el_city)

    el_settlement = _xpath_first(root, "//*[local-name()='name_settlement'][1]")
    settlement = _text_or_none(el_settlement)

    return region, municipality, settlement


def _extract_permitted_use(root: etree._Element) -> Optional[str]:
    """
    ВРИ / вид разрешённого использования.

    Поддерживаем несколько вариантов структуры, в т.ч.:

      <permitted_use>
        <permitted_use_established>
          <by_document>...</by_document>
        </permitted_use_established>
      </permitted_use>
    """
    paths = [
        # как в твоих выписках
        "//*[local-name()='permitted_use']"
        "/*[local-name()='permitted_use_established']"
        "/*[local-name()='by_document'][1]",

        # более общий случай
        "//*[local-name()='permitted_use']/*[local-name()='by_document'][1]",
        "//*[local-name()='permitted_use']/*[local-name()='value'][1]",
        "//*[local-name()='permitted_utilization']/*[local-name()='value'][1]",
        "//*[local-name()='util_by_doc']/*[local-name()='value'][1]",
    ]
    for p in paths:
        el = _xpath_first(root, p)
        txt = _text_or_none(el)
        if txt:
            return txt
    return None


def _extract_capital_objects(root: etree._Element) -> List[str]:
    """
    Список кадастровых номеров объектов капитального строительства в границах ЗУ (если есть).
    """
    res: List[str] = []
    for el in root.xpath("//*[local-name()='object_realty']//*[local-name()='cad_number']"):
        txt = _text_or_none(el)
        if txt:
            res.append(txt)
    return res


def _extract_contours_from_contours_location(root: etree._Element) -> List[List[Coord]]:
    """
    Извлекает координаты ТОЛЬКО из <contours_location>, с сохранением структуры
    контуров и порядка точек.

    Ожидаем структуру:

      <contours_location>
        <contours>
          <contour>
            <entity_spatial>
              <spatials_elements>
                <spatial_element>
                  <ordinates>
                    <ordinate>
                      <x>...</x>
                      <y>...</y>
                      <ord_nmb>1</ord_nmb>
                    </ordinate>
                    ...
    """
    contours_result: List[List[Coord]] = []

    contour_elements = root.xpath(
        "//*[local-name()='contours_location']"
        "/*[local-name()='contours']"
        "/*[local-name()='contour']"
    )

    for cont_el in contour_elements:
        spatial_elements = cont_el.xpath(".//*[local-name()='spatial_element']")
        if not spatial_elements:
            continue

        for se in spatial_elements:
            ordinates = se.xpath(".//*[local-name()='ordinate']")
            contour_coords: List[Coord] = []

            for idx, ord_el in enumerate(ordinates, start=1):
                x_nodes = ord_el.xpath("*[local-name()='x']")
                y_nodes = ord_el.xpath("*[local-name()='y']")
                num_nodes = ord_el.xpath("*[local-name()='ord_nmb']")

                x = _text_or_none(x_nodes[0]) if x_nodes else None
                y = _text_or_none(y_nodes[0]) if y_nodes else None
                num = _text_or_none(num_nodes[0]) if num_nodes else None

                if not x or not y:
                    continue

                if not num:
                    num = str(idx)

                contour_coords.append(Coord(num=num, x=x, y=y))

            if contour_coords:
                contours_result.append(contour_coords)

    return contours_result


def _detect_is_land(root: etree._Element) -> bool:
    """
    Пытаемся понять, что это именно земельный участок.
    """
    tag = etree.QName(root.tag).localname.lower()
    if "land" in tag:
        return True
    if root.xpath("//*[local-name()='land_record']"):
        return True
    return False


# ----------------------------- ГЛАВНАЯ ФУНКЦИЯ ----------------------------- #

def parse_egrn_xml(raw: bytes) -> EGRNData:
    """
    Главная функция парсинга ЕГРН.

    Умеет работать с:
      - XML-файлом выписки,
      - ZIP, содержащим XML (кроме proto_.xml),
      - GZ-файлом.

    Координаты:
      - берём только из <contours_location>,
      - contours: список контуров,
      - coordinates: плоский список всех точек во всех контурах.
    """
    xml_bytes = _extract_xml_bytes(raw)
    root = _parse_root(xml_bytes)

    cadnum = _extract_cadnum(root)
    area = _extract_area(root)
    address = _extract_address(root)
    region, municipality, settlement = _extract_admins(root)
    permitted_use = _extract_permitted_use(root)
    capital_objects = _extract_capital_objects(root)

    contours = _extract_contours_from_contours_location(root)
    coordinates = [pt for contour in contours for pt in contour]

    has_coords = bool(coordinates)
    is_land = _detect_is_land(root)

    return EGRNData(
        cadnum=cadnum,
        address=address,
        area=area,
        region=region,
        municipality=municipality,
        settlement=settlement,
        coordinates=coordinates,
        contours=contours,
        is_land=is_land,
        has_coords=has_coords,
        capital_objects=capital_objects,
        permitted_use=permitted_use,
    )
