# Найдём и обновим строку в spatial_analysis.py
import fileinput
import sys

filename = 'utils/spatial_analysis.py'

# Читаем файл
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# Заменяем секцию создания RestrictionZone
old_code = '''            restriction = RestrictionZone(
                zone_type=restr_dict.get('zone_type', zone_type),
                name=restr_dict.get('name'),
                decision_number=restr_dict.get('decision_number'),
                decision_date=restr_dict.get('decision_date'),
                decision_authority=restr_dict.get('decision_authority'),
            )'''

new_code = '''            restriction = RestrictionZone(
                zone_type=restr_dict.get('zone_type', zone_type),
                name=restr_dict.get('name'),
                registry_number=restr_dict.get('registry_number'),
                decision_number=restr_dict.get('decision_number'),
                decision_date=restr_dict.get('decision_date'),
                decision_authority=restr_dict.get('decision_authority'),
            )'''

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ spatial_analysis.py обновлён")
else:
    print("⚠️ Секция не найдена, возможно уже обновлено")
