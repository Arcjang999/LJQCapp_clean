"""Accept newly registered candidates without accepting changes to frozen inputs."""
import json


def can_refresh_registered_materials(previous, current):
    guard = previous.get('material_registration_guard')
    if not guard or guard != current.get('material_registration_guard'):
        return False
    before = previous.get('material_registration_rows', {})
    after = current.get('material_registration_rows', {})
    if not before or before.keys() != after.keys():
        return False
    for field, rows in before.items():
        latest = {row['id']: json.dumps(row, sort_keys=True, default=str) for row in after[field]}
        if any(latest.get(row['id']) != json.dumps(row, sort_keys=True, default=str) for row in rows):
            return False
    return True
