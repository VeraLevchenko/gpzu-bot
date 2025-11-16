# parsers/egrn_parser.py
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
import zipfile
import gzip

from lxml import etree


@dataclass
class Coord:
    """
    Одна точка контура ЗУ из ЕГРН.
    num – номер точки (ord_nmb / num_geopoint / индекс)
    x, y – координаты в метрической системе (как есть из XML, в строковом виде).
    """
    num: str
    x: str
    y: str


@dataclass
class EGRNData:
    """
    Минимально необходимый набор данных из выписки ЕГРН.
    Используется ботом для ТУ, MID/MIF, ГПЗУ и др.
    """
    cadnum: Optional[str]
    address: Optional[str]
    area: Optional[str]
    region: Optional[str]
    municipality: Optional[str]
    settlement: Optional[str]
    coordinates: List[Coord]
    is_land: bool
    has_coords: bool
    capital_objects: List[str]
    permitted_use: Optional[str] = None


# ----------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------------- #


def _extract_xml_bytes(raw: bytes) -> bytes:
    """
    Принимает «как есть» bytes из файла (XML / ZIP / GZ) и возвращает чистый XML.
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
            # Берём первый XML, который не начинается с "proto_"
            xml_names = [
                name for name in zf.namelist()
                if name.lower().endswith(".xml") and not name.lower().startswith("proto_")
            ]
            if not xml_names:
                raise ValueError("В ZIP-архиве не найден подходящий XML (кроме proto_.xml).")
            with zf.open(xml_names[0], "r") as xf:
                return xf.read()

    return data


def _parse_root(xml_bytes: bytes) -> etree._Element:
    """
    Разбирает XML в корневой элемент lxml.etree.
    """
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
    # Кадастровый номер ЗУ
    el = _xpath_first(root, "//*[local-name()='cad_number'][1]")
    return _text_or_none(el)


def _extract_area(root: etree._Element) -> Optional[str]:
    """
    Площадь участка – берём из <area><value>.
    """
    el = _xpath_first(root, "//*[local-name()='area']/*[local-name()='value'][1]")
    return _text_or_none(el)


def _extract_address(root: etree._Element) -> Optional[str]:
    """
    Адрес: приоритет readable_address, иначе любой address_location/address.
    """
    el = _xpath_first(root, "//*[local-name()='readable_address'][1]")
    if el is not None:
        return _text_or_none(el)

    el = _xpath_first(root, "//*[local-name()='address_location']/*[local-name()='address'][1]")
    if el is not None:
        return _text_or_none(el)

    el = _xpath_first(root, "//*[local-name()='address'][1]")
    return _text_or_none(el)


def _extract_admins(root: etree._Element) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Регион / муниципалитет / населённый пункт (по возможности).
    Для текущих задач достаточно хотя бы региона и города.
    """
    region = None
    municipality = None
    settlement = None

    # Регион
    el_region = _xpath_first(root, "//*[local-name()='region']/*[local-name()='value'][1]")
    region = _text_or_none(el_region)

    # Муниципалитет / городской округ — часто через city
    el_city = _xpath_first(root, "//*[local-name()='name_city'][1]")
    municipality = _text_or_none(el_city)

    # Населённый пункт
    el_settlement = _xpath_first(root, "//*[local-name()='name_settlement'][1]")
    settlement = _text_or_none(el_settlement)

    return region, municipality, settlement


def _extract_permitted_use(root: etree._Element) -> Optional[str]:
    """
    Вид разрешенного использования:
    - util_by_doc/value
    - permitted_use/value
    - permitted_utilization/value
    """
    paths = [
        "//*[local-name()='util_by_doc']/*[local-name()='value'][1]",
        "//*[local-name()='permitted_use']/*[local-name()='value'][1]",
        "//*[local-name()='permitted_utilization']/*[local-name()='value'][1]",
    ]
    for p in paths:
        el = _xpath_first(root, p)
        txt = _text_or_none(el)
        if txt:
            return txt
    return None


def _extract_capital_objects(root: etree._Element) -> List[str]:
    """
    Привязанные объекты капитального строительства (если есть).
    Пока собираем только как список строк (кадастровые номера ОКС).
    """
    res: List[str] = []
    for el in root.xpath("//*[local-name()='object_realty']//*[local-name()='cad_number']"):
        txt = _text_or_none(el)
        if txt:
            res.append(txt)
    return res


def _extract_coords_from_contours_location(root: etree._Element) -> List[Coord]:
    """
    КООРДИНАТЫ ТОЛЬКО ИЗ <contours_location>.

    Структура:
      <contours_location>
        <contours>
          <contour>
            <entity_spatial>
              <sk_id>...</sk_id>
              <spatials_elements>
                <spatial_element>
                  <ordinates>
                    <ordinate>
                      <x>...</x>
                      <y>...</y>
                      <ord_nmb>1</ord_nmb>
                      <num_geopoint>1</num_geopoint>
                    </ordinate>
                    ...
    """
    ordinates = root.xpath(
        "//*[local-name()='contours_location']"
        "/*[local-name()='contours']"
        "/*[local-name()='contour']"
        "/*[local-name()='entity_spatial']"
        "/*[local-name()='spatials_elements']"
        "/*[local-name()='spatial_element']"
        "/*[local-name()='ordinates']"
        "/*[local-name()='ordinate']"
    )

    coords: List[Coord] = []

    for idx, ord_el in enumerate(ordinates, start=1):
        x_el = _xpath_first(ord_el, "*[local-name()='x']")
        y_el = _xpath_first(ord_el, "*[local-name()='y']")
        num_el = _xpath_first(ord_el, "*[local-name()='ord_nmb']")

        x = _text_or_none(x_el)
        y = _text_or_none(y_el)
        num = _text_or_none(num_el)

        # если ord_nmb нет, пробуем num_geopoint
        if not num:
            num_el2 = _xpath_first(ord_el, "*[local-name()='num_geopoint']")
            num = _text_or_none(num_el2)

        if not x or not y:
            continue

        if not num:
            num = str(idx)

        coords.append(Coord(num=num, x=x, y=y))

    return coords


def _detect_is_land(root: etree._Element) -> bool:
    """
    Пытаемся понять, что это выписка по земельному участку.
    Смотрим:
      - имя корневого элемента
      - наличие land_record
    """
    tag = etree.QName(root.tag).localname.lower()
    if "land" in tag:
        return True

    if root.xpath("//*[local-name()='land_record']"):
        return True

    return False


# ----------------------------- ПУБЛИЧНАЯ ФУНКЦИЯ ----------------------------- #


def parse_egrn_xml(raw: bytes) -> EGRNData:
    """
    Главная функция парсинга ЕГРН.

    На вход:
      - bytes XML-файла
      - bytes ZIP с XML (игнорируем proto_.xml)
      - bytes GZ с XML

    На выход:
      - EGRNData с заполненными основными полями
      - coordinates берутся ТОЛЬКО из <contours_location>.
    """
    xml_bytes = _extract_xml_bytes(raw)
    root = _parse_root(xml_bytes)

    cadnum = _extract_cadnum(root)
    area = _extract_area(root)
    address = _extract_address(root)
    region, municipality, settlement = _extract_admins(root)
    permitted_use = _extract_permitted_use(root)
    capital_objects = _extract_capital_objects(root)

    coords = _extract_coords_from_contours_location(root)
    has_coords = bool(coords)
    is_land = _detect_is_land(root)

    data = EGRNData(
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
        permitted_use=permitted_use,
    )

    return data
