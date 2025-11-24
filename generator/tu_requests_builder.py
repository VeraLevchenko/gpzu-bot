# generator/tu_requests_builder.py
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from datetime import date
from docxtpl import DocxTemplate
from openpyxl import load_workbook
from filelock import FileLock, Timeout
from parsers.egrn_parser import EGRNData

BASE_DIR = Path(__file__).resolve().parents[1]
TU_TEMPLATES_DIR = BASE_DIR / "templates" / "tu"
TU_JOURNAL_PATH = BASE_DIR / "Журнал_регистрации_ТУ_ГПЗУ.xlsx"
TU_JOURNAL_LOCK_PATH = BASE_DIR / "Журнал_регистрации_ТУ_ГПЗУ.xlsx.lock"
JOURNAL_SHEET_NAME = "Лист1"

# Каждый элемент: (суффикс_для_файла, название_РСО_для_журнала, путь_к_шаблону)
TEMPLATE_CONFIG = [
    ("Водоканал", "ООО «Водоканал»", TU_TEMPLATES_DIR / "Водоканал.docx"),
    ("Газоснабжение", "филиал ООО «Газпром газораспределение Сибирь»", TU_TEMPLATES_DIR / "Газоснабжение.docx"),
    ("Теплоснабжение", "ООО «ЭнергоТранзит», ООО «Новокузнецкая теплосетевая компания»", TU_TEMPLATES_DIR / "Теплоснабжение.docx"),
]

def _format_area(area: Optional[str]) -> str:
    if not area:
        return ""
    s = area.strip().replace(",", ".")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def build_tu_context(cadnum: str, address: str, area: str, vri: str, app_number: str, app_date: str, out_num: str, out_date: str) -> Dict[str, str]:
    return {
        "APP_NUMBER": app_number or "",
        "APP_DATE": app_date or "",
        "CADNUM": cadnum or "",
        "AREA": _format_area(area),
        "VRI": vri or "",
        "ADDRESS": address or "",
        "OUT_NUM": out_num or "",
        "OUT_DATE": out_date or "",
    }

def _render_doc(template_path: Path, context: Dict[str, str]) -> bytes:
    tpl = DocxTemplate(str(template_path))
    tpl.render(context)
    bio = BytesIO()
    tpl.save(bio)
    return bio.getvalue()

def build_tu_docs_with_outgoing(cadnum: str, address: str, area: str, vri: str, app_number: str, app_date: str, applicant: str) -> List[Tuple[str, bytes]]:
    if not TU_JOURNAL_PATH.exists():
        raise FileNotFoundError(f"Не найден журнал регистрации ТУ: {TU_JOURNAL_PATH}")
    
    lock = FileLock(str(TU_JOURNAL_LOCK_PATH), timeout=10)
    
    try:
        with lock:
            try:
                wb = load_workbook(TU_JOURNAL_PATH)
            except PermissionError:
                raise RuntimeError("❌ Не удалось открыть журнал регистрации ТУ. Закройте журнал и попробуйте ещё раз.")
            
            if JOURNAL_SHEET_NAME not in wb.sheetnames:
                raise RuntimeError(f"❌ В журнале не найден лист '{JOURNAL_SHEET_NAME}'. Доступные листы: {wb.sheetnames}")
            
            ws = wb[JOURNAL_SHEET_NAME]
            headers = {cell.value: cell.column for cell in ws[1] if cell.value}
            
            col_out_num = headers.get("Исходящий номер")
            col_out_date = headers.get("Исходящая дата")
            col_app_num = headers.get("Номер заявления")
            col_app_date = headers.get("Дата заявления")
            col_applicant = headers.get("Заявитель")
            col_cadnum = headers.get("Кадастровый номер земельного участка")
            col_address = headers.get("Адрес")
            col_rso = headers.get("РСО")
            
            if not all([col_out_num, col_out_date, col_app_num, col_app_date, col_applicant, col_cadnum, col_address, col_rso]):
                raise RuntimeError(f"❌ В журнале отсутствуют необходимые столбцы. Найдены: {list(headers.keys())}")
            
            max_num = 0
            for row in range(2, ws.max_row + 1):
                val = ws.cell(row=row, column=col_out_num).value
                if val is None:
                    continue
                try:
                    n = int(str(val).strip())
                    if n > max_num:
                        max_num = n
                except Exception:
                    continue
            
            current_num = max_num
            today_str = date.today().strftime("%d.%m.%Y")
            docs: List[Tuple[str, bytes]] = []
            cad_for_filename = cadnum.replace(":", " ")
            
            for suffix, rso_name, tpl_path in TEMPLATE_CONFIG:
                if not tpl_path.exists():
                    continue
                
                current_num += 1
                out_num_str = str(current_num)
                out_date_str = today_str
                
                new_row = ws.max_row + 1
                ws.cell(row=new_row, column=col_out_num, value=current_num)
                ws.cell(row=new_row, column=col_out_date, value=out_date_str)
                ws.cell(row=new_row, column=col_app_num, value=app_number)
                ws.cell(row=new_row, column=col_app_date, value=app_date)
                ws.cell(row=new_row, column=col_applicant, value=applicant)
                ws.cell(row=new_row, column=col_cadnum, value=cadnum)
                ws.cell(row=new_row, column=col_address, value=address)
                ws.cell(row=new_row, column=col_rso, value=rso_name)
                
                ctx = build_tu_context(cadnum, address, area, vri, app_number, app_date, out_num_str, out_date_str)
                content = _render_doc(tpl_path, ctx)
                filename = f"ТУ_{suffix}_{cad_for_filename}.docx"
                docs.append((filename, content))
            
            try:
                wb.save(TU_JOURNAL_PATH)
            except PermissionError:
                raise RuntimeError("❌ Не удалось сохранить журнал. Закройте журнал и повторите попытку.")
            except OSError as ex:
                raise RuntimeError(f"❌ Ошибка при сохранении журнала: {ex}")
    
    except Timeout:
        raise RuntimeError("⏳ Журнал сейчас используется другим процессом. Попробуйте через несколько секунд.")
    
    return docs
