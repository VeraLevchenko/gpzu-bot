import re

with open('parsers/tab_parser.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Найдём и заменим функцию find_restrictions_for_parcel
old_code = '''            if check_geometry_intersects(parcel_poly, restr_geom):
                found.append({
                    "zone_type": restr.get("zone_type", ""),
                    "name": restr.get("name", ""),
                    "decision_number": restr.get("decision_number", ""),
                    "decision_date": restr.get("decision_date", ""),
                    "decision_authority": restr.get("decision_authority", ""),
                })'''

new_code = '''            if check_geometry_intersects(parcel_poly, restr_geom):
                found.append({
                    "zone_type": restr.get("zone_type", ""),
                    "name": restr.get("name", ""),
                    "registry_number": restr.get("registry_number", ""),
                    "decision_number": restr.get("decision_number", ""),
                    "decision_date": restr.get("decision_date", ""),
                    "decision_authority": restr.get("decision_authority", ""),
                })'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open('parsers/tab_parser.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ tab_parser.py обновлён - добавлен registry_number")
else:
    print("⚠️ Секция не найдена")
