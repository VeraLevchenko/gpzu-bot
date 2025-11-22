# models/gp_data.py
"""
Модель данных для градостроительного плана.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import date
import json


@dataclass
class ApplicationInfo:
    """Данные из заявления"""
    number: Optional[str] = None
    date: Optional[str] = None
    date_text: Optional[str] = None
    applicant: Optional[str] = None
    purpose: Optional[str] = None
    service_date: Optional[str] = None


@dataclass
class ParcelInfo:
    """Данные о земельном участке из ЕГРН"""
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None
    permitted_use: Optional[str] = None
    coordinates: List[Dict[str, str]] = field(default_factory=list)
    capital_objects_egrn: List[str] = field(default_factory=list)


@dataclass
class TerritorialZoneInfo:
    """Информация о территориальной зоне"""
    name: Optional[str] = None
    code: Optional[str] = None
    vri_main: List[str] = field(default_factory=list)
    vri_conditional: List[str] = field(default_factory=list)
    vri_auxiliary: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    act_reference: Optional[str] = None


@dataclass
class CapitalObject:
    """Объект капитального строительства"""
    cadnum: Optional[str] = None
    object_type: Optional[str] = None
    purpose: Optional[str] = None
    area: Optional[str] = None
    floors: Optional[str] = None
    year_built: Optional[str] = None


@dataclass
class PlanningProject:
    """Проект планировки территории"""
    exists: bool = False
    decision_number: Optional[str] = None
    decision_date: Optional[str] = None
    decision_authority: Optional[str] = None
    decision_full: Optional[str] = None
    project_name: Optional[str] = None
    territory: Optional[str] = None


@dataclass
class RestrictionZone:
    """Зона с особыми условиями использования территории"""
    zone_type: str
    name: Optional[str] = None
    registry_number: Optional[str] = None  # ДОБАВЛЕНО
    decision_number: Optional[str] = None
    decision_date: Optional[str] = None
    decision_authority: Optional[str] = None
    restrictions: List[str] = field(default_factory=list)
    additional_info: Optional[str] = None
    
    def get_full_name(self) -> str:
        """Получить полное название с реестровым номером"""
        if self.name and self.registry_number:
            return f"{self.name} ({self.registry_number})"
        elif self.name:
            return self.name
        elif self.registry_number:
            return f"ЗОУИТ {self.registry_number}"
        else:
            return f"ЗОУИТ ({self.zone_type})"


@dataclass
class GPData:
    """Полная модель данных градплана"""
    application: ApplicationInfo = field(default_factory=ApplicationInfo)
    parcel: ParcelInfo = field(default_factory=ParcelInfo)
    zone: TerritorialZoneInfo = field(default_factory=TerritorialZoneInfo)
    capital_objects: List[CapitalObject] = field(default_factory=list)
    planning_project: PlanningProject = field(default_factory=PlanningProject)
    zouit: List[RestrictionZone] = field(default_factory=list)
    ago: List[RestrictionZone] = field(default_factory=list)
    krt: List[RestrictionZone] = field(default_factory=list)
    okn: List[RestrictionZone] = field(default_factory=list)
    other_restrictions: List[RestrictionZone] = field(default_factory=list)
    gp_number: Optional[str] = None
    gp_date: Optional[str] = None
    analysis_completed: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
    
    def add_error(self, error: str):
        self.errors.append(error)
    
    def add_warning(self, warning: str):
        self.warnings.append(warning)
    
    def has_restrictions(self) -> bool:
        return bool(self.zouit or self.ago or self.krt or self.okn or self.other_restrictions)
    
    def get_all_restrictions(self) -> List[RestrictionZone]:
        return self.zouit + self.ago + self.krt + self.okn + self.other_restrictions
    
    def get_summary(self) -> str:
        lines = []
        lines.append("📊 СВОДКА ДАННЫХ ДЛЯ ГРАДПЛАНА\n")
        
        lines.append("📄 ЗАЯВЛЕНИЕ:")
        lines.append(f"  Номер: {self.application.number or '—'}")
        lines.append(f"  Заявитель: {self.application.applicant or '—'}")
        lines.append("")
        
        lines.append("🗺 ЗЕМЕЛЬНЫЙ УЧАСТОК:")
        lines.append(f"  Кадастровый номер: {self.parcel.cadnum or '—'}")
        lines.append(f"  Адрес: {self.parcel.address or '—'}")
        lines.append(f"  Площадь: {self.parcel.area or '—'} кв. м")
        lines.append("")
        
        lines.append("📍 ТЕРРИТОРИАЛЬНАЯ ЗОНА:")
        if self.zone.code or self.zone.name:
            lines.append(f"  {self.zone.code or ''} {self.zone.name or ''}")
        else:
            lines.append("  Не определена")
        lines.append("")
        
        lines.append("🏢 ОБЪЕКТЫ КАПСТРОИТЕЛЬСТВА:")
        lines.append(f"  Найдено: {len(self.capital_objects)} шт.")
        lines.append("")
        
        lines.append("📋 ПРОЕКТ ПЛАНИРОВКИ:")
        if self.planning_project.exists:
            lines.append(f"  Участок входит в границы ППТ")
        else:
            lines.append("  Не входит в границы ППТ")
        lines.append("")
        
        restrictions_count = len(self.get_all_restrictions())
        lines.append("⚠️ ОГРАНИЧЕНИЯ:")
        if restrictions_count > 0:
            lines.append(f"  Всего: {restrictions_count}")
            if self.zouit:
                lines.append(f"  - ЗОУИТ: {len(self.zouit)}")
                for z in self.zouit[:3]:
                    lines.append(f"    • {z.get_full_name()}")
                if len(self.zouit) > 3:
                    lines.append(f"    ... и ещё {len(self.zouit) - 3}")
            if self.okn:
                lines.append(f"  - ОКН: {len(self.okn)}")
        else:
            lines.append("  Отсутствуют")
        
        if self.errors:
            lines.append("\n❌ ОШИБКИ:")
            for err in self.errors:
                lines.append(f"  • {err}")
        
        if self.warnings:
            lines.append("\n⚠️ ПРЕДУПРЕЖДЕНИЯ:")
            for warn in self.warnings:
                lines.append(f"  • {warn}")
        
        return "\n".join(lines)


def create_gp_data_from_parsed(
    application_dict: Dict[str, Any],
    egrn_dict: Dict[str, Any]
) -> GPData:
    gp = GPData()
    
    gp.application = ApplicationInfo(
        number=application_dict.get('number'),
        date=application_dict.get('date'),
        date_text=application_dict.get('date_text'),
        applicant=application_dict.get('applicant'),
        purpose=application_dict.get('purpose'),
        service_date=application_dict.get('service_date'),
    )
    
    coords_list = egrn_dict.get('coordinates', [])
    coords_dicts = []
    if coords_list:
        for c in coords_list:
            if hasattr(c, 'num'):
                coords_dicts.append({'num': c.num, 'x': c.x, 'y': c.y})
            elif isinstance(c, dict):
                coords_dicts.append(c)
    
    gp.parcel = ParcelInfo(
        cadnum=egrn_dict.get('cadnum'),
        address=egrn_dict.get('address'),
        area=egrn_dict.get('area'),
        region=egrn_dict.get('region'),
        municipality=egrn_dict.get('municipality'),
        settlement=egrn_dict.get('settlement'),
        permitted_use=egrn_dict.get('permitted_use'),
        coordinates=coords_dicts,
        capital_objects_egrn=egrn_dict.get('capital_objects', []),
    )
    
    return gp
