"""도달 가능성: 충돌 메시 위에서 캡슐(반지름·높이)이 설 수 있는 칸을 격자로 구하고, 시작점에서 걸어서 닿는 범위를 잰다.

충돌 메시(닫힌 메시)를 격자 기둥마다 고체 구간으로 바꾼 뒤:
  설 자리   위로 캡슐 높이만큼 빈 윗면
  막힌 칸   그 높이에서 (+단 높이, +캡슐 높이) 사이에 고체가 있거나, ±단 높이 안에 디딜 면이 없는 칸(낭떠러지)
  여유      캡슐 중심은 막힌 칸 중심에서 반지름 + 반 칸 이상 떨어져야 한다
  연결      이웃 칸(4방향)의 높이 차가 단 높이 이하이고 겹친 머리 공간이 캡슐 높이 이상
캡슐 반지름·높이·단 높이는 게임의 캐릭터·내비 설정에서 가져와 인자로 준다(기본값 없음). 문짝은 넣지 않는다(문은 열린다고 본다).
계단으로 층이 이어지는지는 --stack 으로 같은 모듈을 위아래로 쌓아 본다(--pitch 층 높이).

판정: 시작점이 한 성분에 있음, 방 표식(바닥 없는 공간 제외)이 모두 시작 성분의 설 자리 근처, 문 표식의 개구부에 시작 성분의 칸이 있음,
쌓았을 때 위·아래 층과 이어짐, --block-rect 로 한 경로를 막아도 위층과 이어짐, 충돌 메시가 닫혀 있음(고체 구간의 시작·끝이 짝이 맞음).

  blender -b --factory-startup --python check_reachability.py -- --fbx level.fbx --manifest manifest.json \
      --capsule-radius 0.34 --capsule-height 1.92 --step-height 0.45 --seed-marker ROOM_Stair \
      [--stack=-1,0,1 --pitch 3.12] [--block-rect lane_b:-3.15,-6.97,-1.94,-2.73] [--nav-png nav.png] --out nav.json
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--nav-collision', help='걷는 면·장애물로 쓸 닫힌 메시 이름 정규식(기본: 문짝이 아닌 충돌 메시, 없으면 문짝이 아닌 렌더 메시)')
    ap.add_argument('--capsule-radius', type=float, help='캡슐(내비 에이전트) 반지름(m)')
    ap.add_argument('--capsule-height', type=float, help='캡슐 높이(m)')
    ap.add_argument('--step-height', type=float, help='한 번에 오르내릴 수 있는 단 높이(m)')
    ap.add_argument('--cell', type=float, default=0.05, help='격자 칸 크기(m)')
    ap.add_argument('--stack', default='0', help='쌓을 층 번호(쉼표). 예: -1,0,1')
    ap.add_argument('--pitch', type=float, help='층 높이(m). 없으면 manifest files[].storey_pitch_m')
    ap.add_argument('--seed-marker', help='시작점으로 쓸 표식 이름(계단실·승강기 홀처럼 플레이어가 들어오는 곳)')
    ap.add_argument('--seed', help='시작점 좌표 x,y,z(m)')
    ap.add_argument('--marker-tolerance', type=float, help='방 표식에서 시작 성분의 설 자리까지 허용 거리(m). 기본: 캡슐 반지름')
    ap.add_argument('--door-zone-depth', type=float, default=0.10, help='문 개구부로 보는 깊이(m, 벽 두께 방향). 표식에 depth_m 속성이 있으면 그 값을 쓴다')
    ap.add_argument('--block-rect', action='append', default=[], metavar='NAME:x0,y0,x1,y1',
                    help='이 직사각형 안의 칸을 막고도 위층과 이어지는지 본다(계단 갈래 하나를 뺀 경우 등). --stack 에 1 이 있어야 한다')
    ap.add_argument('--nav-png', help='보행 격자 그림을 이 경로에 남긴다(PIL 필요)')


class Grid:
    def __init__(self, lo, hi, cell):
        self.cell = cell
        self.x0 = math.floor((lo[0] - 1.0) / cell) * cell + 0.00013      # 칸 중심이 정점 좌표 격자 위에 놓이지 않게 조금 비튼다
        self.y0 = math.floor((lo[1] - 1.0) / cell) * cell + 0.00013
        self.nx = int(math.ceil((hi[0] + 1.0 - self.x0) / cell))
        self.ny = int(math.ceil((hi[1] + 1.0 - self.y0) / cell))

    def xy(self, col):
        col = np.asarray(col)
        return self.x0 + (col % self.nx + 0.5) * self.cell, self.y0 + (col // self.nx + 0.5) * self.cell


def rasterize(grid, co, tri):
    """수직이 아닌 삼각형마다 그 아래 칸 중심의 z. 아래를 보는 면은 고체 시작(+1), 위를 보는 면은 끝(-1)."""
    cell = grid.cell
    P = co[tri]
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    d = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    cols, zs, ss = [], [], []
    for t in np.nonzero(np.abs(d) > 1e-12)[0]:
        a, b, c = P[t]
        mn, mx = P[t, :, :2].min(axis=0), P[t, :, :2].max(axis=0)
        i0 = max(0, int(math.ceil((mn[0] - grid.x0) / cell - 0.5)))
        i1 = min(grid.nx - 1, int(math.floor((mx[0] - grid.x0) / cell - 0.5)))
        j0 = max(0, int(math.ceil((mn[1] - grid.y0) / cell - 0.5)))
        j1 = min(grid.ny - 1, int(math.floor((mx[1] - grid.y0) / cell - 0.5)))
        if i1 < i0 or j1 < j0:
            continue
        X, Y = np.meshgrid(grid.x0 + (np.arange(i0, i1 + 1) + 0.5) * cell, grid.y0 + (np.arange(j0, j1 + 1) + 0.5) * cell)
        px, py = X - a[0], Y - a[1]
        lb = (px * e2[t, 1] - py * e2[t, 0]) / d[t]
        lc = (e1[t, 0] * py - e1[t, 1] * px) / d[t]
        la = 1.0 - lb - lc
        inside = (la >= 0) & (lb >= 0) & (lc >= 0)
        if not inside.any():
            continue
        J, I = np.nonzero(inside)
        cols.append((J + j0) * grid.nx + (I + i0))
        zs.append((la * a[2] + lb * b[2] + lc * c[2])[inside])
        ss.append(np.full(len(J), 1 if d[t] < 0 else -1, dtype=np.int8))
    return np.concatenate(cols), np.concatenate(zs), np.concatenate(ss)


def solid_intervals(col, z, s):
    order = np.lexsort((-s, z, col))           # 같은 높이에서는 시작을 먼저(맞닿은 고체를 한 구간으로)
    col, z, s = col[order], z[order], s[order].astype(np.int64)
    depth = np.cumsum(s)
    first = np.r_[True, col[1:] != col[:-1]]
    grp = np.cumsum(first) - 1
    base = (depth - s)[first]
    after = depth - base[grp]
    before = after - s
    st = (before <= 0) & (after > 0)
    en = (before > 0) & (after <= 0)
    sc, ec = col[st], col[en]
    n = max(int(col.max()) + 1, 1)
    bad = np.nonzero(np.bincount(sc, minlength=n) != np.bincount(ec, minlength=n))[0]
    end_depth = after[np.r_[first[1:], True]]
    keep_s, keep_e = ~np.isin(sc, bad), ~np.isin(ec, bad)
    return col[st][keep_s], z[st][keep_s], z[en][keep_e], {'unbalanced_columns': int(len(bad)), 'columns_ending_inside': int((end_depth != 0).sum())}


def free_mask(blocked, rc):
    """막힌 칸에서 rc(칸) 이상 떨어진 칸. 세로 거리를 먼저 구하고 가로로 최소 제곱 거리를 찾는다(rc 안에서 정확한 유클리드)."""
    K = int(math.ceil(rc)) + 1
    H = blocked.shape[0]
    g = np.where(blocked, 0, K + 1).astype(np.int64)
    for i in range(1, H):
        np.minimum(g[i], g[i - 1] + 1, out=g[i])
    for i in range(H - 2, -1, -1):
        np.minimum(g[i], g[i + 1] + 1, out=g[i])
    g2 = g * g
    best = g2.copy()
    for dk in range(1, K + 1):
        np.minimum(best[:, dk:], g2[:, :-dk] + dk * dk, out=best[:, dk:])
        np.minimum(best[:, :-dk], g2[:, dk:] + dk * dk, out=best[:, :-dk])
    return best >= rc * rc


def walk_spans(icol, ib, it, height):
    order = np.lexsort((ib, icol))
    icol, ib, it = icol[order], ib[order], it[order]
    same = np.r_[icol[1:] == icol[:-1], False]
    ceil = np.where(same, np.r_[ib[1:], np.inf], np.inf)
    walk = (ceil - it) >= height
    return icol[walk], it[walk], ceil[walk], (icol, ib, it)


def standable_spans(grid, scol, sh, intervals, radius, height, step):
    icol, ib, it = intervals
    rc = (radius + grid.cell / 2) / grid.cell
    margin = int(math.ceil(rc)) + 3
    ok = np.zeros(len(scol), dtype=bool)
    for h in np.unique(np.round(sh, 3)):
        cand = np.nonzero(np.abs(sh - h) < 6e-4)[0]
        if not len(cand):
            continue
        cc = scol[cand]
        cy, cx = cc // grid.nx, cc % grid.nx
        y0, y1 = max(0, cy.min() - margin), min(grid.ny, cy.max() + margin + 1)
        x0, x1 = max(0, cx.min() - margin), min(grid.nx, cx.max() + margin + 1)
        W = x1 - x0

        def local(cols):
            yy, xx = cols // grid.nx, cols % grid.nx
            m = (yy >= y0) & (yy < y1) & (xx >= x0) & (xx < x1)
            return (yy[m] - y0) * W + (xx[m] - x0)
        blocked = np.ones((y1 - y0) * W, dtype=bool)
        blocked[local(scol[np.abs(sh - h) <= step + 1e-6])] = False                 # 디딜 면이 있는 칸
        blocked[local(icol[(ib < h + height - 1e-6) & (it > h + step + 1e-6)])] = True   # 몸통 높이에 고체가 있는 칸
        free = free_mask(blocked.reshape(y1 - y0, W), rc).ravel()
        ok[cand] = free[(cy - y0) * W + (cx - x0)]
    return ok


def components(grid, col, h, ceil, height, step, removed=None):
    """설 수 있는 칸끼리 4방향 연결 성분(칸마다 성분의 대표 번호). removed(불리언)는 연결에서 뺄 칸."""
    n = len(col)
    order = np.lexsort((h, col))
    rank = np.zeros(n, dtype=np.int64)
    sc = col[order]
    first = np.r_[True, sc[1:] != sc[:-1]]
    start = np.maximum.accumulate(np.where(first, np.arange(n), 0))
    rank[order] = np.arange(n) - start
    K = int(rank.max()) + 1
    ncol = grid.nx * grid.ny
    tab = np.full(ncol * K, -1, dtype=np.int64)
    tab[col * K + rank] = np.arange(n)
    tab = tab.reshape(ncol, K)
    allc = np.arange(ncol)
    ea, eb = [], []
    for off, valid in ((1, (allc % grid.nx) < grid.nx - 1), (grid.nx, allc < ncol - grid.nx)):
        src = allc[valid]
        for k1 in range(K):
            A = tab[src, k1]
            for k2 in range(K):
                B = tab[src + off, k2]
                m = (A >= 0) & (B >= 0)
                a, b = A[m], B[m]
                m2 = (np.abs(h[a] - h[b]) <= step + 1e-6) & ((np.minimum(ceil[a], ceil[b]) - np.maximum(h[a], h[b])) >= height - 1e-6)
                ea.append(a[m2])
                eb.append(b[m2])
    a, b = np.concatenate(ea), np.concatenate(eb)
    if removed is not None:
        keep = ~(removed[a] | removed[b])
        a, b = a[keep], b[keep]
    lab = np.arange(n)
    while True:
        la, lb = lab[a], lab[b]
        if np.array_equal(la, lb):
            return lab
        np.minimum.at(lab, np.maximum(la, lb), np.minimum(la, lb))
        while True:
            nl = lab[lab]
            if np.array_equal(nl, lab):
                break
            lab = nl


def nearest(st, mask, x, y, z, step):
    """mask 인 칸 중 높이가 z ± 단 높이 안에서 (x, y)에 가장 가까운 칸 번호와 거리."""
    idx = np.nonzero(mask & (np.abs(st['h'] - z) <= step + 1e-6))[0]
    if not len(idx):
        return None, None
    d = np.hypot(st['x'][idx] - x, st['y'][idx] - y)
    k = int(np.argmin(d))
    return int(idx[k]), float(d[k])


def run(level, a):
    if a.capsule_radius is None or a.capsule_height is None or a.step_height is None:
        return {'reachability': qa.skipped('--capsule-radius / --capsule-height / --step-height 를 게임의 캐릭터·내비 설정 값으로 줘야 한다')}
    names = level.select(a.nav_collision, level.static_collision_names or level.static_names)
    if not names:
        return {'reachability': qa.skipped('걷는 면으로 쓸 메시가 없다')}
    R, Hc, step, cell = a.capsule_radius, a.capsule_height, a.step_height, a.cell
    stack = sorted({int(v) for v in a.stack.split(',') if v.strip()})
    pitch = a.pitch or next((f.get('storey_pitch_m') for f in level.files if f.get('storey_pitch_m')), None)
    if stack != [0] and not pitch:
        return {'reachability': qa.skipped('--stack 을 쓰려면 --pitch(층 높이)가 있어야 한다')}
    floor_z = level.floor_z

    cos, tris, off = [], [], 0
    for n in names:
        co, tri = level.world(n)
        cos.append(co)
        tris.append(tri + off)
        off += len(co)
    co, tri = np.concatenate(cos), np.concatenate(tris)
    grid = Grid(co.min(axis=0), co.max(axis=0), cell)
    col, z, s = rasterize(grid, co, tri)
    col = np.concatenate([col] * len(stack))
    z = np.concatenate([z + k * (pitch or 0.0) for k in stack])
    s = np.concatenate([s] * len(stack))
    icol, ib, it, closure = solid_intervals(col, z, s)
    scol, sh, sceil, intervals = walk_spans(icol, ib, it, Hc)
    ok = standable_spans(grid, scol, sh, intervals, R, Hc, step)
    st = {'col': scol[ok], 'h': sh[ok], 'ceil': sceil[ok]}
    st['x'], st['y'] = grid.xy(st['col'])
    st['label'] = components(grid, st['col'], st['h'], st['ceil'], Hc, step)
    everything = np.ones(len(st['col']), dtype=bool)
    qa.log('보행 격자 %d×%d, 고체 구간 %d, 설 자리 %d / 걷는 면 %d' % (grid.nx, grid.ny, len(icol), len(st['col']), len(scol)))

    # ---- 시작점
    tol = a.marker_tolerance if a.marker_tolerance is not None else R
    seed_desc, seed_cell = None, None
    if a.seed_marker:
        m = next((m for m in level.markers if m['name'] == a.seed_marker), None)
        if m is None:
            return {'reachability': {'error': '시작 표식 %s 가 없다' % a.seed_marker, 'passed': False}}
        seed_xyz, seed_desc = m['position'], 'marker ' + a.seed_marker
    elif a.seed:
        seed_xyz, seed_desc = [float(v) for v in a.seed.split(',')], 'point ' + a.seed
    else:
        seed_xyz = None
    if seed_xyz is not None:
        seed_cell, seed_dist = nearest(st, everything, *seed_xyz, step)
        if seed_cell is None or seed_dist > max(tol, cell):
            return {'reachability': {'error': '시작점 %s 근처(%.2fm)에 설 자리가 없다' % (seed_desc, max(tol, cell)), 'passed': False}}
        main = int(st['label'][seed_cell])
    else:
        here = np.abs(st['h'] - floor_z) < 0.01
        labs, counts = np.unique(st['label'][here], return_counts=True)
        if not len(labs):
            return {'reachability': {'error': '바닥 높이에 설 자리가 없다', 'passed': False}}
        main, seed_desc = int(labs[np.argmax(counts)]), 'largest component at floor level (no seed given)'
        seed_cell = int(np.nonzero(here & (st['label'] == main))[0][0])
    in_main = st['label'] == main

    # ---- 이 층의 성분
    top = floor_z + pitch - 0.3 if pitch else np.inf
    here = (st['h'] > floor_z - 0.3) & (st['h'] < top)
    comp_rows = []
    for lab in np.unique(st['label'][here]):
        m = here & (st['label'] == lab)
        comp_rows.append({'label': int(lab), 'cells': int(m.sum()), 'area_m2': qa.r6(m.sum() * cell * cell), 'main': bool(lab == main),
                          'bounds_m': [qa.r6(st['x'][m].min()), qa.r6(st['y'][m].min()), qa.r6(st['x'][m].max()), qa.r6(st['y'][m].max())],
                          'z_range_m': [qa.r6(st['h'][m].min()), qa.r6(st['h'][m].max())]})
    comp_rows.sort(key=lambda r: -r['cells'])

    # ---- 방 표식
    skip = {v for v in a.skip_floor_kinds.split(',') if v}
    rooms = []
    for m in level.markers_with(a.room_prefix):
        x, y, zz = m['position']
        if qa.prop(m, 'floor_kind') in skip:
            rooms.append({'room': m['name'], 'skipped': 'floor_kind=%s' % qa.prop(m, 'floor_kind'), 'reachable': None, 'position_m': [x, y, zz]})
            continue
        _, d_main = nearest(st, in_main, x, y, zz, step)
        _, d_any = nearest(st, everything, x, y, zz, step)
        rooms.append({'room': m['name'], 'reachable': bool(d_main is not None and d_main <= tol),
                      'nearest_reachable_cell_m': None if d_main is None else qa.r6(d_main),
                      'nearest_standable_cell_m': None if d_any is None else qa.r6(d_any), 'position_m': [x, y, zz]})
    unreachable = [r['room'] for r in rooms if r['reachable'] is False]

    # ---- 문 표식: 개구부(폭 × 깊이) 안에 시작 성분의 칸이 있는가
    require = qa.door_requirements(a)
    doors = []
    for m in level.markers_with(a.door_prefix):
        width = qa.prop(m, 'width_m', 'door_width')
        if width is None:
            doors.append({'door': m['name'], 'skipped': '폭(width_m) 속성이 없다', 'passable': None, 'required': False})
            continue
        x, y, zz = m['position']
        cth, sth = math.cos(m['yaw']), math.sin(m['yaw'])
        dx, dy = st['x'] - x, st['y'] - y
        depth = float(qa.prop(m, 'depth_m', default=a.door_zone_depth))
        zone = (np.abs(dx * cth + dy * sth) <= float(width) / 2) & (np.abs(-dx * sth + dy * cth) <= depth / 2) & (np.abs(st['h'] - zz) <= 0.02)
        required = qa.door_required(m, require)
        doors.append({'door': m['name'], 'width_m': float(width), 'required': required, 'standable_cells_in_opening': int(zone.sum()),
                      'reachable_cells_in_opening': int((zone & in_main).sum()), 'passable': bool((zone & in_main).any()), 'position_m': [x, y, zz]})
    not_passable = [d['door'] for d in doors if d['required'] and d['passable'] is False]

    # ---- 층 사이: 이 층 바닥의 시작 성분 칸과 같은 자리의 위·아래 층 칸이 같은 성분인가
    link = {}
    if pitch:
        def linked(labels, k):
            mine = labels[seed_cell]
            base = np.unique(st['col'][(labels == mine) & (np.abs(st['h'] - floor_z) < 0.01)])
            other = (labels == mine) & (np.abs(st['h'] - (floor_z + k * pitch)) < 0.01)
            return bool(np.isin(st['col'][other], base).any())
        if 1 in stack:
            link['up'] = linked(st['label'], 1)
        if -1 in stack:
            link['down'] = linked(st['label'], -1)
        for item in a.block_rect:
            name, rect = item.split(':', 1)
            x0, y0, x1, y1 = [float(v) for v in rect.split(',')]
            removed = (st['x'] >= min(x0, x1)) & (st['x'] <= max(x0, x1)) & (st['y'] >= min(y0, y1)) & (st['y'] <= max(y0, y1))
            labels = components(grid, st['col'], st['h'], st['ceil'], Hc, step, removed)
            link['without_' + name] = {'up': linked(labels, 1) if 1 in stack else None, 'removed_cells': int(removed.sum())}
    link_ok = all(v if isinstance(v, bool) else bool(v['up']) for v in link.values())

    rep = {'collision_meshes': names, 'cell_m': cell, 'capsule_radius_m': R, 'capsule_height_m': Hc, 'step_m': step,
           'clearance_rule': 'capsule centre >= radius + half cell from blocked cell centres (blocked = body-height solid or no footing within +-step)',
           'stack': stack, 'pitch_m': pitch, 'grid': [grid.nx, grid.ny], 'mesh_closure': closure, 'walk_surfaces': int(len(scol)),
           'standable_cells_all_levels': int(len(st['col'])), 'seed': seed_desc, 'main_component': main,
           'this_storey_component_count': len(comp_rows), 'this_storey_components': comp_rows[:30],
           'marker_tolerance_m': tol, 'rooms_checked': sum(1 for r in rooms if r['reachable'] is not None),
           'rooms_skipped_no_floor': [r['room'] for r in rooms if r['reachable'] is None], 'unreachable_rooms': unreachable,
           'doors_checked': sum(1 for d in doors if d['passable'] is not None), 'doors_required': sum(1 for d in doors if d['required']),
           'required_doors_not_passable': not_passable,
           'other_doors_not_passable': [d['door'] for d in doors if not d['required'] and d['passable'] is False],
           'floor_link': link, 'rooms': rooms, 'doors': doors}
    rep['passed'] = bool(closure['unbalanced_columns'] == 0 and closure['columns_ending_inside'] == 0 and not unreachable and not not_passable and link_ok)
    if a.nav_png:
        draw(a.nav_png, grid, st, in_main, here, floor_z, rooms, doors)
        rep['png'] = os.path.basename(a.nav_png)
    return {'reachability': rep}


def draw(path, grid, st, in_main, here, floor_z, rooms, doors):
    """초록: 시작점에서 닿는 설 자리(바닥 높이), 파랑: 닿는 계단·계단참, 주황: 설 수 있지만 끊긴 곳.
    문: 파란 원 = 통과, 빨간 원 = 막힘. 방: 검은 점 = 닿음, 빨간 X = 못 닿음, 회색 X = 바닥 없는 공간."""
    from PIL import Image, ImageDraw
    S = 2
    px = np.full((grid.ny, grid.nx, 3), 255, dtype=np.uint8)
    cols, mainc, hs = st['col'][here], in_main[here], st['h'][here]
    colour = np.where(mainc[:, None], np.array([[150, 215, 150]]), np.array([[240, 150, 60]]))
    colour[mainc & (np.abs(hs - floor_z) > 0.01)] = (110, 170, 220)
    px[cols // grid.nx, cols % grid.nx] = colour
    img = Image.fromarray(px[::-1].repeat(S, 0).repeat(S, 1))
    dr = ImageDraw.Draw(img)
    H = grid.ny * S

    def P(x, y):
        return (x - grid.x0) / grid.cell * S, H - (y - grid.y0) / grid.cell * S
    for d in doors:
        if d['passable'] is None:
            continue
        X, Y = P(*d['position_m'][:2])
        dr.ellipse([X - 4, Y - 4, X + 4, Y + 4], outline=(0, 90, 220) if d['passable'] else (220, 0, 0), width=2)
    for r in rooms:
        X, Y = P(*r['position_m'][:2])
        if r['reachable']:
            dr.ellipse([X - 3, Y - 3, X + 3, Y + 3], fill=(0, 0, 0))
        else:
            c = (150, 150, 150) if r['reachable'] is None else (220, 0, 0)
            dr.line([X - 5, Y - 5, X + 5, Y + 5], fill=c, width=2)
            dr.line([X - 5, Y + 5, X + 5, Y - 5], fill=c, width=2)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path)


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
