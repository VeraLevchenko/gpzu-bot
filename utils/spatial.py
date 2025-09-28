# utils/spatial.py
from typing import List, Tuple, Optional
from dataclasses import dataclass
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

@dataclass
class Parcel:
    # Контур участка в порядке XML (x,y) в float
    contour: List[Tuple[float, float]]

@dataclass
class ZoneShape:
    name: str
    # Список контуров (возможны мультиполигоны)
    contours: List[List[Tuple[float, float]]]

def _make_polygon(contour: List[Tuple[float, float]]) -> Optional[Polygon]:
    if len(contour) < 3:
        return None
    poly = Polygon(contour)
    if not poly.is_valid:
        poly = poly.buffer(0)  # попытка чинить самопересечения
    return poly if poly.is_valid else None

def _zone_to_polygon(z: ZoneShape) -> Optional[Polygon]:
    polys = []
    for cont in z.contours:
        p = _make_polygon(cont)
        if p is not None and not p.is_empty:
            polys.append(p)
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    return unary_union(polys)

def determine_zone(parcel: Parcel, zones: List[ZoneShape]) -> Optional[str]:
    """
    Возвращает имя зоны, в которой расположен участок.
    Алгоритм:
      1) ЦЕНТРОИД участка ∈ зона → эта зона.
      2) Иначе берём зону с максимальной площадью пересечения.
      3) Если пересечение нулевое — None.
    """
    ppoly = _make_polygon(parcel.contour)
    if ppoly is None or ppoly.is_empty:
        return None

    centroid = ppoly.centroid

    best_name = None
    best_area = 0.0

    for z in zones:
        zpoly = _zone_to_polygon(z)
        if zpoly is None or zpoly.is_empty:
            continue

        if zpoly.contains(centroid):
            return z.name

        inter = zpoly.intersection(ppoly)
        area = inter.area if not inter.is_empty else 0.0
        if area > best_area:
            best_area = area
            best_name = z.name

    return best_name if best_area > 0 else None
