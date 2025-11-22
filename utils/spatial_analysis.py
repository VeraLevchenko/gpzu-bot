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
    parse_zouit_layer_extended,
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
    """
    Извлечь координаты участка.
    
    ВАЖНО: Координаты в gp_data.parcel.coordinates УЖЕ в порядке (Y, X),
    т.к. они были преобразованы при создании GPData в create_gp_data_from_parsed().
    
    ЕГРН изначально: X (север), Y (восток)
    В JSON/GPData: x=Y (восток), y=X (север) - УЖЕ ПОМЕНЯНЫ!
    
    Returns:
        List[(y, x)] - координаты в формате для Shapely (восток, север)
    """
    coords_list = gp_data.parcel.coordinates
    if not coords_list:
        return []
    
    result = []
    for coord in coords_list:
        try:
            # В JSON координаты УЖЕ поменяны местами:
            # coord['x'] = это Y из ЕГРН (восток)
            # coord['y'] = это X из ЕГРН (север)
            x_str = coord.get('x', '')  # Это Y (восток)
            y_str = coord.get('y', '')  # Это X (север)
            x = float(x_str.replace(',', '.').replace(' ', ''))
            y = float(y_str.replace(',', '.').replace(' ', ''))
            
            # Для Shapely нужен порядок (восток, север), т.е. (x, y)
            # Координаты уже в правильном порядке!
            result.append((x, y))
            
            logger.debug(f"Координата из JSON: x(восток)={x}, y(север)={y} → ({x}, {y}) для Shapely")
            
        except (ValueError, AttributeError, KeyError) as ex:
            logger.warning(f"Ошибка парсинга координаты: {ex}")
            continue
    
    if result:
        logger.info(f"Извлечено {len(result)} координат (уже в формате Y,X для анализа)")
    
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
            # Создаём объект зоны
            zone = TerritorialZoneInfo(
                name=zone_info.get('name'),
                code=zone_info.get('code'),
            )
            
            # Устанавливаем дополнительную информацию через свойства
            zone.multiple_zones = zone_info.get('multiple_zones', False)
            zone.all_zones = zone_info.get('all_zones', [])
            zone.overlap_percent = zone_info.get('overlap_percent')
            
            gp_data.zone = zone
            
            # Добавляем предупреждение, если участок в нескольких зонах
            if zone_info.get('multiple_zones'):
                all_zones_list = zone_info.get('all_zones', [])
                zones_text = ", ".join([
                    f"{z['code']} ({z['overlap_percent']:.1f}%)"
                    for z in all_zones_list
                ])
                warning_msg = (
                    f"⚠️ Участок пересекается с несколькими территориальными зонами: {zones_text}. "
                    f"Выбрана зона с максимальным перекрытием: {zone_info.get('code')} "
                    f"({zone_info.get('overlap_percent'):.1f}%)"
                )
                gp_data.add_warning(warning_msg)
                logger.warning(warning_msg)
            
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
        # ВАЖНО: Устанавливаем объект с exists=False и заполненным decision_full
        gp_data.planning_project = PlanningProject(exists=False)
        gp_data.planning_project.decision_full = gp_data.planning_project.get_formatted_description()
        return
    
    try:
        projects = parse_planning_projects_layer(LayerPaths.PLANNING_PROJECTS)
        if not projects:
            logger.info("Слой проектов планировки пуст")
            # ВАЖНО: Устанавливаем объект с exists=False и заполненным decision_full
            gp_data.planning_project = PlanningProject(exists=False)
            gp_data.planning_project.decision_full = gp_data.planning_project.get_formatted_description()
            return
        
        project_info = check_planning_project_intersection(coords, projects)
        if project_info:
            # Создаём объект с новыми полями
            planning_project = PlanningProject(
                exists=True,
                project_type=project_info.get('project_type'),
                project_name=project_info.get('project_name'),
                decision_number=project_info.get('decision_number'),
                decision_date=project_info.get('decision_date'),
                decision_authority=project_info.get('decision_authority'),
            )
            
            # ВАЖНО: Формируем полное описание
            planning_project.decision_full = planning_project.get_formatted_description()
            
            gp_data.planning_project = planning_project
            
            logger.info(
                f"Участок входит в границы ППТ: {project_info.get('project_type')} "
                f'"{project_info.get("project_name")}"'
            )
        else:
            # ВАЖНО: Устанавливаем объект с exists=False и заполненным decision_full
            gp_data.planning_project = PlanningProject(exists=False)
            gp_data.planning_project.decision_full = gp_data.planning_project.get_formatted_description()
            logger.info("Участок не входит в границы ППТ")
            
    except Exception as ex:
        msg = f"Ошибка при проверке ППТ: {ex}"
        logger.exception(msg)
        gp_data.add_error(msg)
        # ВАЖНО: Устанавливаем объект с exists=False и заполненным decision_full в случае ошибки
        gp_data.planning_project = PlanningProject(exists=False)
        gp_data.planning_project.decision_full = gp_data.planning_project.get_formatted_description()


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