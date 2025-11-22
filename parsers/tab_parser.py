# parsers/tab_parser.py
"""
Парсер TAB/MIF файлов MapInfo для пространственных слоёв.
Содержит функции для чтения и анализа геопространственных данных.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import geopandas as gpd
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely import wkt
import pandas as pd

logger = logging.getLogger("gpzu-bot.tab_parser")


def read_tab_file(tab_path: Path | str) -> Optional[gpd.GeoDataFrame]:
    """
    Читает TAB-файл и возвращает GeoDataFrame.
    
    Args:
        tab_path: Путь к TAB-файлу
    
    Returns:
        GeoDataFrame или None при ошибке
    """
    try:
        gdf = gpd.read_file(tab_path, driver="MapInfo File")
        logger.debug(f"Прочитан TAB-файл: {Path(tab_path).name}, записей: {len(gdf)}")
        return gdf
    except Exception as ex:
        logger.error(f"Ошибка чтения TAB-файла {tab_path}: {ex}")
        return None


def get_field_value(row: pd.Series, field_names: List[str]) -> Optional[str]:
    """
    Получить значение поля из строки, пробуя разные варианты названий.
    
    Args:
        row: Строка DataFrame
        field_names: Список возможных названий поля
    
    Returns:
        Значение поля или None
    """
    for field in field_names:
        if field in row.index:
            val = row[field]
            if pd.notna(val) and str(val).strip():
                return str(val).strip()
    return None


def parse_zones_layer(tab_path: Path | str) -> List[Dict[str, Any]]:
    """
    Парсинг слоя территориальных зон.
    
    Args:
        tab_path: Путь к TAB-файлу с зонами
    
    Returns:
        Список зон с геометрией и атрибутами
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    zones = []
    
    # Возможные названия полей для кода и наименования зоны
    CODE_FIELDS = ["Индекс_зоны", "CODE", "ZONE_CODE", "Код", "КОД", "Обозначение", "ОБОЗНАЧЕНИЕ"]
    NAME_FIELDS = ["Код_объекта", "NAME", "ZONE_NAME", "Наименование", "НАИМЕНОВАНИЕ", "Название", "НАЗВАНИЕ"]
    
    for idx, row in gdf.iterrows():
        zone = {
            "code": get_field_value(row, CODE_FIELDS),
            "name": get_field_value(row, NAME_FIELDS),
            "geometry": row.get('geometry'),
        }
        zones.append(zone)
    
    logger.info(f"Загружено зон из {Path(tab_path).name}: {len(zones)}")
    return zones


def find_zone_for_parcel(coords: List[Tuple[float, float]], zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Определить территориальную зону для участка по координатам.
    
    Логика:
    1. Находим ВСЕ зоны, которые пересекаются с участком
    2. Вычисляем процент перекрытия для каждой зоны
    3. Выбираем зону с МАКСИМАЛЬНЫМ перекрытием
    4. Возвращаем выбранную зону + информацию о всех пересечениях
    
    Args:
        coords: Координаты участка в формате [(y, x), ...]
        zones: Список зон с геометрией
    
    Returns:
        Словарь с информацией о зоне:
        {
            "code": код зоны,
            "name": название зоны,
            "multiple_zones": True/False,
            "all_zones": список всех пересечений,
            "overlap_percent": процент перекрытия
        }
    """
    if not coords or len(coords) < 3:
        logger.warning("Недостаточно координат для построения полигона")
        return None
    
    try:
        parcel_polygon = Polygon(coords)
        
        # Собираем все пересекающиеся зоны с процентом перекрытия
        intersecting_zones = []
        
        for zone in zones:
            zone_geom = zone.get('geometry')
            if zone_geom is None:
                continue
            
            # Проверяем пересечение
            if parcel_polygon.intersects(zone_geom):
                intersection = parcel_polygon.intersection(zone_geom)
                overlap_percent = (intersection.area / parcel_polygon.area) * 100
                
                intersecting_zones.append({
                    "code": zone.get('code'),
                    "name": zone.get('name'),
                    "overlap_percent": overlap_percent,
                    "overlap_area": intersection.area,
                })
                
                logger.debug(
                    f"Зона {zone.get('code')} {zone.get('name')}: "
                    f"перекрытие {overlap_percent:.1f}%"
                )
        
        if not intersecting_zones:
            logger.warning("Территориальная зона не найдена для участка")
            return None
        
        # Сортируем по проценту перекрытия (по убыванию)
        intersecting_zones.sort(key=lambda z: z['overlap_percent'], reverse=True)
        
        # Берём зону с максимальным перекрытием
        best_zone = intersecting_zones[0]
        
        # Определяем, попадает ли участок в несколько зон
        # Флаг устанавливается только если:
        # 1. Зон больше одной И
        # 2. Основная зона покрывает менее 100% участка
        multiple_zones = len(intersecting_zones) > 1 and best_zone['overlap_percent'] < 99.9
        
        if multiple_zones:
            # Логируем, что нашли несколько зон
            zones_info = ", ".join([
                f"{z['code']} ({z['overlap_percent']:.1f}%)" 
                for z in intersecting_zones
            ])
            logger.warning(
                f"⚠️ УЧАСТОК ПЕРЕСЕКАЕТСЯ С НЕСКОЛЬКИМИ ЗОНАМИ: {zones_info}. "
                f"Выбрана зона с максимальным перекрытием: {best_zone['code']} "
                f"({best_zone['overlap_percent']:.1f}%)"
            )
        else:
            logger.info(
                f"Зона определена: {best_zone['code']} {best_zone['name']} "
                f"(перекрытие {best_zone['overlap_percent']:.1f}%)"
            )
        
        return {
            "code": best_zone.get('code'),
            "name": best_zone.get('name'),
            "multiple_zones": multiple_zones,
            "all_zones": intersecting_zones,
            "overlap_percent": best_zone.get('overlap_percent'),
        }
        
    except Exception as ex:
        logger.error(f"Ошибка при определении зоны: {ex}")
        return None


def parse_capital_objects_layer(tab_path: Path | str) -> List[Dict[str, Any]]:
    """
    Парсинг слоя объектов капитального строительства.
    
    Args:
        tab_path: Путь к TAB-файлу
    
    Returns:
        Список объектов с геометрией
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    objects = []
    
    CADNUM_FIELDS = ["CADNUM", "CAD_NUM", "КадастровыйНомер", "Кадастровый_номер"]
    TYPE_FIELDS = ["TYPE", "OBJECT_TYPE", "Тип", "ТИП"]
    PURPOSE_FIELDS = ["PURPOSE", "Назначение", "НАЗНАЧЕНИЕ"]
    AREA_FIELDS = ["AREA", "Площадь", "ПЛОЩАДЬ"]
    FLOORS_FIELDS = ["FLOORS", "Этажность", "ЭТАЖНОСТЬ"]
    
    for idx, row in gdf.iterrows():
        obj = {
            "cadnum": get_field_value(row, CADNUM_FIELDS),
            "object_type": get_field_value(row, TYPE_FIELDS),
            "purpose": get_field_value(row, PURPOSE_FIELDS),
            "area": get_field_value(row, AREA_FIELDS),
            "floors": get_field_value(row, FLOORS_FIELDS),
            "geometry": row.get('geometry'),
        }
        objects.append(obj)
    
    logger.info(f"Загружено объектов капстроительства из {Path(tab_path).name}: {len(objects)}")
    return objects


def find_objects_on_parcel(coords: List[Tuple[float, float]], objects: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Найти объекты капстроительства на участке.
    
    Args:
        coords: Координаты участка
        objects: Список объектов с геометрией
    
    Returns:
        Список найденных объектов
    """
    if not coords or len(coords) < 3:
        return []
    
    try:
        parcel_polygon = Polygon(coords)
        found = []
        
        for obj in objects:
            obj_geom = obj.get('geometry')
            if obj_geom is None:
                continue
            
            if parcel_polygon.intersects(obj_geom):
                found.append({
                    "cadnum": obj.get('cadnum'),
                    "object_type": obj.get('object_type'),
                    "purpose": obj.get('purpose'),
                    "area": obj.get('area'),
                    "floors": obj.get('floors'),
                })
        
        return found
        
    except Exception as ex:
        logger.error(f"Ошибка при поиске объектов: {ex}")
        return []


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
    
    # Определяем названия полей (с учётом возможных вариантов)
    PROJECT_TYPE_FIELDS = ["Вид_проекта", "PROJECT_TYPE", "ВидПроекта", "Вид", "TYPE"]
    PROJECT_NAME_FIELDS = ["Наименование_проекта", "PROJECT_NAME", "NAME", "Наименование", "НАИМЕНОВАНИЕ"]
    DECISION_NUMBER_FIELDS = ["Номер_распоряжения", "DECISION_NUMBER", "DEC_NUM", "НомерРешения", "Номер_решения", "НОМЕР_РЕШЕНИЯ", "Номер"]
    DECISION_DATE_FIELDS = ["Дата_распоряжения", "DECISION_DATE", "DEC_DATE", "ДатаРешения", "Дата_решения", "ДАТА_РЕШЕНИЯ", "Дата"]
    DECISION_AUTHORITY_FIELDS = ["DECISION_AUTHORITY", "AUTHORITY", "ОрганУтвердивший", "Орган", "ОРГАН"]
    
    for idx, row in gdf.iterrows():
        project = {
            "project_type": get_field_value(row, PROJECT_TYPE_FIELDS),
            "project_name": get_field_value(row, PROJECT_NAME_FIELDS),
            "decision_number": get_field_value(row, DECISION_NUMBER_FIELDS),
            "decision_date": get_field_value(row, DECISION_DATE_FIELDS),
            "decision_authority": get_field_value(row, DECISION_AUTHORITY_FIELDS),
            "geometry": row.get('geometry'),
        }
        projects.append(project)
    
    logger.info(f"Загружено проектов планировки из {Path(tab_path).name}: {len(projects)}")
    return projects


def check_planning_project_intersection(coords: List[Tuple[float, float]], projects: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    Проверить попадание участка в границы проекта планировки.
    
    Args:
        coords: Координаты участка
        projects: Список проектов с геометрией
    
    Returns:
        Информация о проекте или None
    """
    if not coords or len(coords) < 3:
        return None
    
    try:
        parcel_polygon = Polygon(coords)
        
        for project in projects:
            proj_geom = project.get('geometry')
            if proj_geom is None:
                continue
            
            if parcel_polygon.intersects(proj_geom):
                return {
                    "project_type": project.get('project_type'),
                    "project_name": project.get('project_name'),
                    "decision_number": project.get('decision_number'),
                    "decision_date": project.get('decision_date'),
                    "decision_authority": project.get('decision_authority'),
                }
        
        return None
        
    except Exception as ex:
        logger.error(f"Ошибка при проверке ППТ: {ex}")
        return None


def parse_zouit_layer_extended(tab_path: Path | str, zone_type: str = "ЗОУИТ") -> List[Dict[str, Any]]:
    """
    Парсинг слоя ЗОУИТ с расширенными полями (включая реестровый номер).
    
    Args:
        tab_path: Путь к TAB-файлу
        zone_type: Тип зоны (для идентификации)
    
    Returns:
        Список зон с ограничениями
    """
    gdf = read_tab_file(tab_path)
    if gdf is None or gdf.empty:
        return []
    
    restrictions = []
    
    # Определяем названия полей для ЗОУИТ
    NAME_FIELDS = [
        "Вид_или_наименование_по_доку_8",  # Полное описание
        "Полное_наименование",              # Краткое название
        "Наименование",                     # Название
        "NAME", 
        "НАИМЕНОВАНИЕ", 
        "Название"
    ]
    REGISTRY_FIELDS = [
        "Реестровый_номер_границы",        # Реестровый номер границы
        "REGISTRY_NUMBER", 
        "РеестровыйНомер", 
        "Реестровый_номер", 
        "УчетныйНомер"
    ]
    DECISION_NUMBER_FIELDS = [
        "Номер",
        "DECISION_NUMBER", 
        "НомерРешения", 
        "Номер_решения"
    ]
    DECISION_DATE_FIELDS = [
        "Дата_регистрации",                # Дата регистрации
        "Дата_создания",                    # Дата создания (альтернатива)
        "DECISION_DATE", 
        "ДатаРешения", 
        "Дата_решения"
    ]
    DECISION_AUTHORITY_FIELDS = [
        "DECISION_AUTHORITY", 
        "ОрганУтвердивший", 
        "Орган"
    ]
    
    for idx, row in gdf.iterrows():
        restriction = {
            "zone_type": zone_type,
            "name": get_field_value(row, NAME_FIELDS),
            "registry_number": get_field_value(row, REGISTRY_FIELDS),
            "decision_number": get_field_value(row, DECISION_NUMBER_FIELDS),
            "decision_date": get_field_value(row, DECISION_DATE_FIELDS),
            "decision_authority": get_field_value(row, DECISION_AUTHORITY_FIELDS),
            "geometry": row.get('geometry'),
        }
        restrictions.append(restriction)
    
    logger.info(f"Загружено ограничений из {Path(tab_path).name}: {len(restrictions)}")
    return restrictions


def find_restrictions_for_parcel(coords: List[Tuple[float, float]], restrictions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Найти ограничения для участка.
    
    Args:
        coords: Координаты участка
        restrictions: Список ограничений с геометрией
    
    Returns:
        Список найденных ограничений
    """
    if not coords or len(coords) < 3:
        return []
    
    try:
        parcel_polygon = Polygon(coords)
        found = []
        
        for restr in restrictions:
            restr_geom = restr.get('geometry')
            if restr_geom is None:
                continue
            
            if parcel_polygon.intersects(restr_geom):
                found.append({
                    "zone_type": restr.get('zone_type'),
                    "name": restr.get('name'),
                    "registry_number": restr.get('registry_number'),
                    "decision_number": restr.get('decision_number'),
                    "decision_date": restr.get('decision_date'),
                    "decision_authority": restr.get('decision_authority'),
                })
        
        return found
        
    except Exception as ex:
        logger.error(f"Ошибка при поиске ограничений: {ex}")
        return []