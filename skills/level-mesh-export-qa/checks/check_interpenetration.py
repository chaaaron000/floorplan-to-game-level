"""서로 파고듦: 경계 상자가 겹치는 닫힌 메시 쌍마다 교차 부피를 잰다(Blender Boolean, EXACT, INTERSECT).

삼각형이 맞닿지도 않고 한쪽이 다른 쪽 안에 들어 있지도 않은 쌍은 재지 않는다. 문짝끼리는 보지 않는다(문짝 대 고정 지형만).
FBX 는 float32 라 맞닿은 면에서도 (맞닿은 넓이 × 좌표 반올림, 16m 안쪽에서 약 1µm)만큼의 부피가 나온다. 기준(1e-7 m³) 근처의 값은 그 크기와 견줘 읽는다.
대상 메시는 닫혀 있어야 한다. 재질 경계에서 열어 둔 렌더 메시를 쓰는 규약이면 --objects 로 충돌 메시를 고른다.

  blender -b --factory-startup --python check_interpenetration.py -- --fbx level.fbx [--manifest manifest.json] [--objects '^COL_'] --out pen.json
"""
import os
import sys

import bmesh
import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--pen-objects', '--objects', dest='pen_objects', help='검사할 메시 이름 정규식(기본: 렌더 메시 전부)')
    ap.add_argument('--pen-tol', type=float, default=1e-7, help='파고듦으로 치는 교차 부피(m³)')


def intersection_volume(a, b):
    tmp = a.copy()
    tmp.data = a.data.copy()
    bpy.context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new('qa', 'BOOLEAN')
    mod.operation, mod.solver, mod.object = 'INTERSECT', 'EXACT', b
    mod.use_self = True         # 섬끼리 겹친 메시(문짝에 박힌 손잡이 등)에서 겹친 부분이 교차 부피로 잘못 나오지 않게
    dg = bpy.context.evaluated_depsgraph_get()
    ev = tmp.evaluated_get(dg)
    bm = bmesh.new()
    bm.from_mesh(ev.to_mesh())
    bm.transform(tmp.matrix_world)
    vol = abs(bm.calc_volume(signed=True))
    bm.free()
    ev.to_mesh_clear()
    data = tmp.data
    bpy.data.objects.remove(tmp)
    bpy.data.meshes.remove(data)
    return vol


def run(level, a):
    names = level.select(a.pen_objects, level.render_names)
    if len(names) < 2:
        return {'interpenetration': qa.skipped('검사할 메시가 2개 미만이다')}
    leaves = set(level.leaf_names) | {n for n in names if n.startswith(level.prefix) and n not in level.static_collision_names}
    statics = [n for n in names if n not in leaves]
    movers = [n for n in names if n in leaves]
    pairs = [(x, y) for i, x in enumerate(statics) for y in statics[i + 1:]] + [(m, s) for m in movers for s in statics]
    bvhs, tested, results = {}, 0, []

    def bvh(n):
        if n not in bvhs:
            bvhs[n] = qa.bvh_of(*level.world(n))
        return bvhs[n]
    for x, y in pairs:
        cx, cy = level.world(x)[0], level.world(y)[0]
        if np.any(cx.max(axis=0) < cy.min(axis=0) - 1e-6) or np.any(cy.max(axis=0) < cx.min(axis=0) - 1e-6):
            continue
        tested += 1
        touching = bool(bvh(x).overlap(bvh(y)))
        if not touching and not (qa.point_inside(bvh(y), cx[0]) or qa.point_inside(bvh(x), cy[0])):
            continue
        vol = intersection_volume(level.meshes[x], level.meshes[y])
        row = {'a': x, 'b': y, 'triangle_contact': touching, 'intersection_volume_m3': vol}
        if vol > a.pen_tol:
            # A∩B 와 B∩A 는 같아야 한다. 불리언이 맞닿은 면에서 조각을 남기는 일이 있어, 기준을 넘으면 순서를 바꿔 다시 재고 작은 쪽을 쓴다
            other = intersection_volume(level.meshes[y], level.meshes[x])
            row.update({'intersection_volume_m3': min(vol, other), 'volume_a_into_b_m3': vol, 'volume_b_into_a_m3': other})
        row['penetrates'] = row['intersection_volume_m3'] > a.pen_tol
        results.append(row)
    bad = [r for r in results if r['penetrates']]
    return {'interpenetration': {
        'meshes': len(names), 'static': len(statics), 'movable': len(movers), 'pairs_bbox_overlap': tested, 'pairs_measured': len(results),
        'tolerance_m3': a.pen_tol, 'pairs_above_tolerance': len(bad), 'penetrating_pairs': sorted(bad, key=lambda r: -r['intersection_volume_m3']),
        'max_contact_volume_m3': max([r['intersection_volume_m3'] for r in results], default=0.0), 'passed': not bad}}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
