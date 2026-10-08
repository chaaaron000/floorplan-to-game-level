"""천장고: 방 표식마다 위로 광선을 쏘아 천장 메시까지의 높이가 기대값과 맞는지 잰다.

기대값은 표식 속성 ceiling_height_m(없으면 height_m), 그것도 없으면 --ceiling-expected.
바닥이 없는 공간(floor_kind 가 --skip-floor-kinds 에 든 것, 예: 승강로)은 재지 않는다.

  blender -b --factory-startup --python check_ceiling_height.py -- --fbx level.fbx --manifest manifest.json [--ceiling-objects Ceiling] --out ceiling.json
"""
import os
import sys

from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--ceiling-objects', default='Ceiling', help='천장 메시 이름 정규식')
    ap.add_argument('--ceiling-expected', type=float, help='표식에 기대 천장고가 없을 때 쓰는 값(m)')
    ap.add_argument('--ceiling-tol', type=float, default=0.002, help='허용 오차(m)')
    ap.add_argument('--ceiling-ray-offset', type=float, default=0.01, help='표식 위 이만큼에서 쏜다(m)')


def run(level, a):
    rooms = level.markers_with(a.room_prefix)
    names = level.select(a.ceiling_objects, [])
    if not rooms or not names:
        return {'ceiling_height': qa.skipped('방 표식(%d) 또는 천장 메시(%d)가 없다' % (len(rooms), len(names)))}
    bvhs = [qa.bvh_of(*level.world(n)) for n in names]
    skip = {s for s in a.skip_floor_kinds.split(',') if s}
    rows, no_expect = [], []
    for m in rooms:
        if qa.prop(m, 'floor_kind') in skip:
            rows.append({'room': m['name'], 'skipped': 'floor_kind=%s' % qa.prop(m, 'floor_kind'), 'ok': None})
            continue
        expected = qa.prop(m, 'ceiling_height_m', 'height_m', default=a.ceiling_expected)
        x, y, z = m['position']
        origin = Vector((x, y, z + a.ceiling_ray_offset))
        hits = [h[0].z for h in (b.ray_cast(origin, Vector((0.0, 0.0, 1.0)), 100.0) for b in bvhs) if h[0] is not None]
        measured = (min(hits) - z) if hits else None
        if expected is None:
            no_expect.append(m['name'])
        ok = bool(expected is not None and measured is not None and abs(measured - float(expected)) <= a.ceiling_tol)
        rows.append({'room': m['name'], 'expected_m': None if expected is None else float(expected),
                     'measured_m': None if measured is None else qa.r6(measured), 'ok': ok})
    measured = [r for r in rows if r['ok'] is not None]
    failures = [r for r in measured if not r['ok']]
    return {'ceiling_height': {'ceiling_meshes': names, 'tolerance_m': a.ceiling_tol, 'room_markers': len(rooms), 'measured_rooms': len(measured),
                               'skipped_rooms': [r['room'] for r in rows if r['ok'] is None], 'no_expected_height': no_expect,
                               'failures': failures, 'rooms': rows, 'passed': bool(measured) and not failures}}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
