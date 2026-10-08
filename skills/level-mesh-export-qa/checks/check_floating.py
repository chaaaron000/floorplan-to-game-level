"""떠 있음: 바닥에 서 있어야 하는 메시(벽·기둥·붙박이)의 섬마다 가장 낮은 점이 바닥에서 얼마나 떴는지 잰다.

대상은 --floating-objects 정규식으로 고른다(천장·창·상부장처럼 원래 떠 있는 요소가 든 메시는 넣지 않는다).
바닥 높이는 --floor-z(없으면 manifest files[].floor_z_m, 그것도 없으면 0).

  blender -b --factory-startup --python check_floating.py -- --fbx level.fbx --floating-objects 'Walls|Columns|Casework' --out floating.json
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--floating-objects', help='바닥에 닿아 있어야 하는 메시 이름 정규식')
    ap.add_argument('--floor-z', type=float, help='바닥 윗면 높이(m)')
    ap.add_argument('--float-tol', type=float, default=0.01, help='떠 있음으로 치는 높이(m)')


def run(level, a):
    names = level.select(a.floating_objects, []) if a.floating_objects else []
    if not names:
        return {'floating': qa.skipped('--floating-objects 로 바닥에 닿아야 하는 메시를 골라야 한다')}
    floor_z = a.floor_z if a.floor_z is not None else level.floor_z
    rows = []
    for name in names:
        co, tri = level.world(name)
        lab = level.islands(name)
        used = np.unique(tri)
        roots, inv = np.unique(lab[used], return_inverse=True)
        zmin = np.full(len(roots), np.inf)
        zmax = np.full(len(roots), -np.inf)
        np.minimum.at(zmin, inv, co[used, 2])
        np.maximum.at(zmax, inv, co[used, 2])
        for i, root in enumerate(roots):
            c = co[int(root)]
            rows.append({'mesh': name, 'island_min_z_m': qa.r6(zmin[i]), 'island_max_z_m': qa.r6(zmax[i]),
                         'gap_m': qa.r6(zmin[i] - floor_z), 'floating': bool(zmin[i] - floor_z > a.float_tol), 'at_m': [qa.r6(c[0]), qa.r6(c[1])]})
    bad = [r for r in rows if r['floating']]
    return {'floating': {'meshes': names, 'floor_z_m': floor_z, 'tolerance_m': a.float_tol, 'islands': len(rows), 'floating': bad,
                         'count': len(bad), 'passed': not bad}}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
