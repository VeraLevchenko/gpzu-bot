# utils/spatial_analysis.py
"""
Модуль пространственного анализа земельного участка.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from models.gp_data import (
    GPData,
    TerritorialZoneInfo,
    CapitalObject,
    PlanningProject,
    RestrictionZone,
)
from core.layers_config import LayerPaths
from parsers.tab_parser import (
    parse_zones_layer,
    find_zone_for_parcel,
    parse_capital_objects_layer,
    find_objects_on_parcel,
    parse_planning_projects_layer,
    check_planning_project_intersection,
    parse_zouit_layer_extended,  # Используем расширенную версию
    find_restrictions_for_parcel,
)

logger = logging.getLogger("gpzu-bot.spatial_analysis")


def perform_spatial_analysis(gp_data: GPData) -> GPData:
    """Выполнить комплексный пространственный анализ участка"""
    logger.info(f"Начало пространственного анализа для участка {gp_data.parcel.cadnum}")
    
    coords = _get_parcel_coords(gp_data)
    if not coords:
        gp_data.add_error("Отсутствуют координаты участка")
        logger.error("Нет координат участка для анализа")
        return gp_data
    
    logger.info("Этап 1/5: Определение территориальной зоны")
    _analyze_zone(gp_data, coords)
    
    logger.info("Этап 2/5: Поиск объектов капстроительства")
    _analyze_capital_objects(gp_data, coords)
    
    logger.info("Этап 3/5: Проверка проектов планировки")
    _analyze_planning_projects(gp_data, coords)
    
    logger.info("Этап 4/5: Проверка ЗОУИТ")
    _analyze_zouit(gp_data, coords)
    
    logger.info("Этап 5/5: Проверка прочих ограничений")
    _analyze_other_restrictions(gp_data, coords)
    
    gp_data.analysis_completed = True
    logger.info("Пространственный анализ завершён успешно")
    
    return gp_data


def _get_parcel_coords(gp_data: GPData) -> List[Tuple[float, float]]:
    """Извлечь координаты участка"""
    coords_list = gp_data.parcel.coordinates
    if not coords_list:
        return []
    
    result = []
    for coord in coords_list:
        try:
            x_str = coord.get('x', '')
            y_str = coord.get('y', '')
            x = float(x_str.replace(',', '.').replace(' ', ''))
            y = float(y_str.replace(',', '.').replace(' ', ''))
            result.append((x, y))
        except (ValueError, AttributeError, KeyError) as ex:
            logger.warning(f"Ошибка парсинга координаты: {ex}")
            continue
    
    return result


def _analyze_zone(gp_data: GPData, coords: List[Tuple[float, float]]):
    """Определить территориальную зону участка"""
    if not LayerPaths.ZONES.exists():
        msg = f"Слой зон не найден: {LayerPaths.ZONES}"
        logger.warning(msg)
        gp_data.add_warning(msg)
        return
    
    try:
        zones = parse_zones_layer(LayerPaths.ZONES)
        if not zones:
            logger.warning("Слой зон пуст")
            gp_data.add_warning("Слой территориальных зон не содержит данных")
            return
        
        zone_info = find_zone_for_parcel(coords, zones)
        if zone_info:
            gp_data.zone = TerritorialZoneInfo(
                name=zone_info.get('name'),
                code=zone_info.get('code'),
            )
            logger.info(f"Зона определена: {zone_info.get('code')} {zone_info.get('name')}")
        else:
            logger.warning("Не удалось определить зону участка")
            gp_data.add_warning("Территориальная зона не определена")
            
    except Exception as ex:
        msg = f"Ошибка при определении зоны: {ex}"
        logger.exception(msg)
        gp_data.add_error(msg)


def _analyze_capital_objects(gp_data: GPData, coords: List[Tuple[float, float]]):
    """Найти объекты капстроительства на участке"""
    if not LayerPaths.CAPITAL_OBJECTS.exists():
        msg = f"Слой объектов не найден: {LayerPaths.CAPITAL_OBJECTS}"
        logger.warning(msg)
        gp_data.add_warning(msg)
        return
    
    try:
        objects = parse_capital_objects_layer(LayerPaths.CAPITAL_OBJECTS)
        if not objects:
            logger.info("Слой объектов пуст")
            return
        
        found = find_objects_on_parcel(coords, objects)
        for obj_dict in found:
            cap_obj = CapitalObject(
                cadnum=obj_dict.get('cadnum'),
                object_type=obj_dict.get('object_type'),
                purpose=obj_dict.get('purpose'),
                area=obj_dict.get('area'),
                floors=obj_dict.get('floors'),
            )
            gp_data.capital_objects.append(cap_obj)
        
        if found:
            logger.info(f"Найдено объектов на участке: {len(found)}")
        else:
            logger.info("Объекты капстроительства на участке отсутствуют")
            
    except Exception as ex:
        msg = f"Ошибка при поиске объектов: {ex}"
        logger.exception(msg)
        gp_data.add_error(msg)


def _analyze_planning_projects(gp_data: GPData, coords: List[Tuple[float, float]]):
    """Проверить попадание в проект планировки"""
    if not LayerPaths.PLANNING_PROJECTS.exists():
        msg = f"Слой ППТ не найден: {LayerPaths.PLANNING_PROJECTS}"
        logger.warning(msg)
        gp_data.add_warning(msg)
        return
    
    try:
        projects = parse_planning_projects_layer(LayerPaths.PLANNING_PROJECTS)
        if not projects:
            logger.info("Слой проектов планировки пуст")
            return
        
        project_info = check_planning_project_intersection(coords, projects)
        if project_info:
            decision_full = _format_decision(
                project_info.get('decision_number'),
                project_info.get('decision_date'),
                project_info.get('decision_authority'),
            )
            
            gp_data.planning_project = PlanningProject(
                exists=True,
                decision_number=project_info.get('decision_number'),
                decision_date=project_info.get('decision_date'),
                decision_authority=project_info.get('decision_authority'),
                decision_full=decision_full,
                project_name=project_info.get('project_name'),
            )
            logger.info("Участок входит в границы ППТ")
        else:
            gp_data.planning_project = PlanningProject(exists=False)
            logger.info("Участок не входит в границы ППТ")
            
    except Exception as ex:
        msg = f"Ошибка при проверке ППТ: {ex}"
        logger.exception(msg)
        gp_data.add_error(msg)


def _analyze_zouit(gp_data: GPData, coords: List[Tuple[float, float]]):
    """Проверить наличие ЗОУИТ"""
    if not LayerPaths.ZOUIT.exists():
        logger.debug(f"Слой ЗОУИТ не найден: {LayerPaths.ZOUIT}")
        return
    
    try:
        # Используем расширенную функцию с реестровым номером
        restrictions = parse_zouit_layer_extended(LayerPaths.ZOUIT, "ЗОУИТ")
        
        if not restrictions:
            return
        
        found = find_restrictions_for_parcel(coords, restrictions)
        
        for restr_dict in found:
            restriction = RestrictionZone(
                zone_type=restr_dict.get('zone_type', "ЗОУИТ"),
                name=restr_dict.get('name'),
                registry_number=restr_dict.get('registry_number'),
                decision_number=restr_dict.get('decision_number'),
                decision_date=restr_dict.get('decision_date'),
                decision_authority=restr_dict.get('decision_authority'),
            )
            gp_data.zouit.append(restriction)
        
        if found:
            logger.info(f"Найдено ЗОУИТ: {len(found)}")
        
    except Exception as ex:
        msg = f"Ошибка при проверке ЗОУИТ: {ex}"
        logger.warning(msg)
        gp_data.add_warning(msg)


def _analyze_other_restrictions(gp_data: GPData, coords: List[Tuple[float, float]]):
    """Проверить АГО, КРТ, ОКН"""
    # Пока заглушка, можно расширить позже
    pass


def _format_decision(
    number: Optional[str],
    date: Optional[str],
    authority: Optional[str]
) -> str:
    """Форматировать реквизиты решения"""
    parts = []
    if authority:
        parts.append(authority)
    if number:
        parts.append(f"№ {number}")
    if date:
        parts.append(f"от {date}")
    return " ".join(parts) if parts else "Реквизиты не определены"


def test_layers_availability() -> Dict[str, bool]:
    """Проверить доступность слоёв"""
    return LayerPaths.check_layers_exist()


def get_analysis_summary(gp_data: GPData) -> str:
    """Получить сводку анализа"""
    if not gp_data.analysis_completed:
        return "Анализ ещё не выполнен"
    return gp_data.get_summary()
