# core/layers_config.py
"""
Конфигурация путей к слоям TAB на сервере.

Здесь задаются пути к файлам TAB с различными пространственными данными.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


# ======================== ПУТИ К СЛОЯМ ======================== #

class LayerPaths:
    """
    Пути к файлам TAB со слоями пространственных данных.
    
    Пути читаются из переменных окружения (.env файл).
    """
    
    # Базовая папка (опционально)
    BASE = Path(os.getenv("LAYERS_BASE_PATH", "/mnt/graphics/NOVOKUZ"))
    
    # ===== ОСНОВНЫЕ СЛОИ ===== #
    
    # Территориальные зоны
    ZONES = Path(os.getenv(
        "LAYER_ZONES",
        "/mnt/graphics/NOVOKUZ/_Правила землепользования и застройки/Территориальные_зоны_пр.TAB"
    ))
    
    # Объекты капитального строительства (ACTUAL_OKSN)
    CAPITAL_OBJECTS = Path(os.getenv(
        "LAYER_CAPITAL_OBJECTS",
        "/mnt/graphics/NOVOKUZ/ФГУ участки/ACTUAL_OKSN.TAB"
    ))
    
    # Проекты планировки территории
    PLANNING_PROJECTS = Path(os.getenv(
        "LAYER_PLANNING_PROJECTS",
        "/mnt/graphics/NOVOKUZ/Проекты планировок и межеваний.TAB"
    ))
    
    # ЗОУИТ (все типы в одном файле)
    ZOUIT = Path(os.getenv(
        "LAYER_ZOUIT",
        "/mnt/graphics/NOVOKUZ/ФГУ участки/ACTUAL_ZOUIT.TAB"
    ))
    
    # Объекты культурного наследия (ОКН)
    OKN = Path(os.getenv(
        "LAYER_OKN",
        "/mnt/graphics/NOVOKUZ/ЗОНЫ КУЛЬТУРНОГО НАСЛЕДИЯ/Объекты культурного наследия.TAB"
    ))
    
    # ===== ДОПОЛНИТЕЛЬНЫЕ СЛОИ ОКН ===== #
    
    # Зоны охраны ОКН
    OKN_ZONES = Path(os.getenv(
        "LAYER_OKN_ZONES",
        "/mnt/graphics/NOVOKUZ/ЗОНЫ КУЛЬТУРНОГО НАСЛЕДИЯ/Зоны охраны объектов культурного наследия.TAB"
    ))
    
    # Границы территорий ОКН
    OKN_BOUNDARIES = Path(os.getenv(
        "LAYER_OKN_BOUNDARIES",
        "/mnt/graphics/NOVOKUZ/ЗОНЫ КУЛЬТУРНОГО НАСЛЕДИЯ/Границы территорий объектов Культурного наследия.TAB"
    ))
    
    # ===== ЗАГЛУШКИ ДЛЯ ПОКА НЕИСПОЛЬЗУЕМЫХ СЛОЁВ ===== #
    
    # Эти слои пока не используются, но структура готова
    ZOUIT_COMMUNICATIONS = ZOUIT  # Все ЗОУИТ в одном файле
    ZOUIT_SANITARY = ZOUIT
    ZOUIT_WATER = ZOUIT
    ZOUIT_OTHER = ZOUIT
    
    AGO = BASE / "ago.tab"  # Если появится слой АГО
    KRT = BASE / "krt.tab"  # Если появится слой КРТ
    
    
    @classmethod
    def get_all_zouit_layers(cls) -> list[Path]:
        """Получить список всех слоёв ЗОУИТ (пока один файл)"""
        return [cls.ZOUIT]
    
    @classmethod
    def check_layers_exist(cls) -> dict[str, bool]:
        """
        Проверить существование всех основных слоёв.
        
        Returns:
            Словарь {название_слоя: существует}
        """
        layers = {
            "zones": cls.ZONES,
            "capital_objects": cls.CAPITAL_OBJECTS,
            "planning_projects": cls.PLANNING_PROJECTS,
            "zouit": cls.ZOUIT,
            "okn": cls.OKN,
            "okn_zones": cls.OKN_ZONES,
            "okn_boundaries": cls.OKN_BOUNDARIES,
        }
        
        return {name: path.exists() for name, path in layers.items()}
    
    @classmethod
    def get_missing_layers(cls) -> list[str]:
        """Получить список отсутствующих слоёв"""
        status = cls.check_layers_exist()
        return [name for name, exists in status.items() if not exists]


# ======================== МАППИНГ ПОЛЕЙ ======================== #

class FieldMapping:
    """
    Маппинг названий полей в TAB-файлах.
    
    Разные слои могут использовать разные названия полей.
    Здесь задаём возможные варианты названий для каждого типа данных.
    """
    
    # Территориальные зоны
    ZONE_NAME_FIELDS = ["ZONE_NAME", "NAME", "ZoneName", "Название", "Наименование", "НАИМЕНОВАНИЕ"]
    ZONE_CODE_FIELDS = ["ZONE_CODE", "CODE", "ZoneCode", "Код", "КОД", "Обозначение", "ОБОЗНАЧЕНИЕ"]
    
    # Объекты капитального строительства (ACTUAL_OKSN)
    OBJECT_CADNUM_FIELDS = ["CADNUM", "CAD_NUM", "CadastralNumber", "КадастровыйНомер", "Кадастровый_номер", "КАДАСТРОВЫЙ_НОМЕР"]
    OBJECT_TYPE_FIELDS = ["OBJECT_TYPE", "TYPE", "ObjectType", "ТипОбъекта", "Тип", "ТИП"]
    OBJECT_PURPOSE_FIELDS = ["PURPOSE", "Назначение", "НАЗНАЧЕНИЕ", "Назнач"]
    OBJECT_AREA_FIELDS = ["AREA", "AREA_M2", "Площадь", "ПЛОЩАДЬ"]
    OBJECT_FLOORS_FIELDS = ["FLOORS", "STOREYS", "Этажность", "ЭТАЖНОСТЬ", "Этажей"]
    
    # Проекты планировки
    PROJECT_NAME_FIELDS = ["PROJECT_NAME", "NAME", "Наименование", "НАИМЕНОВАНИЕ"]
    DECISION_NUMBER_FIELDS = ["DECISION_NUMBER", "DEC_NUM", "НомерРешения", "Номер_решения", "НОМЕР_РЕШЕНИЯ"]
    DECISION_DATE_FIELDS = ["DECISION_DATE", "DEC_DATE", "ДатаРешения", "Дата_решения", "ДАТА_РЕШЕНИЯ"]
    DECISION_AUTHORITY_FIELDS = ["DECISION_AUTHORITY", "AUTHORITY", "ОрганУтвердивший", "Орган", "ОРГАН"]
    
    # ЗОУИТ
    ZOUIT_NAME_FIELDS = ["NAME", "Наименование", "НАИМЕНОВАНИЕ", "RESTRICTION_NAME"]
    ZOUIT_TYPE_FIELDS = ["TYPE", "Тип", "ТИП", "RESTRICTION_TYPE", "Вид_ограничения"]
    
    # ОКН (объекты культурного наследия)
    OKN_NAME_FIELDS = ["NAME", "Наименование", "НАИМЕНОВАНИЕ", "Object_name"]
    OKN_CATEGORY_FIELDS = ["CATEGORY", "Категория", "КАТЕГОРИЯ", "Вид"]
    OKN_STATUS_FIELDS = ["STATUS", "Статус", "СТАТУС"]
    
    # Общие поля ограничений
    RESTRICTION_NAME_FIELDS = ["NAME", "RESTRICTION_NAME", "Наименование", "НАИМЕНОВАНИЕ"]
    RESTRICTION_TYPE_FIELDS = ["TYPE", "RESTRICTION_TYPE", "ТипОграничения", "Тип", "ТИП"]
    
    
    @staticmethod
    def find_field(gdf, field_variants: list[str]) -> str | None:
        """
        Найти поле в GeoDataFrame по списку возможных вариантов названий.
        
        Args:
            gdf: GeoDataFrame со слоем
            field_variants: Список возможных названий поля
        
        Returns:
            Название найденного поля или None
        """
        columns = [col.upper() for col in gdf.columns]
        for variant in field_variants:
            if variant.upper() in columns:
                # Возвращаем оригинальное название (с учётом регистра)
                idx = columns.index(variant.upper())
                return gdf.columns[idx]
        return None


# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================== #

def get_layers_status_report() -> str:
    """
    Получить отчёт о статусе слоёв (для логирования/отладки).
    
    Returns:
        Текстовый отчёт
    """
    status = LayerPaths.check_layers_exist()
    missing = LayerPaths.get_missing_layers()
    
    lines = []
    lines.append(f"Базовый путь к слоям: {LayerPaths.BASE}")
    lines.append(f"Всего основных слоёв: {len(status)}")
    lines.append(f"Доступно: {sum(status.values())}")
    lines.append(f"Отсутствует: {len(missing)}")
    
    if missing:
        lines.append("\nОтсутствующие слои:")
        for name in missing:
            lines.append(f"  - {name}")
    else:
        lines.append("\n✅ Все слои доступны!")
    
    lines.append("\nПути к слоям:")
    lines.append(f"  Зоны: {LayerPaths.ZONES}")
    lines.append(f"  Объекты: {LayerPaths.CAPITAL_OBJECTS}")
    lines.append(f"  ППТ: {LayerPaths.PLANNING_PROJECTS}")
    lines.append(f"  ЗОУИТ: {LayerPaths.ZOUIT}")
    lines.append(f"  ОКН: {LayerPaths.OKN}")
    
    return "\n".join(lines)