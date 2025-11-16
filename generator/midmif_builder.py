# generator/midmif_builder.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional
from pathlib import Path


@dataclass
class SimpleCoord:
    num: str
    x: str
    y: str


def _sanitize_cadnum(cadnum: Optional[str]) -> str:
    if not cadnum:
        return "no_cad"
    return cadnum.replace(":", "_").replace(" ", "_")


def _parse_float(s: str) -> Optional[float]:
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _format_decimal(val: Optional[float], digits: int = 2) -> str:
    if val is None:
        return f"0.{('0' * digits)}"
    fmt = f"%.{digits}f" % val
    return fmt


def _build_mif_text(
    cadnum: Optional[str],
    area: Optional[str],
    coords: List[SimpleCoord],
) -> str:
    """
    Формирует содержимое MIF-файла:
      - заголовок (Version, Charset, Delimiter, CoordSys, Columns...)
      - один Region (контур ЗУ)
      - Text-объекты с подписями номеров точек
    """
    if not coords:
        raise ValueError("Нет координат для построения MIF.")

    # Замыкаем контур (на случай, если он не замкнут)
    closed = list(coords)
    first = coords[0]
    last = coords[-1]
    if first.x != last.x or first.y != last.y:
        closed.append(first)

    # Центр (для Center)
    xs = [_parse_float(c.x) for c in closed if _parse_float(c.x) is not None]
    ys = [_parse_float(c.y) for c in closed if _parse_float(c.y) is not None]
    if xs and ys:
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
    else:
        cx = cy = 0.0

    cad_display = cadnum or "—"
    area_val = _parse_float(area or "")

    lines: List[str] = []

    # --- HEADER ---
    lines.append("Version   450")
    lines.append('Charset "WindowsCyrillic"')
    lines.append('Delimiter ","')
    # Используем заданную тобой систему координат
    lines.append(
        'CoordSys Earth Projection 8, 1001, "m", 88.46666666666, 0, 1, '
        '2300000, -5512900.5719999997 Bounds (-7786100, -9553200) (12213900, 10446800)'
    )
    lines.append("Columns 10")
    lines.append('  Идентификатор_объекта Char(40)')
    lines.append('  Код_Код_объекта Char(20)')
    lines.append('  Код_объекта Char(250)')
    lines.append('  Код_Индекс_зоны Char(20)')
    lines.append('  Индекс_зоны Char(10)')
    lines.append('  Код_Наименование_объекта Char(20)')
    lines.append('  Наименование_объекта Char(220)')
    lines.append('  Номер_зоны Integer')
    lines.append('  Площадь_кв_м Decimal(15, 2)')
    lines.append('  Примечание Char(220)')
    lines.append("Data")

    # --- REGION ---
    lines.append("")
    lines.append("Region  1")
    lines.append(f"  {len(closed)}")
    for c in closed:
        # Координаты как есть, только запятые заменяем на точки
        x = c.x.replace(",", ".")
        y = c.y.replace(",", ".")
        lines.append(f"{x} {y}")

    lines.append("    Pen (15,2,0)")
    lines.append("    Brush (2,13269749,16777215)")
    lines.append(
        f"    Center {_format_decimal(cx, 2)} {_format_decimal(cy, 2)}"
    )

    # --- TEXT OBJECTS (подписи точек) ---
    # Для каждого исходного (НЕ замыкающего) узла создаём Text "N" x y
    for c in coords:
        x = c.x.replace(",", ".")
        y = c.y.replace(",", ".")
        text = c.num.strip() or "?"
        lines.append("")
        # простейшее текстовое обозначение
        lines.append(f'Text "{text}" {x} {y}')
        lines.append('    Font ("Arial",0,0,0)')
        lines.append("    Pen (1,2,0)")

    return "\n".join(lines)


def _build_mid_text(
    cadnum: Optional[str],
    area: Optional[str],
    coords: List[SimpleCoord],
) -> str:
    """
    Формируем содержимое MID-файла.
    Для каждого объекта (Region + каждый Text) — одна строка.
    """
    cad = cadnum or ""
    area_val = _parse_float(area or "")
    area_str = _format_decimal(area_val, 2)

    rows: List[str] = []

    def _csv_row(
        Идентификатор_объекта="",
        Код_Код_объекта="",
        Код_объекта="",
        Код_Индекс_зоны="",
        Индекс_зоны="",
        Код_Наименование_объекта="",
        Наименование_объекта="",
        Номер_зоны="0",
        Площадь_кв_м=area_str,
        Примечание="",
    ):
        # строка в формате MID (Delimiter ",")
        fields = [
            Идентификатор_объекта,
            Код_Код_объекта,
            Код_объекта,
            Код_Индекс_зоны,
            Индекс_зоны,
            Код_Наименование_объекта,
            Наименование_объекта,
            str(Номер_зоны),
            Площадь_кв_м,
            Примечание,
        ]
        parts = []
        for v in fields:
            # число для Decimal и Integer оставляем без кавычек
            if v.replace(".", "", 1).isdigit():
                parts.append(v)
            else:
                parts.append(f'"{v}"')
        return ",".join(parts)

    # 1) строка для региона
    rows.append(
        _csv_row(
            Идентификатор_объекта=cad,
            Наименование_объекта=f"Земельный участок {cad}",
            Номер_зоны="1",
            Площадь_кв_м=area_str,
            Примечание="контур ЗУ",
        )
    )

    # 2) строки для подписи точек (Text)
    for c in coords:
        rows.append(
            _csv_row(
                Идентификатор_объекта=cad,
                Наименование_объекта=f"Точка {c.num}",
                Номер_зоны="0",
                Площадь_кв_м="0.00",
                Примечание=f"узел контура {c.num}",
            )
        )

    return "\n".join(rows)


def build_mid_mif_from_coords(
    cadnum: Optional[str],
    area: Optional[str],
    coords: List[Tuple[str, str, str]],  # (num, x, y) без замыкающей точки
) -> Tuple[str, bytes, bytes]:
    """
    Главная функция генерации MIF/MID.

    На вход:
      cadnum  – кадастровый номер (может быть None)
      area    – площадь (как строка)
      coords  – список (num, x, y) в том порядке, как в ЕГРН
                (ЗДЕСЬ без замыкающей точки – мы сами замыкаем контур в MIF)

    На выход:
      (base_name, mif_bytes, mid_bytes)
      base_name без расширения, чтобы можно было сделать base_name.mif, base_name.mid
    """
    if not coords:
        raise ValueError("Нет координат для генерации MID/MIF.")

    simple_coords = [SimpleCoord(num=n, x=x, y=y) for (n, x, y) in coords]

    mif_text = _build_mif_text(cadnum, area, simple_coords)
    mid_text = _build_mid_text(cadnum, area, simple_coords)

    # Используем кодировку Windows-1251, т.к. в MIF указан Charset "WindowsCyrillic"
    mif_bytes = mif_text.encode("cp1251", errors="replace")
    mid_bytes = mid_text.encode("cp1251", errors="replace")

    base_name = _sanitize_cadnum(cadnum)
    return base_name, mif_bytes, mid_bytes
