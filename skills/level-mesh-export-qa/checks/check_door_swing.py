"""문 최대 열림 각도: 여닫이 문짝이 고정 지형(벽·기둥·붙박이·유리·창틀·계단)에 닿지 않고 열리는 최대 각을 잰다.

문짝마다 경첩에서 자유 끝까지의 상자를 평면에서 돌려, 문짝 높이 구간에 걸친 고정 지형의 평면과 겹치기 직전의 각을 구한다.
  문 표식  hinges_world_json = [[경첩x, 경첩y, 자유끝x, 자유끝y], ...](문짝 순서), swing = out|in(표식 로컬 +Y 쪽으로 열리면 out), width_m
  문짝     경첩에 맞는 문짝 렌더 메시에서 두께·높이 구간을 잰다(닫힌 상태로 월드 좌표에 놓여 있어야 한다)
  고정 지형 닫힌 메시의 섬마다 문짝 높이 구간 안의 수평 단면을 합친 것(각기둥 모양이면 정확하고, 기운 면은 구간마다 세 높이를 본다)
문짝 상자는 조금 줄여서 본다(--swing-shrink 자유 끝, 면, 위아래, 경첩 쪽 추가분). 경첩 쪽은 문짝 두께 + 추가분 + 자유 끝 분을 뺀다
(경첩이 문틀 속에 있어 뒤꿈치가 문틀과 1~2cm 겹치는 것은 걸림으로 치지 않는다). 엔진 쪽 문 검사가 있으면 같은 값으로 맞춘다.

판정: 닫힌 상태에서 이미 겹친 문짝 0. --min-open-deg / --min-clear-width 를 주면 그 기준도 본다.
통과 폭 = 개구부 폭 − Σ(문짝 길이·cosθ + 두께·sinθ), θ = 최대 열림 각.

  blender -b --factory-startup --python check_door_swing.py -- --fbx level.fbx --manifest manifest.json [--min-clear-width 0.68 --door-require access=passage] --out doors.json
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--swing-obstacles', help='고정 지형으로 볼 닫힌 메시 이름 정규식(기본: 문짝이 아닌 충돌 메시, 없으면 문짝이 아닌 렌더 메시)')
    ap.add_argument('--swing-step', type=float, default=0.1, help='각도 해상도(도)')
    ap.add_argument('--swing-limit', type=float, default=90.0, help='이 각까지만 연다(도)')
    ap.add_argument('--swing-shrink', default='0.02,0.005,0.06,0.02', help='문짝 상자를 줄이는 양(m): 자유 끝, 면마다, 위아래, 경첩 쪽 추가분')
    ap.add_argument('--swing-hit-area', type=float, default=1e-6, help='겹침으로 치는 넓이(m²)')
    ap.add_argument('--swing-leaf-centered', action='store_true',
                    help='문짝이 경첩선(경첩→자유 끝) 가운데에 놓였다고 보고 잰다. 기본은 문짝 메시가 실제로 놓인 자리')
    ap.add_argument('--min-open-deg', type=float, help='모든 여닫이 문이 이 각 이상 열려야 한다')
    ap.add_argument('--min-clear-width', type=float, help='요구하는 문(--door-require)은 최대로 연 채 이 폭 이상 남아야 한다(m)')


class Obstacles:
    """닫힌 메시의 섬마다 z 구간 [zlo, zhi] 에 걸친 평면 도형. 필요한 섬만 만들어 둔다."""

    def __init__(self, level, names):
        self.level, self.items, self.cache = level, [], {}
        for n in names:
            co, tri = level.world(n)
            lab = level.islands(n)
            tl = lab[tri[:, 0]]
            order = np.argsort(tl, kind='stable')
            tri, tl = tri[order], tl[order]
            cuts = np.nonzero(np.r_[True, tl[1:] != tl[:-1], True])[0]
            for i0, i1 in zip(cuts[:-1], cuts[1:]):
                t = tri[i0:i1]
                p = co[np.unique(t)]
                self.items.append({'mesh': n, 'island': int(tl[i0]), 'tri': t, 'co': co, 'lab': lab, 'lo': p.min(axis=0), 'hi': p.max(axis=0)})
        self.lo = np.array([it['lo'] for it in self.items])
        self.hi = np.array([it['hi'] for it in self.items])

    def footprint(self, i, zlo, zhi):
        import shapely
        key = (i, round(zlo, 4), round(zhi, 4))
        if key not in self.cache:
            it = self.items[i]
            co, t = it['co'], it['tri']
            zs = np.unique(np.round(co[np.unique(t), 2], 6))
            cuts = [zlo] + [z for z in zs if zlo + 1e-6 < z < zhi - 1e-6] + [zhi]
            n = np.cross(co[t[:, 1]] - co[t[:, 0]], co[t[:, 2]] - co[t[:, 0]])
            ln = np.linalg.norm(n, axis=1)
            nz = np.abs(n[ln > 1e-12, 2] / ln[ln > 1e-12])
            prismatic = bool(np.all((nz < 1e-6) | (nz > 1 - 1e-6)))
            fracs = (0.5,) if prismatic else (1 / 6, 0.5, 5 / 6)
            parts = []
            for z0, z1 in zip(cuts[:-1], cuts[1:]):
                for f in fracs:
                    parts += [g for _, g in qa.section_parts(co, t, z0 + (z1 - z0) * f, it['lab'])]
            self.cache[key] = shapely.union_all(parts) if parts else None
        return self.cache[key]

    def near(self, x, y, reach, zlo, zhi):
        """경첩 (x, y)에서 reach 안에 경계 상자가 걸치고 z 구간이 겹치는 섬의 (번호, 평면 도형)"""
        dx = np.maximum(np.maximum(self.lo[:, 0] - x, x - self.hi[:, 0]), 0.0)
        dy = np.maximum(np.maximum(self.lo[:, 1] - y, y - self.hi[:, 1]), 0.0)
        m = (np.hypot(dx, dy) <= reach) & (self.lo[:, 2] < zhi - 1e-6) & (self.hi[:, 2] > zlo + 1e-6)
        out = []
        for i in np.nonzero(m)[0]:
            g = self.footprint(int(i), zlo, zhi)
            if g is not None and not g.is_empty:
                out.append((int(i), g))
        return out


def find_leaf(level, hinge, used):
    """경첩에 맞는 문짝 렌더 메시: manifest 의 pivot_m 이 경첩과 2mm 안이거나, 메시 평면 경계의 가운데가 문짝 중점에 가장 가까운 것."""
    hx, hy, ex, ey = hinge
    best = (None, 1e9)
    for n in level.leaf_names:
        if n in used:
            continue
        e = level.entries.get(n)
        if e is not None and 'pivot_m' in e:
            d = math.hypot(e['pivot_m'][0] - hx, e['pivot_m'][1] - hy)
            if d <= 0.002:
                return n
            continue
        co, _ = level.world(n)
        c = (co.min(axis=0) + co.max(axis=0)) / 2
        d = math.hypot(c[0] - (hx + ex) / 2, c[1] - (hy + ey) / 2)
        if d < best[1]:
            best = (n, d)
    return best[0] if best[1] < 0.10 else None


def max_open(box, pivot, sign, hits_of, limit, step):
    """(최대 각, 처음 닿는 것, 닫힌 상태에서 닿는 것)"""
    import shapely.affinity as sa

    def hit(deg):
        return hits_of(sa.rotate(box, sign * deg, origin=pivot))
    closed = hit(0.0)
    deg = 0.0
    while deg < limit - 1e-9:
        nxt = min(limit, deg + 1.0)
        if hit(nxt):
            lo, hi = deg, nxt
            while hi - lo > step / 2:
                mid = (lo + hi) / 2
                if hit(mid):
                    hi = mid
                else:
                    lo = mid
            return math.floor(lo / step + 1e-9) * step, hit(hi), closed
        deg = nxt
    return limit, None, closed


def run(level, a):
    import shapely
    import shapely.geometry as sg
    doors = [m for m in level.markers_with(a.door_prefix) if qa.prop(m, 'hinges_world_json') and qa.prop(m, 'kind', default='hinged') == 'hinged'
             and json.loads(qa.prop(m, 'hinges_world_json'))]
    if not doors:
        return {'door_swing': qa.skipped('hinges_world_json 속성이 있는 여닫이 문 표식이 없다')}
    names = level.select(a.swing_obstacles, level.static_collision_names or level.static_names)
    if not names:
        return {'door_swing': qa.skipped('고정 지형으로 쓸 메시가 없다')}
    s_end, s_face, s_z, s_hinge = [float(v) for v in a.swing_shrink.split(',')]
    obstacles = Obstacles(level, names)
    require = qa.door_requirements(a)
    rows, used = [], set()
    for m in doors:
        yaw = m['yaw']
        swing = qa.prop(m, 'swing')
        local_y = (-math.sin(yaw), math.cos(yaw))
        sides = {'out': [local_y], 'in': [(-local_y[0], -local_y[1])]}.get(swing, [local_y, (-local_y[0], -local_y[1])])
        leaves = []
        for hinge in json.loads(qa.prop(m, 'hinges_world_json')):
            hx, hy, ex, ey = [float(v) for v in hinge[:4]]
            L = math.hypot(ex - hx, ey - hy)
            ux, uy = (ex - hx) / L, (ey - hy) / L
            leaf = find_leaf(level, (hx, hy, ex, ey), used)
            if leaf is None:
                leaves.append({'hinge_m': [hx, hy], 'free_end_m': [ex, ey], 'error': '경첩에 맞는 문짝 메시가 없다'})
                continue
            used.add(leaf)
            co, _ = level.world(leaf)
            rel = co[:, :2] - np.array([hx, hy])
            row = {'leaf_mesh': leaf, 'hinge_m': [qa.r6(hx), qa.r6(hy)], 'free_end_m': [qa.r6(ex), qa.r6(ey)], 'leaf_length_m': qa.r6(L)}
            zlo, zhi = float(co[:, 2].min()) + s_z, float(co[:, 2].max()) - s_z
            for sx, sy in sides:
                b = rel @ np.array([sx, sy])
                thick = float(b.max() - b.min())
                a0, a1 = min(thick + s_hinge + s_end, L), L - s_end
                if a.swing_leaf_centered:
                    b0, b1 = -thick / 2 + s_face, thick / 2 - s_face
                else:
                    b0, b1 = float(b.min()) + s_face, float(b.max()) - s_face
                box = sg.Polygon([(hx + ux * p + sx * q, hy + uy * p + sy * q) for p, q in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))])
                sign = 1.0 if (ex - hx) * sy - (ey - hy) * sx > 0 else -1.0
                near = obstacles.near(hx, hy, L + thick + 0.05, zlo, zhi)
                solid = shapely.union_all([g for _, g in near]) if near else None

                def hits_of(g, near=near, solid=solid):
                    # 겹친 넓이는 근처 고정 지형 전체의 합집합으로 잰다(메시를 어떻게 나눴는지에 따라 값이 달라지지 않게)
                    if solid is None or g.intersection(solid).area <= a.swing_hit_area:
                        return None
                    area, i = max((g.intersection(poly).area, i) for i, poly in near)
                    c = g.intersection(solid).representative_point()
                    return {'mesh': obstacles.items[i]['mesh'], 'at_m': [qa.r6(c.x), qa.r6(c.y)], 'overlap_m2': float('%.3g' % g.intersection(solid).area)}
                best, blocker, closed = max_open(box, (hx, hy), sign, hits_of, a.swing_limit, a.swing_step)
                res = {'rotation_sign': int(sign), 'max_open_deg': round(best, 1), 'blocker': blocker, 'closed_overlap': closed is not None}
                if len(sides) == 1:
                    row.update(res)
                else:
                    row.setdefault('directions', []).append(res)
            row.update({'leaf_thickness_m': qa.r6(thick), 'z_range_m': [qa.r6(zlo), qa.r6(zhi)]})
            leaves.append(row)
        known = [l for l in leaves if 'max_open_deg' in l]
        width = qa.prop(m, 'width_m', 'door_width')
        entry = {'door': m['name'], 'swing': swing, 'required': qa.door_required(m, require), 'leaves': leaves,
                 'max_open_deg': min((l['max_open_deg'] for l in known), default=None) if len(known) == len(leaves) else None}
        if width is not None and entry['max_open_deg'] is not None:
            entry['width_m'] = float(width)
            entry['clear_width_at_max_open_m'] = qa.r6(float(width) - sum(
                l['leaf_length_m'] * math.cos(math.radians(l['max_open_deg'])) + l['leaf_thickness_m'] * math.sin(math.radians(l['max_open_deg'])) for l in known))
        rows.append(entry)

    measured = [r for r in rows if r['max_open_deg'] is not None]
    errors = [r['door'] for r in rows if any('error' in l for l in r['leaves'])]
    closed_overlap = [r['door'] for r in rows if any(l.get('closed_overlap') for l in r['leaves'])]
    limited = sorted([{'door': r['door'], 'max_open_deg': r['max_open_deg'], 'blockers': sorted({l['blocker']['mesh'] for l in r['leaves'] if l.get('blocker')})}
                      for r in measured if r['max_open_deg'] < a.swing_limit], key=lambda e: e['max_open_deg'])
    rep = {'obstacle_meshes': names, 'obstacle_islands': len(obstacles.items), 'limit_deg': a.swing_limit, 'step_deg': a.swing_step,
           'leaf_box': 'centred on hinge line' if a.swing_leaf_centered else 'as placed in the leaf mesh',
           'shrink_m': {'free_end': s_end, 'face': s_face, 'top_bottom': s_z, 'hinge_extra': s_hinge},
           'hinged_doors': len(rows), 'measured': len(measured), 'leaves': sum(len(r['leaves']) for r in rows),
           'full_open': sum(1 for r in measured if r['max_open_deg'] >= a.swing_limit), 'limited': limited,
           'unknown_swing_direction': [r['door'] for r in rows if r['swing'] not in ('in', 'out')], 'leaf_mesh_missing': errors,
           'closed_overlap': closed_overlap, 'doors': rows}
    ok = bool(measured) and not closed_overlap and not errors
    if a.min_open_deg is not None:
        rep['below_min_open_deg'] = [r['door'] for r in measured if r['max_open_deg'] < a.min_open_deg]
        ok = ok and not rep['below_min_open_deg']
    if a.min_clear_width is not None:
        rep['clear_width_failures'] = [{'door': r['door'], 'clear_width_at_max_open_m': r['clear_width_at_max_open_m'], 'max_open_deg': r['max_open_deg']}
                                       for r in measured if r['required'] and r.get('clear_width_at_max_open_m') is not None
                                       and r['clear_width_at_max_open_m'] < a.min_clear_width]
        ok = ok and not rep['clear_width_failures']
    rep['passed'] = ok
    return {'door_swing': rep}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
