"""메시 위상: 비매니폴드 모서리, 면적 0 삼각형, 뒤집힌 셸, 두 번째 UV. 고체 안의 빈 공간 셸과 두께 0 조각은 수만 남긴다.

삼각화한 뒤의 메시를 삼각형마다 국소 float64 외적으로 잰다(전역 좌표 float 면적은 얇은 삼각형에서 상쇄된다).
닫혀 있어야 하는 메시는 --closed 로 고른다(기본: 모든 메시). 재질 경계에서 일부러 열어 둔 렌더 메시가 있으면
충돌 메시만 --closed 로 고르고, 렌더 메시는 --aggregate 로 위치 기준 용접한 합본에 열린 가장자리가 없는지 본다.

  blender -b --factory-startup --python check_mesh_topology.py -- --fbx level.fbx [--closed '^COL_'] [--aggregate '^SM_'] --out topology.json
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--closed', help='닫혀 있어야 하는 메시 이름 정규식(기본: 전부)')
    ap.add_argument('--aggregate', help='위치 기준으로 용접해 합본 하나로 닫힘을 볼 메시 이름 정규식')
    ap.add_argument('--weld-m', type=float, default=1e-5, help='합본을 만들 때 같은 정점으로 보는 거리(m)')


def topology_stats(co, tri):
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    vol = float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0)
    return {'triangles': int(len(tri)), 'nonmanifold_edges': int((counts != 2).sum()), 'boundary_edges': int((counts == 1).sum()),
            'overconnected_edges': int((counts > 2).sum()), 'zero_area_triangles': int((area < 1e-15).sum()),
            'near_zero_area_triangles': int((area < 1e-10).sum()), 'min_triangle_area_m2': float(area.min()) if len(area) else None,
            'signed_volume_m3': qa.r6(vol)}


def classify_islands(co, tri, labels):
    """닫힌 메시의 섬을 부피 부호로 본다: (뒤집힌 셸 수, 빈 공간 셸 수, 두께 0 조각 수).
    두께 0 조각   |부피| ≤ 겉넓이 × 1µm. 두 면이 등을 맞댄 닫힌 조각으로, 고체 합치기가 남긴 찌꺼기다(판정하지 않고 수만 남긴다)
    빈 공간 셸    부피가 음수이고 그 섬의 한 점이 다른 섬들 안에 있다. 합친 고체 안의 빈 공간(벽·바닥·천장으로 둘러싸인 방 등)은 정상이다
    뒤집힌 셸    부피가 음수인데 다른 섬 안에 있지 않다(법선이 안쪽을 본다)"""
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    v = np.einsum('ij,ij->i', a, np.cross(b, c)) / 6.0
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    tl = labels[tri[:, 0]]
    roots, inv = np.unique(tl, return_inverse=True)
    vol = np.bincount(inv, weights=v, minlength=len(roots))
    flat = np.abs(vol) <= np.bincount(inv, weights=area, minlength=len(roots)) * 1e-6
    inverted = cavities = 0
    for root in roots[(vol < 0) & ~flat]:
        others = tri[tl != root]
        if len(others) and qa.point_inside(qa.bvh_of(co, others), co[tri[tl == root][0, 0]]):
            cavities += 1
        else:
            inverted += 1
    return inverted, cavities, int(flat.sum())


def welded(level, names, weld):
    cos, tris, off = [], [], 0
    for n in names:
        co, tri = level.world(n)
        cos.append(co)
        tris.append(tri + off)
        off += len(co)
    co, tri = np.concatenate(cos), np.concatenate(tris)
    _, first, inv = np.unique(np.round(co / weld).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    tri = inv.reshape(-1)[tri]
    tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])]
    return co[first], tri


def run(level, a):
    closed = set(level.select(a.closed, level.meshes))
    per, inward, cavities, flat = {}, {}, {}, {}
    for n in sorted(level.meshes):
        co, tri = level.world(n)
        per[n] = topology_stats(co, tri)
        per[n]['must_be_closed'] = n in closed
        if n in closed and per[n]['nonmanifold_edges'] == 0:
            k, hollow, thin = classify_islands(co, tri, level.islands(n))
            if k:
                inward[n] = k
            if hollow:
                cavities[n] = hollow
            if thin:
                flat[n] = thin
    rep = {'meshes': len(per), 'closed_meshes_checked': len(closed),
           'nonmanifold_edges_total': int(sum(per[n]['nonmanifold_edges'] for n in closed)),
           'open_by_design': {n: per[n]['boundary_edges'] for n in per if n not in closed and per[n]['nonmanifold_edges']},
           'zero_area_triangles_total': int(sum(t['zero_area_triangles'] for t in per.values())),
           'near_zero_area_triangles_total': int(sum(t['near_zero_area_triangles'] for t in per.values())),
           'inward_islands': inward, 'enclosed_cavity_shells': cavities, 'zero_thickness_shells': flat, 'per_mesh': per}
    ok = rep['nonmanifold_edges_total'] == 0 and rep['zero_area_triangles_total'] == 0 and not inward
    if a.aggregate:
        names = level.select(a.aggregate, [])
        if names:
            rep['welded_aggregate'] = dict(topology_stats(*welded(level, names, a.weld_m)), meshes=names, weld_m=a.weld_m)
            # 위치로 용접하면 고체끼리 모서리에서 맞닿은 곳이 면 4장짜리 모서리가 된다. 열린 가장자리가 없는지만 판정한다
            ok = ok and rep['welded_aggregate']['boundary_edges'] == 0
        else:
            rep['welded_aggregate'] = {'meshes': [], 'note': '--aggregate 와 맞는 메시가 없다'}
            ok = False
    rep['passed'] = bool(ok)

    render = level.render_names
    uv2 = {'render_meshes': len(render), 'with_two_channels': sum(1 for n in render if len(level.meshes[n].data.uv_layers) >= 2),
           'missing': [n for n in render if len(level.meshes[n].data.uv_layers) < 2]}
    uv2['passed'] = bool(render) and not uv2['missing']
    return {'mesh_topology': rep, 'uv2': uv2}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
