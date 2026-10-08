"""미터·도 단위의 메시 단면 형상 판정. 허용 오차는 SHAPE가 정의한다."""
import math

import numpy as np

SHAPE = {
    'min_segment_m': 0.05,      # 이보다 짧은 직선 변은 픽셀 계단·턱
    'jog_max_m': 0.05,          # 평행한 두 선(또는 면)이 이만큼 이하로 어긋나면 턱
    'jog_gap_m': 1.2,           # 그 두 선이 축 방향으로 이만큼 이내에 있을 때만(문 개구부 건너편까지)
    'skew_min_deg': 0.1,        # 주방향에서 이보다 크게,
    'skew_max_deg': 3.0,        # 이 이하로 벗어난 변은 비틀림
    'skew_min_len_m': 0.3,
    'direction_min_len_m': 1.0,  # 사선 주방향을 찾을 때 쓰는 변의 최소 길이
    'min_wall_m': 0.03,         # 벽 두께 하한(이보다 얇거나 3px 미만이면 벽이 아니라 픽셀 조각)
    'min_wall_px': 3.0,
    'curve_min_edges': 4,       # 같은 방향으로 조금씩 꺾이는 짧은 변이 이만큼 이어지면 곡선을 직선 조각으로 그린 것
    'curve_max_edge_m': 10.0,   # 큰 반지름 곡선을 1px 단순화한 윤곽은 현이 수 m까지 길어진다
    'curve_turn_deg': (0.1, 25.0),  # 꼭짓점 하나에서 이보다 크게 꺾이면 모서리(곡선 구간이 끊긴다)
    'curve_min_total_deg': 6.0,     # 곡선 구간 전체가 이만큼 이상 돌아가야 곡선(곧은 벽의 픽셀 계단은 0°에 가깝다)
    'curve_cv': 0.25,           # 그 조각들의 꺾임각·변 길이 변동계수가 이보다 크면 불규칙한 곡선
    'curve_fine_edge_m': 0.5,   # regularize: 변이 이보다 짧은 곡선 조각은 고르더라도 원호로 바꾼다
}


def _is_arc(item):
    return isinstance(item, dict) and 'arc' in item



def arc_xy(arc, deg):
    """원호 위 점. 각도는 이미지 좌표(x 오른쪽, y 아래)의 atan2(y-cy, x-cx), 도 단위."""
    t = math.radians(float(deg))
    return (float(arc['center'][0]) + float(arc['radius']) * math.cos(t),
            float(arc['center'][1]) + float(arc['radius']) * math.sin(t))



def path_edges(points, closed=False):
    """경로 항목(점 (x, y) 또는 {'arc': {center, radius, a0, a1}})을 변 목록 [('line', a, b) | ('arc', arc)]으로.
    원호의 양 끝과 앞뒤 점은 직선 변으로 잇는다."""
    edges, prev, first = [], None, None
    for it in points:
        if _is_arc(it):
            arc = it['arc']
            s, e = arc_xy(arc, arc['a0']), arc_xy(arc, arc['a1'])
            if prev is not None and math.dist(prev, s) > 1e-6:
                edges.append(('line', prev, s))
            edges.append(('arc', arc))
            first = s if first is None else first
            prev = e
        else:
            p = (float(it[0]), float(it[1]))
            if prev is not None and math.dist(prev, p) > 1e-9:
                edges.append(('line', prev, p))
            first = p if first is None else first
            prev = p
    if closed and prev is not None and math.dist(prev, first) > 1e-6:
        edges.append(('line', prev, first))
    return edges



def _angle(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0



def _adiff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)



def _turn(a, b, c):
    """a→b→c에서 꺾인 각(도, 부호 있음)."""
    ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
    return math.degrees(math.atan2(ux * vy - uy * vx, ux * vx + uy * vy))



def _cv(xs):
    xs = np.asarray(xs, float)
    return float(xs.std() / xs.mean()) if len(xs) > 1 and xs.mean() > 0 else 0.0



def _align_shifts(d, h1, h2):
    """선 2(선 1에서 법선 방향 d만큼 떨어짐, 반두께 h2)를 선 1(반두께 h1)에 맞추는 이동 후보:
    중심선을 맞추거나 **같은 쪽 면**을 맞춘다(두께가 다른 벽은 한쪽 면이 이어지는 게 정상이다)."""
    return [-d, (h2 - h1) - d, (h1 - h2) - d]



def dominant_directions(lines, declared=(), min_len=1.0, window=3.0):
    """주방향(도, 0~180): 0·90 + 선언한 방향 + 그 밖의 긴 변이 모인 사선 방향(길이 가중 평균).
    lines는 [(각도, 길이)]. 0·90에서 window 이내인 변은 새 방향을 만들지 않는다."""
    dirs = [0.0, 90.0] + [float(d) % 180.0 for d in declared]
    clusters = []
    for ang, L in sorted(lines, key=lambda t: -t[1]):
        if L < min_len or min(_adiff(ang, d) for d in dirs) <= window:
            continue
        c = next((c for c in clusters if _adiff(c['mean'], ang) <= window), None)
        if c is None:
            clusters.append({'ref': ang, 'sum': 0.0, 'w': L, 'mean': ang})
        else:
            c['sum'] += (((ang - c['ref'] + 90.0) % 180.0) - 90.0) * L
            c['w'] += L
            c['mean'] = (c['ref'] + c['sum'] / c['w']) % 180.0
    return dirs + [round(c['mean'], 4) for c in clusters]



def _curve_runs(pts, closed, o):
    """곡선을 직선 조각으로 그린 구간 [(시작 점 인덱스, 변 수)]: 짧은 변(≤ curve_max_edge)이 이어지고 꼭짓점마다
    조금(≤ curve_turn_deg 위쪽)만 꺾이며, 구간 전체로는 curve_min_total_deg 이상 돌아간다. 픽셀 계단처럼
    좌우로 흔들리는 조각도 잡는다(곧은 벽의 계단은 전체 회전이 0에 가까워 빠진다). 닫힌 경로는 인덱스가 감긴다."""
    n = len(pts)
    if closed and n > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts = pts[:-1]
        n -= 1
    if n < 3:
        return [], pts
    hi = o['curve_turn_deg'][1]

    def turn_at(k):
        if not closed and (k == 0 or k == n - 1):
            return None
        a, b, c = pts[(k - 1) % n], pts[k % n], pts[(k + 1) % n]
        if max(math.dist(a, b), math.dist(b, c)) > o['curve_max_edge_m']:
            return None
        t = _turn(a, b, c)
        return t if abs(t) <= hi else None
    turns = [turn_at(k) for k in range(n)]
    if closed and all(t is not None for t in turns):  # 고리 전체가 곡선(원형 기둥 등)
        return ([(0, n)] if abs(sum(turns)) >= o['curve_min_total_deg'] and n >= o['curve_min_edges'] else []), pts
    start = next((k for k in range(n) if turns[k] is None), 0) if closed else 0
    runs, k = [], 0
    while k < n:
        i = (start + k) % n
        if turns[i] is None:
            k += 1
            continue
        j = k
        while j + 1 < n and turns[(start + j + 1) % n] is not None:
            j += 1
        total = sum(turns[(start + t) % n] for t in range(k, j + 1))
        if j - k + 2 >= o['curve_min_edges'] and abs(total) >= o['curve_min_total_deg']:
            runs.append(((start + k - 1) % n, j - k + 2))
        k = j + 1
    return runs, pts



def _run_points(pts, run):
    i0, count = run
    return [pts[(i0 + t) % len(pts)] for t in range(count + 1)]



def _run_stats(rp, lo=0.1):
    lengths = [math.dist(a, b) for a, b in zip(rp, rp[1:])]
    turns = [_turn(a, b, c) for a, b, c in zip(rp, rp[1:], rp[2:])]
    main = 1 if sum(turns) >= 0 else -1
    flips = sum(1 for t in turns if t * main < -lo)
    inner = lengths[1:-1] if len(lengths) >= 4 else lengths
    return {'edges': len(lengths), 'turn_cv': round(_cv([abs(t) for t in turns]), 3),
            'length_cv': round(_cv(inner), 3), 'sign_flips': flips, 'total_turn_deg': round(sum(turns), 2),
            'median_edge': float(np.median(lengths))}



def _irregular(st, o):
    return st['sign_flips'] > 0 or st['turn_cv'] > o['curve_cv'] or st['length_cv'] > o['curve_cv']



def shape_issues(paths, declared=(), opts=None, jog='pairs'):
    """형상 품질 문제를 찾는다. paths: [{'id', 'edges': path_edges 형식(미터), 'thickness': m 또는 None,
    'px': 같은 길이의 픽셀 변 목록(선택, 표시 위치용)}].
    jog='pairs'는 서로 다른 경로까지(중심선 TRACE), 'chain'은 같은 고리에서 짧은 변을 사이에 둔 두 변만(메시 단면) 본다.
    돌려주는 것: {'issues': [...], 'directions_deg': [...]}."""
    o = dict(SHAPE, **(opts or {}))
    issues, lines = [], []

    def at(p, i, a, b):
        px = p.get('px')
        if px and i < len(px) and px[i][0] == 'line':
            a, b = px[i][1], px[i][2]
            return {'at_px': [round((a[0] + b[0]) / 2, 1), round((a[1] + b[1]) / 2, 1)]}
        return {'at_m': [round((a[0] + b[0]) / 2, 4), round((a[1] + b[1]) / 2, 4)]}
    for p in paths:
        thin_px = p.get('thickness_px') is not None and p['thickness_px'] < o['min_wall_px']
        if p.get('thickness') is not None and (p['thickness'] < o['min_wall_m'] or thin_px):
            issues.append({'type': 'sliver_wall', 'id': p['id'], 'thickness_m': round(p['thickness'], 4),
                           'thickness_px': p.get('thickness_px')})
        edges = p['edges']
        closed_loop = p.get('closed', False)
        # 이어진 직선 변 사슬(원호에서 끊는다)
        chains, cur = [], []
        for i, e in enumerate(edges):
            if e[0] == 'line':
                cur.append(i)
            else:
                if cur:
                    chains.append(cur)
                cur = []
        if cur:
            chains.append(cur)
        whole = closed_loop and len(chains) == 1 and len(chains[0]) == len(edges)
        for ch in chains:
            pts = [edges[ch[0]][1]] + [edges[i][2] for i in ch]
            runs, base = _curve_runs(pts, whole, o)
            curve_edges = set()
            for run in runs:
                rp = _run_points(base, run)
                st = _run_stats(rp)
                for t in range(run[1]):
                    curve_edges.add(ch[(run[0] + t) % len(ch)])
                if _irregular(st, o):
                    mid = ch[(run[0] + st['edges'] // 2) % len(ch)]
                    issues.append({'type': 'irregular_curve', 'id': p['id'], **st,
                                   **at(p, mid, edges[mid][1], edges[mid][2])})
            for k, i in enumerate(ch):
                a, b = edges[i][1], edges[i][2]
                L = math.dist(a, b)
                if L < 1e-9:
                    continue
                lines.append({'id': p['id'], 'i': i, 'a': a, 'b': b, 'ang': _angle(a, b), 'L': L,
                              't': p.get('thickness'), 'curve': i in curve_edges, 'path': p, 'k': k, 'chain': ch})
    straight = [l for l in lines if not l['curve']]
    dirs = dominant_directions([(l['ang'], l['L']) for l in straight], declared, o['direction_min_len_m'],
                               o['skew_max_deg'])
    for l in straight:
        if l['L'] < o['min_segment_m']:
            issues.append({'type': 'short_segment', 'id': l['id'], 'edge': l['i'], 'length_m': round(l['L'], 4),
                           **at(l['path'], l['i'], l['a'], l['b'])})
        dev = min(_adiff(l['ang'], d) for d in dirs)
        if l['L'] >= o['skew_min_len_m'] and o['skew_min_deg'] < dev <= o['skew_max_deg']:
            issues.append({'type': 'near_axis_skew', 'id': l['id'], 'edge': l['i'], 'deviation_deg': round(dev, 3),
                           'length_m': round(l['L'], 3), **at(l['path'], l['i'], l['a'], l['b'])})

    def mismatch(l1, l2):
        """l2가 l1과 같은 선(또는 면)에 놓이려면 움직여야 할 거리와 축 방향 간격."""
        ux, uy = (l1['b'][0] - l1['a'][0]) / l1['L'], (l1['b'][1] - l1['a'][1]) / l1['L']
        nx, ny = -uy, ux
        c1 = nx * l1['a'][0] + ny * l1['a'][1]
        m2 = ((l2['a'][0] + l2['b'][0]) / 2, (l2['a'][1] + l2['b'][1]) / 2)
        d = nx * m2[0] + ny * m2[1] - c1
        s1 = sorted((ux * l1['a'][0] + uy * l1['a'][1], ux * l1['b'][0] + uy * l1['b'][1]))
        s2 = sorted((ux * l2['a'][0] + uy * l2['a'][1], ux * l2['b'][0] + uy * l2['b'][1]))
        gap = max(s2[0] - s1[1], s1[0] - s2[1])
        cands = [abs(x) for x in _align_shifts(d, (l1['t'] or 0.0) / 2, (l2['t'] or 0.0) / 2)]
        return min(cands), d, gap
    jogs = []
    if jog == 'pairs':
        cand = [l for l in straight if l['L'] >= o['min_segment_m']]
        bins = {}
        for idx, l in enumerate(cand):
            bins.setdefault(int(l['ang']) % 180, []).append(idx)
        for idx, l in enumerate(cand):
            b = int(l['ang']) % 180
            for nb in ((b - 1) % 180, b, (b + 1) % 180):
                for j in bins.get(nb, []):
                    if j <= idx:
                        continue
                    m = cand[j]
                    if _adiff(l['ang'], m['ang']) > 1.0:
                        continue
                    lo_, hi_ = (l, m) if l['L'] >= m['L'] else (m, l)
                    mis, d, gap = mismatch(lo_, hi_)
                    if 0.002 < mis <= o['jog_max_m'] and gap <= o['jog_gap_m']:
                        jogs.append((lo_, hi_, mis, gap))
    else:  # 'chain': 같은 사슬에서 짧은 변 하나를 사이에 둔 두 평행 변
        by_chain = {}
        for l in straight:
            by_chain.setdefault(id(l['chain']), {})[l['k']] = l
        for ls in by_chain.values():
            for k, l in ls.items():
                mid, m = ls.get(k + 1), ls.get(k + 2)
                if mid is None or m is None or mid['L'] > o['jog_max_m'] or _adiff(l['ang'], m['ang']) > 1.0:
                    continue
                mis, d, gap = mismatch(l, m)
                if 0.002 < mis <= o['jog_max_m']:
                    jogs.append((l, m, mis, gap))
    for l, m, mis, gap in jogs:
        issues.append({'type': 'wall_jog', 'id': l['id'], 'other': m['id'], 'offset_m': round(mis, 4),
                       'gap_m': round(gap, 3), **at(m['path'], m['i'], m['a'], m['b'])})
    return {'issues': issues, 'directions_deg': dirs}

