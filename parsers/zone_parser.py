"""
Заглушка парсера zone.xml.
Реализуем позже. Пока возвращаем None/пустые данные.
"""
def parse_zone_xml(xml_bytes: bytes):
    return {
        "code": None,
        "title": None,
        "act_ref": None,
        "vri_main": [],
        "vri_cond": [],
        "vri_aux": [],
        "params": {}
    }
