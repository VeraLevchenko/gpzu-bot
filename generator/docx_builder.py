from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docxtpl import DocxTemplate
from docx import Document as Docx
from docx.text.paragraph import Paragraph
from docx.table import _Cell, Table
from docx.shared import Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT

REGION_CONST = "Кемеровская область – Кузбасс"
MUNICIPALITY_CONST = "Новокузнецкий городской округ"
SETTLEMENT_CONST = ""
MARKER_COORDS = "[[COORDS_TABLE]]"  # таблица координат ищется и заменяется кодом

@dataclass
class Coord:
    num: Optional[str]
    x: str
    y: str

@dataclass
class EGRNData:
    cadnum: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    settlement: Optional[str] = None
    coordinates: List[Coord] = None
    is_land: bool = False
    has_coords: bool = False
    capital_objects: List[str] = None

def _render_context(egrn: EGRNData) -> Dict[str, Any]:
    # текст для ОКС (используется в {{ parcel.capital_objects_text }})
    if egrn.capital_objects:
        capital_text = ", ".join(egrn.capital_objects)
    else:
        capital_text = "Объекты капитального строительства отсутствуют"  # без точки
    return {
        "gpzu": {"number": "", "application_ref": ""},
        "parcel": {
            "region": REGION_CONST,
            "municipality": MUNICIPALITY_CONST,
            "settlement": SETTLEMENT_CONST,
            "address": egrn.address or "",
            "cadnum": egrn.cadnum or "",
            "area": egrn.area or "",
            "capital_objects_text": capital_text,
        },
    }

def _fmt_coord(v: str) -> str:
    # в документ — десятичная запятая
    return (v or "").strip().replace(" ", "").replace(".", ",")

def _walk_paragraphs(doc: Docx):
    # верхний уровень
    for p in doc.paragraphs:
        yield p
    # вложенные параграфы в таблицах
    def cell_paragraphs(cell: _Cell):
        for p in cell.paragraphs:
            yield p
        for tbl in cell.tables:
            for row in tbl.rows:
                for c in row.cells:
                    yield from cell_paragraphs(c)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                yield from cell_paragraphs(cell)

def _find_marker_paragraph(doc: Docx, marker: str) -> Optional[Paragraph]:
    for p in _walk_paragraphs(doc):
        txt = "".join(r.text for r in p.runs) if p.runs else p.text
        if txt and marker in txt:
            return p
    return None

def _remove_paragraph(p: Paragraph):
    p._element.getparent().remove(p._element)

def _center_cell(cell: _Cell):
    # вертикаль + горизонталь по центру
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    for par in cell.paragraphs:
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER

def _apply_table_layout(tbl: Table):
    # общая ширина 17,88 см = 4,50 + 6,69 + 6,69
    try:
        tbl.autofit = False
    except Exception:
        pass
    try:
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    except Exception:
        pass
    col_widths = [Cm(4.50), Cm(6.69), Cm(6.69)]
    for row in tbl.rows:
        for i, cell in enumerate(row.cells):
            if i < len(col_widths):
                cell.width = col_widths[i]
            _center_cell(cell)

def _make_table_with_group_header(doc: Docx, rows: List[List[str]]) -> Table:
    """
    Двухстрочная шапка:
      R0C0 (верт. merge с R1C0): "Обозначение (номер) характерной точки"
      R0C1..R0C2 (гор. merge): общий заголовок для X и Y
      R1C1: "X", R1C2: "Y"
    """
    tbl = doc.add_table(rows=2, cols=3)
    try:
        tbl.style = "Table Grid"
    except Exception:
        pass

    # Верхняя строка
    top = tbl.rows[0].cells
    top[0].text = "Обозначение (номер) характерной точки"
    top[1].text = ("Перечень координат характерных точек в системе координат, "
                   "используемой для ведения Единого государственного реестра недвижимости")
    top[2].text = ""  # объединится с top[1]

    # Нижняя строка шапки
    bot = tbl.rows[1].cells
    bot[0].text = ""  # объединится с top[0]
    bot[1].text = "X"
    bot[2].text = "Y"

    # Объединения
    top[0].merge(bot[0])   # вертикально
    top[1].merge(top[2])   # горизонтально

    # Центровка шапки
    for c in top + bot:
        _center_cell(c)

    # Данные
    for n, x, y in rows:
        r = tbl.add_row().cells
        r[0].text = n
        r[1].text = x
        r[2].text = y
        for c in r:
            _center_cell(c)

    _apply_table_layout(tbl)
    return tbl

def _move_tbl_after_paragraph(tbl: Table, anchor: Paragraph):
    tbl_elm = tbl._tbl
    anchor_elm = anchor._p
    parent = anchor_elm.getparent()
    parent.insert(parent.index(anchor_elm) + 1, tbl_elm)

def _inject_coords_table(doc: Docx, coords: List[Coord]):
    # данные в порядке парсера
    data_rows = [[(c.num or "").strip(), _fmt_coord(c.x), _fmt_coord(c.y)] for c in coords or []]

    marker_p = _find_marker_paragraph(doc, MARKER_COORDS)
    if not marker_p:
        return

    new_tbl = _make_table_with_group_header(doc, data_rows)
    _move_tbl_after_paragraph(new_tbl, marker_p)
    _remove_paragraph(marker_p)

def build_section1_docx(egrn: EGRNData) -> bytes:
    # сначала docxtpl подставит все {{ ... }}, включая {{ parcel.capital_objects_text }}
    tpl_path = Path(__file__).resolve().parent.parent / "templates" / "gpzu_template.docx"
    tpl = DocxTemplate(tpl_path.as_posix())
    ctx = _render_context(egrn)

    bio = BytesIO()
    tpl.render(ctx)
    tpl.save(bio)
    bio.seek(0)

    # затем пост-обработка: таблица координат вместо [[COORDS_TABLE]]
    doc = Docx(bio)
    _inject_coords_table(doc, egrn.coordinates or [])

    out = BytesIO()
    doc.save(out)
    out.seek(0)
    return out.read()
