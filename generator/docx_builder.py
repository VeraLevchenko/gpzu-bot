from typing import Optional, List
from docx import Document as Docx
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from dataclasses import dataclass

# Дублируем простые модели, чтобы не плодить зависимостей
@dataclass
class Coord:
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

def build_section1_docx(egrn: EGRNData) -> bytes:
    """
    Формируем DOCX с разделом 1 новой формы ГПЗУ:
    - заголовок
    - реквизиты местоположения
    - КН, площадь
    - таблица координат X/Y
    Остальные разделы пока не добавляем.
    """
    d = Docx()

    # Заголовок
    p = d.add_paragraph("Градостроительный план земельного участка")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].bold = True
    p.runs[0].font.size = Pt(14)

    d.add_paragraph()

    # Блок "Местонахождение земельного участка"
    h = d.add_paragraph("Местонахождение земельного участка")
    h.runs[0].bold = True

    tbl_meta = d.add_table(rows=0, cols=2)
    def row(lbl: str, val: Optional[str]):
        r = tbl_meta.add_row().cells
        r[0].text = lbl
        r[1].text = val or ""

    row("Субъект Российской Федерации", egrn.region or "")
    row("Муниципальный район или городской округ", egrn.municipality or "")
    row("Поселение", egrn.settlement or "")
    if egrn.address:
        row("Адрес (по ЕГРН, при наличии)", egrn.address)

    d.add_paragraph()

    # Кадастровый номер и площадь
    h2 = d.add_paragraph("Сведения о земельном участке")
    h2.runs[0].bold = True

    tbl_id = d.add_table(rows=0, cols=2)
    row_id = tbl_id.add_row().cells
    row_id[0].text = "Кадастровый номер"
    row_id[1].text = egrn.cadnum or ""
    row_area = tbl_id.add_row().cells
    row_area[0].text = "Площадь"
    row_area[1].text = egrn.area or ""

    d.add_paragraph()

    # Таблица координат (как в форме: №, X, Y)
    d.add_paragraph("Описание границ (характерные точки)").runs[0].bold = True
    coords_tbl = d.add_table(rows=1, cols=3)
    hdr = coords_tbl.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "№", "X", "Y"
    if egrn.coordinates:
        for i, c in enumerate(egrn.coordinates, start=1):
            rowc = coords_tbl.add_row().cells
            rowc[0].text = str(i)
            rowc[1].text = c.x
            rowc[2].text = c.y

    import io
    bio = io.BytesIO()
    d.save(bio)
    bio.seek(0)
    return bio.read()
