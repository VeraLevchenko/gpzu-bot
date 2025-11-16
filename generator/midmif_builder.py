from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class SimpleCoord:
    num: str  # номер точки
    x: str    # X из ЕГРН
    y: str    # Y из ЕГРН


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
    return f"{val:.{digits}f}"


# --------------------------------------------------------------
# MIF
# --------------------------------------------------------------
def _build_mif_text(
    cadnum: Optional[str],
    contours: List[List[SimpleCoord]],
) -> str:

    if not contours:
        raise ValueError("Нет контуров")

    all_pts = [p for c in contours for p in c]

    ys = [_parse_float(p.y) for p in all_pts if _parse_float(p.y) is not None]
    xs = [_parse_float(p.x) for p in all_pts if _parse_float(p.x) is not None]

    cy = sum(ys) / len(ys) if ys else 0.0
    cx = sum(xs) / len(xs) if xs else 0.0

    lines: List[str] = []
    lines.append("Version   450")
    lines.append('Charset "WindowsCyrillic"')
    lines.append('Delimiter ","')
    lines.append(
        'CoordSys Earth Projection 8, 1001, "m", '
        '88.46666666666, 0, 1, 2300000, -5512900.5719999997 '
        'Bounds (-7786100, -9553200) (12213900, 10446800)'
    )

    # 2 поля: кадастровый номер и номер точки (для подписей)
    lines.append("Columns 2")
    lines.append('  Идентификатор_объекта Char(40)')
    lines.append('  Номер_точки Char(40)')
    lines.append("Data")
    lines.append("")

    # REGION
    lines.append(f"Region {len(contours)}")
    for cnt in contours:
        lines.append(f"  {len(cnt)}")
        for p in cnt:
            y = p.y.replace(",", ".")
            x = p.x.replace(",", ".")
            lines.append(f"{y} {x}")  # Y X

    lines.append("    Pen (15,2,0)")
    lines.append("    Brush (2,13269749,16777215)")
    lines.append(f"    Center {_format_decimal(cy)} {_format_decimal(cx)}")

    # ТОЧКИ — красные кружки
    # Symbol (symbol, size, color)
    # цвет 255 — один из базовых (как правило, красный/синий в зависимости от палитры),
    # при необходимости потом подберём другой код.
    seen = set()
    for p in all_pts:
        y = p.y.strip().replace(",", ".")
        x = p.x.strip().replace(",", ".")
        key = (y, x)
        if key in seen:
            continue
        seen.add(key)

        lines.append("")
        lines.append(f"Point {y} {x}")
        lines.append("    Symbol (34,6,12)")

    return "\n".join(lines)


# --------------------------------------------------------------
# MID
# --------------------------------------------------------------
def _build_mid_text(
    cadnum: Optional[str],
    contours: List[List[SimpleCoord]],
) -> str:

    cad = cadnum or ""
    rows: List[str] = []

    def _row(idobj: str, num: str) -> str:
        return f"\"{idobj}\",\"{num}\""

    # 1) Region: Номер_точки пустой
    rows.append(_row(cad, ""))

    # 2) точки (уникальные координаты)
    all_pts = [p for c in contours for p in c]
    seen = set()
    for p in all_pts:
        y = p.y.strip().replace(",", ".")
        x = p.x.strip().replace(",", ".")
        key = (y, x)
        if key in seen:
            continue
        seen.add(key)

        rows.append(_row(cad, p.num))

    return "\n".join(rows)


# --------------------------------------------------------------
# API
# --------------------------------------------------------------
def build_mid_mif_from_contours(
    cadnum: Optional[str],
    contours: List[List[Tuple[str, str, str]]],
):
    if not contours:
        raise ValueError("Нет контуров для генерации MID/MIF.")

    simple: List[List[SimpleCoord]] = []
    for cnt in contours:
        row: List[SimpleCoord] = []
        for num, x, y in cnt:
            row.append(SimpleCoord(str(num), x, y))
        simple.append(row)

    mif_text = _build_mif_text(cadnum, simple)
    mid_text = _build_mid_text(cadnum, simple)

    mif_bytes = mif_text.encode("cp1251", errors="replace")
    mid_bytes = mid_text.encode("cp1251", errors="replace")

    return _sanitize_cadnum(cadnum), mif_bytes, mid_bytes
