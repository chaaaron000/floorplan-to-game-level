"""설치 확인. 두 번 돌린다.

1) 시스템 Python (측정 도구 plan_tools.py 용):
       python check_env.py
2) 백그라운드 Blender (빌더 plan_build.py 용):
       blender -b --python check_env.py

각 줄은 OK / MISSING 이고, 빠진 것이 있으면 종료 코드 1을 돌려준다.
"""
import importlib
import sys

try:
    import bpy  # noqa: F401
    IN_BLENDER = True
except ImportError:
    IN_BLENDER = False

NEED = (['bpy', 'numpy', 'PIL', 'shapely', 'ifcopenshell', 'ifcopenshell.api', 'bonsai']
        if IN_BLENDER else ['numpy', 'PIL'])
OPTIONAL = [] if IN_BLENDER else ['pymupdf']      # PDF 를 다룰 때만 필요

missing = []
print('환경:', 'Blender ' + bpy.app.version_string if IN_BLENDER else 'Python ' + sys.version.split()[0])
for name in NEED + OPTIONAL:
    try:
        importlib.import_module(name)
        print('OK       ', name)
    except Exception as e:      # noqa: BLE001
        tag = 'OPTIONAL ' if name in OPTIONAL else 'MISSING  '
        print(tag, name, '-', type(e).__name__)
        if name not in OPTIONAL:
            missing.append(name)
print('결과:', 'PASS' if not missing else 'FAIL (%s)' % ', '.join(missing))
sys.exit(1 if missing else 0)
