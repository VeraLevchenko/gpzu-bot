# parsers/tab_parser.py
"""
Парсер TAB-файлов (MapInfo) для чтения пространственных слоёв.

Этот модуль использует GeoPandas для чтения TAB-файлов и извлечения
геометрии и атрибутивной информации.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import geopandas as gpd
from shapely.geometry import Point, Polygon, MultiPolygon, shape
from shapely.geometry.base import BaseGeometry

from core.layers_config import FieldMapping

logger = logging.getLogger("gpzu-bot.tab_parser")


# ======================= БАЗОВЫЕ ФУНКЦИИ ЧТЕНИЯ ======================= #

def read_tab_file(tab_path: Path | str) -> Optional[gpd.GeoDataFrame]:
    """
    Прочитать TAB-файл и вернуть GeoDataFrame.
    
    Args:
        tab_path: Путь к TAB-файлу
    
    Returns:
        GeoDataFrame или None при ошибке
    """
    try:
        tab_path = Path(tab_path)
        
        if not tab_path.exists():
            logger.error(f"TAB-файл не найден: {tab_path}")
            return None
        
        # Читаем через GeoPandas (использует GDAL/OGR для TAB)
        gdf = gpd.read_file(tab_path, driver="MapInfo File")
        
        logger.info(
            f"TAB-файл прочитан: {tab_path.name}, "
            f"записей: {len(gdf)}, "
            f"полей: {len(gdf.columns)}"
        )
        
        return gdf
        
    except Exception as ex:
        logger.exception(f"Ошибка чтения TAB-файла {tab_path}: {ex}")
        return None


def check_geometry_intersects(
    parcel_geometry: BaseGeometry,
    layer_geometry: BaseGeometry
) -> bool:
    """
    Проверить пересечение геометрий.
    
    Args:
        parcel_geometry: Геометрия участка
        layer_geometry: Геометрия объекта из слоя
    
    Returns:
        True если пересекаются
    """
    try:
        return parcel_geometry.intersects(layer_geometry)
    except Exception as ex:
        logger.warning(f"Ошибка проверки пересечения: {ex}")
        return False


def get_field_value(row, field_variants: List[str]) -> Optional[str]:
    """
    Получить значение поля из строки GeoDataFrame по списку возможных названий.
    
    Args:
        row: Строка GeoDataFrame (Series)
        field_variants: Список возможных названий поля
    
    Returns:
        Значение поля или None
    """
    for variant in field_variants:
        # Проверяем с учётом и без учёта регистра
        if variant in row.index:
            val = row[variant]
            return str(val).strip() if val is not None else None
        
        # Попробуем найти без учёта регистра
        for col in row.index:
            if col.upper() == variant.upper():
                val = row[col]
                return str(val).strip() if val is not None else None
    
    return None


# ======================= ПАРСИНГ ТЕРРИТОРИАЛЬНЫХ ЗОН ======================= #

def parse_zones_layer(tab_path: Path | str) -> List[Dict[str, Any]]:
    """
    Парсинг слоя территориальных зон.
    
    Args:
        tab_path: Путь к TAB-файлу с зонами
    
    Returns:
        Список зон: [{"name": "...", "code": "...", "geometry": Polygon}, ...]
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    zones = []
    
    for idx, row in gdf.iterrows():
        # Название зоны: сначала пробуем Код_объекта, потом Наименование_объекта
        zone_name = get_field_value(row, ["Код_объекта", "Наименование_объекта", "NAME"])
        
        # Код зоны: Индекс_зоны
        zone_code = get_field_value(row, ["Индекс_зоны", "Код_Индекс_зоны", "CODE"])
        
        geometry = row.get('geometry')
        
        if geometry and not geometry.is_empty:
            zones.append({
                "name": zone_name or "",
                "code": zone_code or "",
                "geometry": geometry,
            })
    
    logger.info(f"Загружено зон из {Path(tab_path).name}: {len(zones)}")
    return zones


def find_zone_for_parcel(
    parcel_coords: List[Tuple[float, float]],
    zones: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Определить, в какой зоне находится участок.
    
    Алгоритм:
    1. Проверяем, содержит ли какая-либо зона центроид участка → возвращаем эту зону
    2. Если нет, ищем зону с максимальной площадью пересечения
    
    Args:
        parcel_coords: Координаты участка [(x, y), ...]
        zones: Список зон из parse_zones_layer()
    
    Returns:
        {"name": "...", "code": "..."} или None
    """
    if not parcel_coords or not zones:
        return None
    
    try:
        # Создаём полигон участка
        parcel_poly = Polygon(parcel_coords)
        if not parcel_poly.is_valid:
            parcel_poly = parcel_poly.buffer(0)
        
        centroid = parcel_poly.centroid
        
        # Шаг 1: Ищем зону, содержащую центроид
        for zone in zones:
            zone_geom = zone.get("geometry")
            if zone_geom and zone_geom.contains(centroid):
                logger.info(
                    f"Участок находится в зоне {zone.get('code')} "
                    f"{zone.get('name')} (по центроиду)"
                )
                return {
                    "name": zone.get("name", ""),
                    "code": zone.get("code", ""),
                }
        
        # Шаг 2: Ищем максимальное пересечение
        max_area = 0.0
        best_zone = None
        
        for zone in zones:
            zone_geom = zone.get("geometry")
            if not zone_geom:
                continue
            
            try:
                intersection = parcel_poly.intersection(zone_geom)
                area = intersection.area
                
                if area > max_area:
                    max_area = area
                    best_zone = zone
            except Exception as ex:
                logger.warning(f"Ошибка пересечения с зоной: {ex}")
                continue
        
        if best_zone and max_area > 0:
            logger.info(
                f"Участок находится в зоне {best_zone.get('code')} "
                f"{best_zone.get('name')} (по пересечению, {max_area:.2f} кв.м)"
            )
            return {
                "name": best_zone.get("name", ""),
                "code": best_zone.get("code", ""),
            }
        
        logger.warning("Не удалось определить зону для участка")
        return None
        
    except Exception as ex:
        logger.exception(f"Ошибка определения зоны: {ex}")
        return None


# ======================= ПАРСИНГ ОБЪЕКТОВ КАПСТРОИТЕЛЬСТВА ======================= #

def parse_capital_objects_layer(tab_path: Path | str) -> List[Dict[str, Any]]:
    """
    Парсинг слоя объектов капитального строительства.
    
    Args:
        tab_path: Путь к TAB-файлу
    
    Returns:
        Список объектов с геометрией и атрибутами
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    objects = []
    
    for idx, row in gdf.iterrows():
        obj = {
            "cadnum": get_field_value(row, FieldMapping.OBJECT_CADNUM_FIELDS),
            "object_type": get_field_value(row, FieldMapping.OBJECT_TYPE_FIELDS),
            "purpose": get_field_value(row, FieldMapping.OBJECT_PURPOSE_FIELDS),
            "area": get_field_value(row, FieldMapping.OBJECT_AREA_FIELDS),
            "floors": get_field_value(row, FieldMapping.OBJECT_FLOORS_FIELDS),
            "geometry": row.get('geometry'),
        }
        objects.append(obj)
    
    logger.info(f"Загружено объектов из {Path(tab_path).name}: {len(objects)}")
    return objects


def find_objects_on_parcel(
    parcel_coords: List[Tuple[float, float]],
    objects: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """
    Найти объекты капстроительства, расположенные на участке.
    
    Args:
        parcel_coords: Координаты участка
        objects: Список объектов из parse_capital_objects_layer()
    
    Returns:
        Список объектов на участке
    """
    if not parcel_coords or not objects:
        return []
    
    try:
        parcel_poly = Polygon(parcel_coords)
        if not parcel_poly.is_valid:
            parcel_poly = parcel_poly.buffer(0)
        
        found = []
        
        for obj in objects:
            obj_geom = obj.get("geometry")
            if not obj_geom:
                continue
            
            # Проверяем пересечение
            if check_geometry_intersects(parcel_poly, obj_geom):
                found.append({
                    "cadnum": obj.get("cadnum", ""),
                    "object_type": obj.get("object_type", ""),
                    "purpose": obj.get("purpose", ""),
                    "area": obj.get("area", ""),
                    "floors": obj.get("floors", ""),
                })
        
        logger.info(f"Найдено объектов на участке: {len(found)}")
        return found
        
    except Exception as ex:
        logger.exception(f"Ошибка поиска объектов: {ex}")
        return []


# ======================= ПАРСИНГ ПРОЕКТОВ ПЛАНИРОВКИ ======================= #

def parse_planning_projects_layer(tab_path: Path | str) -> List[Dict[str, Any]]:
    """
    Парсинг слоя проектов планировки территории.
    
    Args:
        tab_path: Путь к TAB-файлу
    
    Returns:
        Список проектов планировки с геометрией
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    projects = []
    
    for idx, row in gdf.iterrows():
        project = {
            "project_name": get_field_value(row, FieldMapping.PROJECT_NAME_FIELDS),
            "decision_number": get_field_value(row, FieldMapping.DECISION_NUMBER_FIELDS),
            "decision_date": get_field_value(row, FieldMapping.DECISION_DATE_FIELDS),
            "decision_authority": get_field_value(row, FieldMapping.DECISION_AUTHORITY_FIELDS),
            "geometry": row.get('geometry'),
        }
        projects.append(project)
    
    logger.info(f"Загружено проектов планировки из {Path(tab_path).name}: {len(projects)}")
    return projects


def check_planning_project_intersection(
    parcel_coords: List[Tuple[float, float]],
    projects: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Проверить, попадает ли участок в границы проекта планировки.
    
    Args:
        parcel_coords: Координаты участка
        projects: Список проектов из parse_planning_projects_layer()
    
    Returns:
        Информация о проекте планировки или None
    """
    if not parcel_coords or not projects:
        return None
    
    try:
        parcel_poly = Polygon(parcel_coords)
        if not parcel_poly.is_valid:
            parcel_poly = parcel_poly.buffer(0)
        
        for project in projects:
            proj_geom = project.get("geometry")
            if not proj_geom:
                continue
            
            if check_geometry_intersects(parcel_poly, proj_geom):
                logger.info(
                    f"Участок входит в проект планировки: "
                    f"{project.get('project_name')}"
                )
                return {
                    "project_name": project.get("project_name", ""),
                    "decision_number": project.get("decision_number", ""),
                    "decision_date": project.get("decision_date", ""),
                    "decision_authority": project.get("decision_authority", ""),
                }
        
        logger.info("Участок не входит в границы проектов планировки")
        return None
        
    except Exception as ex:
        logger.exception(f"Ошибка проверки ППТ: {ex}")
        return None


# ======================= ПАРСИНГ ОГРАНИЧЕНИЙ (ЗОУИТ, АГО, КРТ) ======================= #

def parse_restrictions_layer(
    tab_path: Path | str,
    restriction_type: str
) -> List[Dict[str, Any]]:
    """
    Парсинг слоя с ограничениями (ЗОУИТ, АГО, КРТ и т.д.).
    
    Args:
        tab_path: Путь к TAB-файлу
        restriction_type: Тип ограничения (для логирования)
    
    Returns:
        Список зон ограничений с геометрией
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    restrictions = []
    
    for idx, row in gdf.iterrows():
        restr = {
            "zone_type": restriction_type,
            "name": get_field_value(row, FieldMapping.RESTRICTION_NAME_FIELDS),
            "decision_number": get_field_value(row, FieldMapping.DECISION_NUMBER_FIELDS),
            "decision_date": get_field_value(row, FieldMapping.DECISION_DATE_FIELDS),
            "decision_authority": get_field_value(row, FieldMapping.DECISION_AUTHORITY_FIELDS),
            "geometry": row.get('geometry'),
        }
        restrictions.append(restr)
    
    logger.info(
        f"Загружено ограничений ({restriction_type}) "
        f"из {Path(tab_path).name}: {len(restrictions)}"
    )
    return restrictions


def find_restrictions_for_parcel(
    parcel_coords: List[Tuple[float, float]],
    restrictions: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """
    Найти ограничения, которые пересекаются с участком.
    
    Args:
        parcel_coords: Координаты участка
        restrictions: Список ограничений из parse_restrictions_layer()
    
    Returns:
        Список пересекающихся ограничений
    """
    if not parcel_coords or not restrictions:
        return []
    
    try:
        parcel_poly = Polygon(parcel_coords)
        if not parcel_poly.is_valid:
            parcel_poly = parcel_poly.buffer(0)
        
        found = []
        
        for restr in restrictions:
            restr_geom = restr.get("geometry")
            if not restr_geom:
                continue
            
            if check_geometry_intersects(parcel_poly, restr_geom):
                found.append({
                    "zone_type": restr.get("zone_type", ""),
                    "name": restr.get("name", ""),
                    "registry_number": restr.get("registry_number", ""),
                    "decision_number": restr.get("decision_number", ""),
                    "decision_date": restr.get("decision_date", ""),
                    "decision_authority": restr.get("decision_authority", ""),
                })
        
        if found:
            logger.info(
                f"Найдено ограничений типа {restrictions[0].get('zone_type', '?')}: "
                f"{len(found)}"
            )
        
        return found
        
    except Exception as ex:
        logger.exception(f"Ошибка поиска ограничений: {ex}")
        return []
# Добавляем в конец файла функцию для ЗОУИТ с реестровым номером
def parse_zouit_layer_extended(tab_path, restriction_type: str) -> List[Dict[str, Any]]:
    """
    Парсинг ЗОУИТ с извлечением реестрового номера.
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    restrictions = []
    
    for idx, row in gdf.iterrows():
        # Извлекаем реестровый номер
        registry_number = get_field_value(row, [
            "Реестровый_номер_границы",
            "REGISTRY_NUMBER",
            "Реестровый_номер"
        ])
        
        restr = {
            "zone_type": restriction_type,
            "name": get_field_value(row, [
                "Вид_или_наименование_по_доку_8",
                "Наименование",
                "Полное_наименование"
            ]),
            "registry_number": registry_number,
            "decision_number": get_field_value(row, ["Номер"]),
            "decision_date": get_field_value(row, ["Дата_регистрации"]),
            "decision_authority": None,
            "geometry": row.get('geometry'),
        }
        restrictions.append(restr)
    
    logger.info(f"Загружено ЗОУИТ из {Path(tab_path).name}: {len(restrictions)}")
    return restrictions
