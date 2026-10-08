"""FBX 재임포트: 빈 Blender 에 다시 들여와 개수·스케일·경계·삼각형·UV·재질을 확인한다.

manifest 가 있으면 files[].meshes 의 name / collision / triangles / bounds_m / materials 와 대조한다.
없으면 잰 수치만 남기고(스케일 1, 메시·empty 가 아닌 객체 0 만 판정), 기대 개수는 --expect-* 로 준다.

  blender -b --factory-startup --python check_fbx_roundtrip.py -- --fbx level.fbx [--manifest manifest.json] --out roundtrip.json
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--bounds-tol', type=float, default=1e-4, help='manifest 경계와의 허용 오차(m)')
    ap.add_argument('--allow-empties', action='store_true', help='manifest 가 있어도 FBX 안의 empty 를 허용한다(표식을 FBX 에도 넣는 규약)')
    ap.add_argument('--expect-render', type=int, help='기대하는 렌더 메시 수(manifest 가 없을 때)')
    ap.add_argument('--expect-collision', type=int, help='기대하는 충돌 메시 수(manifest 가 없을 때)')
    ap.add_argument('--expect-markers', action='append', default=[], metavar='PREFIX=N', help='기대하는 표식 수. 예: DOOR_=9')


def run(level, a):
    obs, meshes = level.objects, level.meshes
    empties = sorted(n for n, o in obs.items() if o.type == 'EMPTY')
    other = sorted(n for n, o in obs.items() if o.type not in ('MESH', 'EMPTY'))
    render, collision = level.render_names, level.collision_names
    rep = {'objects': len(obs), 'meshes': len(meshes), 'render_meshes': len(render), 'collision_meshes': len(collision),
           'empties': len(empties), 'other_objects': other,
           'markers_by_prefix': {p: sum(1 for m in level.markers if m['name'].startswith(p)) for p in sorted({m['name'].split('_')[0] + '_' for m in level.markers})}}
    scales, los, his = set(), [], []
    for name, ob in obs.items():
        scales.add(tuple(round(s, 6) for s in ob.matrix_world.to_scale()))
    for name in meshes:
        co, _ = level.world(name)
        los.append(co.min(axis=0))
        his.append(co.max(axis=0))
    lo, hi = np.min(los, axis=0), np.max(his, axis=0)
    rep.update({'object_world_scales': sorted(scales), 'world_bounds_m': [[qa.r6(v) for v in lo], [qa.r6(v) for v in hi]],
                'render_triangles': int(sum(len(level.world(n)[1]) for n in render)),
                'collision_triangles': int(sum(len(level.world(n)[1]) for n in collision))})
    ok = not other and all(abs(s - 1.0) < 1e-5 for sc in scales for s in sc)

    if level.entries:
        expected = level.entries
        worst, tri_mismatch, uv_missing, mat_mismatch = (0.0, None), [], [], []
        for name in meshes:
            e = expected.get(name)
            if e is None:
                continue
            co, tri = level.world(name)
            if 'bounds_m' in e:
                err = float(max(np.abs(co.min(axis=0) - e['bounds_m'][0]).max(), np.abs(co.max(axis=0) - e['bounds_m'][1]).max()))
                if err > worst[0]:
                    worst = (err, name)
            if 'triangles' in e and len(tri) != e['triangles']:
                tri_mismatch.append([name, int(len(tri)), e['triangles']])
            if not level.is_collision(name):
                if len(meshes[name].data.uv_layers) < 2:
                    uv_missing.append(name)
                mats = [m.name for m in meshes[name].data.materials if m]
                if 'materials' in e and mats != e['materials']:
                    mat_mismatch.append([name, mats, e['materials']])
        rep.update({'compared_with': 'manifest', 'missing': sorted(set(expected) - set(meshes)), 'extra': sorted(set(meshes) - set(expected)),
                    'bounds_max_error_m': qa.r6(worst[0]), 'bounds_worst_mesh': worst[1], 'triangle_mismatch': tri_mismatch,
                    'uv2_missing': uv_missing, 'material_mismatch': mat_mismatch})
        ok = (ok and not rep['missing'] and not rep['extra'] and worst[0] <= a.bounds_tol and not tri_mismatch and not uv_missing
              and not mat_mismatch and (a.allow_empties or not empties))
    else:
        rep['compared_with'] = 'expected counts' if (a.expect_render is not None or a.expect_collision is not None or a.expect_markers) else 'nothing (measured only)'
        rep['uv2_missing'] = [n for n in render if len(meshes[n].data.uv_layers) < 2]
        mismatch = []
        if a.expect_render is not None and a.expect_render != len(render):
            mismatch.append(['render_meshes', len(render), a.expect_render])
        if a.expect_collision is not None and a.expect_collision != len(collision):
            mismatch.append(['collision_meshes', len(collision), a.expect_collision])
        for item in a.expect_markers:
            prefix, n = item.split('=')
            got = sum(1 for m in level.markers if m['name'].startswith(prefix))
            if got != int(n):
                mismatch.append([prefix, got, int(n)])
        rep['count_mismatch'] = mismatch
        ok = ok and not mismatch and not rep['uv2_missing']
    rep['passed'] = bool(ok)
    return {'fbx_roundtrip': rep}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
