# parsers/egrn_parser.py
"""
Парсер выписок ЕГРН по земельным участкам.

Поддерживает:
- входные данные в виде raw XML (bytes)
- ZIP-архив с XML или XML.GZ внутри

Возвращает объект EGRNData с основными атрибутами:
- cadnum          — кадастровый номер
- address         — адрес (readable_address)
- area            — площадь (строкой, как в XML)
- permitted_use   — вид разрешённого использования по документу (ВРИ)
- is_land         — признак, что объект — земельный участок
- coordinates     — список точек контура
- capital_objects — включённые объекты (кадастровые номера)
- region, municipality, settlement — административное деление
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Iterable, Tuple

import io
import gzip
import zipfile
import os

from lxml import etree


# ------------------------------ МОДЕЛИ ------------------------------ #


@dataclass
class Coord:
    """
    Одна точка контура земельного участка.
    num  – номер точки (ord_nmb)
    x, y – координаты (строкой, как в XML)
    """
    num: Optional[str] = None
    x: Optional[str] = None
    y: Optional[str] = None


@dataclass
class EGRNData:
    """
    Сводная информация из выписки ЕГРН по земельному участку.
    """
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
    permitted_use: Optional[str] = None  # ВРИ по документу


# ------------------------- ВСПОМОГАТЕЛЬНЫЕ ------------------------- #


def _ensure_xml_bytes(input_bytes: bytes) -> bytes:
    """
    Принимает сырые байты:
      - если это ZIP (PK...), достаём из него XML или XML.GZ
        *Игнорируем* файлы, имя которых начинается с proto_ (proto_.xml и т.п.)
      - иначе считаем, что это уже XML.
    """
    if input_bytes[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(input_bytes)) as zf:
            xml_candidates: List[str] = []
            gz_candidates: List[str] = []
            proto_xml: List[str] = []
            proto_gz: List[str] = []

            for info in zf.infolist():
                name_lower = info.filename.lower()
                base = os.path.basename(name_lower)

                if name_lower.endswith(".xml"):
                    if base.startswith("proto_"):
                        proto_xml.append(info.filename)
                    else:
                        xml_candidates.append(info.filename)
                elif name_lower.endswith(".gz"):
                    if base.startswith("proto_"):
                        proto_gz.append(info.filename)
                    else:
                        gz_candidates.append(info.filename)

            # Сначала обычные XML, кроме proto_
            if xml_candidates:
                return zf.read(xml_candidates[0])

            # Потом gzip-XML, кроме proto_
            if gz_candidates:
                gz_data = zf.read(gz_candidates[0])
                return gzip.decompress(gz_data)

            # В крайнем случае — proto_.xml (если ничего больше нет)
            if proto_xml:
                return zf.read(proto_xml[0])

            if proto_gz:
                gz_data = zf.read(proto_gz[0])
                return gzip.decompress(gz_data)

            raise ValueError("В ZIP-архиве не найден XML или XML.GZ")

    # Не ZIP — считаем сразу XML
    return input_bytes


def _root(xml_bytes: bytes) -> etree._Element:
    """
    Разбираем XML в корневой элемент.
    """
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    return etree.fromstring(xml_bytes, parser=parser)


def _collect_texts(root: etree._Element, xpaths: Iterable[str]) -> List[str]:
    """
    Пробегаем по списку XPath-выражений и собираем текст.
    Используем только root.xpath(...), НЕ .find() — чтобы не вызывать
    lxml._elementpath и не ловить invalid predicate.
    """
    result: List[str] = []
    for xp in xpaths:
        nodes = root.xpath(xp)
        for node in nodes:
            if isinstance(node, (etree._ElementUnicodeResult, str)):
                text = str(node).strip()
            else:
                text = (node.text or "").strip()
            if text:
                result.append(text)
    return result


# ----------------------- ВЫТЯГИВАНИЕ ПОЛЕЙ ----------------------- #


def _extract_cadnum(root: etree._Element) -> Optional[str]:
    """
    Кадастровый номер земельного участка.

    Для большинства выписок extract_base_params_land первый cad_number —
    это именно КН участка.
    """
    texts = _collect_texts(root, ["//*[local-name()='cad_number']/text()"])
    return texts[0] if texts else None


def _extract_address(root: etree._Element) -> Optional[str]:
    """
    Адрес / местоположение участка.
    """
    texts = _collect_texts(root, ["//*[local-name()='readable_address']/text()"])
    return texts[0] if texts else None


def _extract_area(root: etree._Element) -> Optional[str]:
    """
    Площадь участка.

    Сначала пробуем params/area/value (уточнённая площадь),
    при её отсутствии берём просто первый area/value.
    """
    texts = _collect_texts(
        root,
        [
            "//*[local-name()='params']/*[local-name()='area']/*[local-name()='value']/text()",
            "//*[local-name()='area']/*[local-name()='value']/text()",
        ],
    )
    if not texts:
        return None
    return texts[0].replace(",", ".").strip()


def _extract_permitted_use(root: etree._Element) -> Optional[str]:
    """
    ВРИ (вид разрешенного использования) по документу.

    Частый вариант:
      land_record/params/permitted_use/permitted_use_established/by_document
    """
    texts = _collect_texts(
        root,
        [
            "//*[local-name()='permitted_use']//*[local-name()='by_document']/text()",
            "//*[local-name()='permitted_use_established']//*[local-name()='by_document']/text()",
        ],
    )
    return texts[0] if texts else None


def _extract_is_land(root: etree._Element) -> bool:
    """
    Пытаемся понять, что объект – именно земельный участок.
    """
    # Для extract_base_params_land — точно ЗУ
    if root.tag.endswith("extract_base_params_land"):
        return True

    # Запасной вариант — наличие land_record
    if root.xpath("//*[local-name()='land_record']"):
        return True

    return False


def _extract_coords(root: etree._Element) -> List[Coord]:
    """
    Координаты контура участка.

    Типичный формат:
      .../ordinates/ordinate/x, y, ord_nmb
    """
    coords: List[Coord] = []

    for el in root.xpath("//*[local-name()='ordinate']"):
        x_list = el.xpath("./*[local-name()='x']/text()")
        y_list = el.xpath("./*[local-name()='y']/text()")
        num_list = el.xpath("./*[local-name()='ord_nmb']/text()")

        x = x_list[0].strip() if x_list else None
        y = y_list[0].strip() if y_list else None
        num = num_list[0].strip() if num_list else None

        if x or y:
            coords.append(Coord(num=num, x=x, y=y))

    return coords


def _extract_capital_objects(root: etree._Element, main_cadnum: Optional[str]) -> List[str]:
    """
    Включённые объекты (кадастровые номера), если есть.

    Пример:
      land_record/cad_links/included_objects/included_object/cad_number
    """
    res: List[str] = []

    texts = _collect_texts(
        root,
        [
            "//*[local-name()='included_objects']//*[local-name()='cad_number']/text()",
        ],
    )
    for t in texts:
        s = t.strip()
        if s and s != (main_cadnum or ""):
            res.append(s)

    return res


def _extract_admins(
    root: etree._Element,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Регион, муниципальное образование, населённый пункт.
    """
    region = None
    municipality = None
    settlement = None

    region_texts = _collect_texts(
        root,
        [
            "//*[local-name()='region']/*[local-name()='value']/text()",
            "//*[local-name()='region']/text()",
        ],
    )
    if region_texts:
        region = region_texts[0]

    mun_texts = _collect_texts(
        root,
        [
            "//*[local-name()='municipality']/*[local-name()='value']/text()",
            "//*[local-name()='municipality']/text()",
        ],
    )
    if mun_texts:
        municipality = mun_texts[0]

    sett_texts = _collect_texts(
        root,
        [
            "//*[local-name()='city']/*[local-name()='name_city']/text()",
            "//*[local-name()='city']/text()",
            "//*[local-name()='locality']/text()",
        ],
    )
    if sett_texts:
        settlement = sett_texts[0]

    return region, municipality, settlement


# --------------------------- ОСНОВНАЯ ФУНКЦИЯ --------------------------- #


def parse_egrn_xml(input_bytes: bytes) -> EGRNData:
    """
    Универсальный вход:
      - raw XML (bytes)
      - ZIP с XML/XML.GZ внутри

    На выходе — EGRNData.
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
    permitted_use = _extract_permitted_use(root)

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
        permitted_use=permitted_use,
    )
