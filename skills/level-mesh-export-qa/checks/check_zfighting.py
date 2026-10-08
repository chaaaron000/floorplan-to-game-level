"""z-fighting: 같은 방향을 보는 면이 같은 평면(거리 기준 이내)에서 겹친 넓이를 잰다.

축에 나란한 면(각도 기준 이내)은 방향·평면별로 묶어 투영한 뒤, 메시끼리의 겹침(합집합의 교집합)과
한 메시 안의 겹침(삼각형 넓이 합 − 합집합)을 잰다. 기운 면은 삼각형 쌍마다 그 평면 위에서 교집합 넓이를 잰다.
--zf-stack-pitch 를 주면 같은 모듈을 그 높이만큼 올려 쌓았을 때 원래 것과 겹치는 면을 참고 수치로 남긴다(반복층).

  blender -b --factory-startup --python check_zfighting.py -- --fbx level.fbx [--zf-stack-pitch 3.12] --out zf.json
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--zf-objects', help='검사할 메시 이름 정규식(기본: 렌더 메시 전부. 충돌 메시는 그리지 않으므로 뺀다)')
    ap.add_argument('--zf-angle', type=float, default=1.0, help='같은 방향으로 보는 각도(도)')
    ap.add_argument('--zf-dist', type=float, default=0.002, help='같은 평면으로 보는 거리(m)')
    ap.add_argument('--zf-min-area', type=float, default=1e-4, help='겹침으로 치는 넓이(m²)')
    ap.add_argument('--zf-stack-pitch', type=float, help='이 높이(m)만큼 올린 사본과의 겹침도 잰다')


def coplanar_scan(groups, angle_deg, dist, min_area, side=None):
    """groups: [(이름, co, tri)]. side(이름 → 0/1)가 있으면 서로 다른 쪽의 쌍만 센다(쌓기 검사)."""
    import shapely
    cos_lim = math.cos(math.radians(angle_deg))
    names = [g[0] for g in groups]
    by_dir = {}                 # (축, 부호) → [(메시 번호 배열, 평면 위치 배열, 삼각형 2D 배열)]
    tilted_n, tilted_p, tilted_o, total = [], [], [], 0
    for gi, (name, co, tri) in enumerate(groups):
        a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n, axis=1)
        ok = ln > 1e-12
        n, a, b, c = n[ok] / ln[ok, None], a[ok], b[ok], c[ok]
        total += len(n)
        axis = np.argmax(np.abs(n), axis=1)
        comp = n[np.arange(len(n)), axis]
        aligned = np.abs(comp) >= cos_lim
        t = ~aligned
        if t.any():
            tilted_n.append(n[t])
            tilted_p.append(np.stack([a[t], b[t], c[t]], axis=1))
            tilted_o.append(np.full(int(t.sum()), gi))
        offset = np.einsum('ij,ij->i', n, a) * np.sign(comp)
        for ax in range(3):
            uv = [k for k in range(3) if k != ax]
            for sgn in (-1, 1):
                sel = np.nonzero(aligned & (axis == ax) & (np.sign(comp) == sgn))[0]
                if len(sel):
                    by_dir.setdefault((ax, sgn), []).append((np.full(len(sel), gi), offset[sel], np.stack([a[sel][:, uv], b[sel][:, uv], c[sel][:, uv]], axis=1)))

    def counted(x, y):
        return side is None or side[x] != side[y]
    pairs = []
    for (ax, sgn), parts in by_dir.items():
        owner = np.concatenate([p[0] for p in parts])
        offset = np.concatenate([p[1] for p in parts])
        tris2 = np.concatenate([p[2] for p in parts])
        # 평면 묶음: 평면 위치를 줄 세워 dist 보다 벌어진 곳에서 끊는다(고정 격자로 나누면 같은 평면이 경계에서 갈린다)
        order = np.argsort(offset, kind='stable')
        cut = np.nonzero(np.diff(offset[order]) > dist)[0] + 1
        for idx in np.split(order, cut):
            owners = np.unique(owner[idx])
            if side is not None and len({side[names[o]] for o in owners}) < 2:
                continue
            where = qa.r6(float(offset[idx].mean()) * sgn)
            merged = {}
            for o in owners:
                polys = shapely.polygons(shapely.linearrings(tris2[idx[owner[idx] == o]]))
                u = shapely.union_all(polys, grid_size=1e-6)
                merged[names[o]] = u
                self_overlap = float(shapely.area(polys).sum() - u.area)
                if side is None and self_overlap > min_area:
                    pairs.append({'a': names[o], 'b': names[o], 'plane_axis': 'xyz'[ax], 'facing': sgn, 'plane_m': where, 'area_m2': qa.r6(self_overlap)})
            keys = sorted(merged)
            for i, x in enumerate(keys):
                for y in keys[i + 1:]:
                    if counted(x, y):
                        area = merged[x].intersection(merged[y]).area
                        if area > min_area:
                            pairs.append({'a': x, 'b': y, 'plane_axis': 'xyz'[ax], 'facing': sgn, 'plane_m': where, 'area_m2': qa.r6(area)})

    # 기운 면: 삼각형 쌍마다 그 평면의 기저에서 교집합 넓이(float64, 국소 좌표)
    nt = int(sum(len(n) for n in tilted_n))
    if nt:
        tn, tp, to = np.concatenate(tilted_n), np.concatenate(tilted_p), np.concatenate(tilted_o)
        lo, hi = tp.min(axis=1), tp.max(axis=1)
        acc = {}
        for i in range(nt - 1):
            j = np.nonzero((tn[i + 1:] @ tn[i]) >= cos_lim)[0] + i + 1
            if len(j):
                j = j[np.all(lo[j] <= hi[i] + dist, axis=1) & np.all(hi[j] >= lo[i] - dist, axis=1)]
            if len(j):
                j = j[np.max(np.abs((tp[j] - tp[i, 0]) @ tn[i]), axis=1) <= dist]
            if not len(j):
                continue
            u = tp[i, 1] - tp[i, 0]
            u = u / np.linalg.norm(u)
            basis = np.column_stack([u, np.cross(tn[i], u)])
            pa = shapely.Polygon((tp[i] - tp[i, 0]) @ basis)
            for k in j:
                x, y = names[to[i]], names[to[k]]
                if (side is not None and (x == y or not counted(x, y))):
                    continue
                area = pa.intersection(shapely.Polygon((tp[k] - tp[i, 0]) @ basis)).area
                if area > 1e-9:
                    key = tuple(sorted((x, y)))
                    acc[key] = acc.get(key, 0.0) + area
        for (x, y), area in acc.items():
            if area > min_area:
                pairs.append({'a': x, 'b': y, 'plane_axis': 'tilted', 'facing': 0, 'plane_m': None, 'area_m2': qa.r6(area)})
    pairs.sort(key=lambda e: -e['area_m2'])
    return {'triangles': total, 'axis_aligned_triangles': total - nt, 'tilted_triangles': nt, 'angle_deg': angle_deg, 'distance_m': dist,
            'min_overlap_m2': min_area, 'pairs': len(pairs), 'area_m2': qa.r6(sum(e['area_m2'] for e in pairs)), 'results': pairs[:50]}


def run(level, a):
    names = level.select(a.zf_objects, level.render_names)
    if not names:
        return {'zfighting': qa.skipped('검사할 렌더 메시가 없다')}
    groups = [(n, *level.world(n)) for n in names]
    zf = coplanar_scan(groups, a.zf_angle, a.zf_dist, a.zf_min_area)
    zf['meshes'] = len(names)
    zf['passed'] = zf['pairs'] == 0
    if a.zf_stack_pitch:
        # 쌓았을 때의 겹침은 쌓는 쪽이 한쪽 판을 빼야 하는 것이라 판정하지 않고 수치만 남긴다
        shift = np.array([0.0, 0.0, a.zf_stack_pitch])
        side = {n: 0 for n in names}
        side.update({n + '(+1)': 1 for n in names})
        st = coplanar_scan(groups + [(n + '(+1)', co + shift, tri) for n, co, tri in groups], a.zf_angle, a.zf_dist, a.zf_min_area, side)
        zf['stacked'] = {'pitch_m': a.zf_stack_pitch, 'pairs': st['pairs'], 'area_m2': st['area_m2'], 'results': st['results'],
                         'note': '판정하지 않는 참고 수치(같은 모듈을 쌓을 때 겹치는 면)'}
    return {'zfighting': zf}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
