# generator/tu_requests_builder.py
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from docxtpl import DocxTemplate

from parsers.egrn_parser import EGRNData


# Путь к папке с шаблонами
BASE_DIR = Path(__file__).resolve().parents[1]
TU_TEMPLATES_DIR = BASE_DIR / "templates" / "tu"

# Точное соответствие ожидаемых шаблонов
TEMPLATE_CONFIG = [
    ("Водоканал", TU_TEMPLATES_DIR / "Водоканал.docx"),
    ("Газоснабжение", TU_TEMPLATES_DIR / "Газоснабжение.docx"),
    ("Теплоснабжение", TU_TEMPLATES_DIR / "Теплоснабжение.docx"),
]


def _format_area(area: Optional[str]) -> str:
    """Аккуратное форматирование площади."""
    if not area:
        return ""
    s = area.strip().replace(",", ".")
    if s.endswith(".0"):
        s = s[:-2]
    return s


def build_tu_context(egrn: EGRNData, incoming: str) -> Dict[str, str]:
    """Контекст для подстановки в шаблоны."""
    return {
        "INCOMING": incoming or "",
        "CADNUM": egrn.cadnum or "",
        "AREA": _format_area(egrn.area),
        "VRI": egrn.permitted_use or "",
        "ADDRESS": egrn.address or "",
    }


def _render_doc(template_path: Path, context: Dict[str, str]) -> bytes:
    """Рендер DOCX по шаблону."""
    tpl = DocxTemplate(str(template_path))
    tpl.render(context)
    bio = BytesIO()
    tpl.save(bio)
    return bio.getvalue()


def build_tu_docs(egrn: EGRNData, incoming: str) -> List[Tuple[str, bytes]]:
    """
    Формируем три документа по фиксированным шаблонам.
    Возвращаем список [(filename, bytes), ...].
    Те шаблоны, которых нет — пропускаем, но не падаем.
    """
    ctx = build_tu_context(egrn, incoming)
    docs: List[Tuple[str, bytes]] = []

    cad = (egrn.cadnum or "no_cad").replace(":", " ")

    for suffix, tpl_path in TEMPLATE_CONFIG:
        if not tpl_path.exists():
            # Логически корректно — если шаблона нет, просто не выводим этот файл
            continue

        content = _render_doc(tpl_path, ctx)
        filename = f"ТУ_{suffix}_{cad}.docx"
        docs.append((filename, content))

    return docs
