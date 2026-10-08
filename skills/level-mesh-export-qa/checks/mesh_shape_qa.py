"""정적 메시의 형상 품질: 수평 단면을 잘라 턱·비틀림·짧은 변·불규칙한 곡선을 잰다.

벽 형상을 픽셀 윤곽이나 측정 점으로 만들면 원본 위 겹침은 맞아도 메시에 1~3cm 턱, 0.2~4° 비틀린 변,
간격이 들쭉날쭉한 곡선이 남는다. 이 검사는 형상을 어떻게 만들었든 결과 메시만 본다.
판정 규칙은 같은 폴더의 shape_quality.py의 shape_issues(SHAPE 기준, 미터·도)를 쓴다.

벽·바닥판·천장 같은 셸에 쓴다. 창틀·문틀처럼 원래 몇 cm짜리 부재가 있는 메시는 짧은 변이 정상이라 빼거나 따로 본다.

  python mesh_shape_qa.py <mesh.npz|mesh.obj> --z 1.2 [--z 5.1] [--label wall] [--out report.json]
      npz: vertices(N×3), triangles(M×3) [, labels(M) — --label로 포함 문자열 거르기]. Z가 위.
  blender -b <file.blend> --python mesh_shape_qa.py -- --objects SM_A1_Walls [--objects ...] --z 1.2 --out report.json
      Blender 안에서는 객체의 평가된 메시를 월드 좌표로 쓴다(--scene으로 씬 지정, 기본 현재 씬).
종료 코드: 문제 0이면 0, 아니면 1.
"""
import argparse
import json
import math
import os
import runpy
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SHAPE_QA = runpy.run_path(os.path.join(HERE, 'shape_quality.py'))


def section_loops(vertices, triangles, z, eps=1e-6):
    """삼각형 메시를 z 평면으로 잘라 닫힌·열린 단면 사슬 [[(x, y), ...]]. 꼭짓점이 평면 위에 있으면 eps만큼 올려 자른다."""
    v = np.asarray(vertices, float)
    t = np.asarray(triangles, int)
    zz = v[:, 2] - z
    zz[np.abs(zz) < eps] = eps
    segs = []
    for tri in t:
        d = zz[tri]
        if (d > 0).all() or (d < 0).all():
            continue
        pts = []
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            if (zz[a] > 0) != (zz[b] > 0):
                s = zz[a] / (zz[a] - zz[b])
                p = v[a] + (v[b] - v[a]) * s
                pts.append((float(p[0]), float(p[1])))
        if len(pts) == 2 and math.dist(*pts) > 1e-9:
            segs.append(tuple(pts))
    key = lambda p: (round(p[0], 5), round(p[1], 5))
    adj = {}
    for i, (a, b) in enumerate(segs):
        adj.setdefault(key(a), []).append((i, 0))
        adj.setdefault(key(b), []).append((i, 1))
    used = [False] * len(segs)
    loops = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        chain = [segs[i][0], segs[i][1]]
        for direction in (1, -1):  # 앞으로 잇고, 열린 사슬이면 뒤로도 잇는다
            while True:
                end = chain[-1] if direction == 1 else chain[0]
                nxt = next(((j, s) for j, s in adj.get(key(end), []) if not used[j]), None)
                if nxt is None:
                    break
                j, side = nxt
                used[j] = True
                p = segs[j][1 - side]
                if direction == 1:
                    chain.append(p)
                else:
                    chain.insert(0, p)
            if math.dist(chain[0], chain[-1]) < 1e-5:
                break
        loops.append(chain)
    return loops


def merge_collinear(pts, closed, deg=0.05):
    """삼각화 때문에 한 직선이 여러 조각으로 잘린 것을 한 변으로 합친다."""
    if closed and math.dist(pts[0], pts[-1]) < 1e-5:
        pts = pts[:-1]
    out = []
    for p in pts:
        if out and math.dist(out[-1], p) < 1e-6:
            continue
        out.append(p)
    changed = True
    while changed and len(out) > 3:
        changed = False
        n = len(out)
        rng = range(n) if closed else range(1, n - 1)
        for k in rng:
            a, b, c = out[(k - 1) % n], out[k], out[(k + 1) % n]
            if abs(SHAPE_QA['_turn'](a, b, c)) < deg:
                del out[k]
                changed = True
                break
    return out


def analyze(sections, directions=(), opts=None):
    """sections: [(이름, z, [사슬...])]. 사슬마다 경로로 바꿔 shape_issues(jog='chain')로 본다."""
    paths = []
    for name, z, loops in sections:
        for i, chain in enumerate(loops):
            closed = math.dist(chain[0], chain[-1]) < 1e-5
            pts = merge_collinear(chain, closed)
            if len(pts) < 2:
                continue
            paths.append({'id': f'{name}@z{z:g}#{i}', 'edges': SHAPE_QA['path_edges'](pts, closed), 'closed': closed})
    res = SHAPE_QA['shape_issues'](paths, directions, opts, 'chain')
    edges = [math.dist(e[1], e[2]) for p in paths for e in p['edges'] if e[0] == 'line']
    counts = Counter(i['type'] for i in res['issues'])
    return {'sections': [{'name': n, 'z': z, 'loops': len(l)} for n, z, l in sections],
            'loops': len(paths), 'edges': len(edges),
            'edges_under_2cm': int(sum(1 for e in edges if e < 0.02)),
            'edges_under_5cm': int(sum(1 for e in edges if e < 0.05)),
            'directions_deg': res['directions_deg'], 'counts': dict(counts), 'issues': res['issues'],
            'passed': not res['issues']}


def _load_file(path, label=None):
    if path.lower().endswith('.npz'):
        d = np.load(path)
        v = d['vertices'] if 'vertices' in d.files else d['V']
        t = d['triangles'] if 'triangles' in d.files else (d['faces'] if 'faces' in d.files else d['F'])
        if label and 'labels' in d.files:
            t = t[np.char.find(d['labels'].astype(str), label) >= 0]
        return v, t
    vs, ts = [], []
    with open(path, encoding='utf-8', errors='ignore') as fp:
        for line in fp:
            if line.startswith('v '):
                vs.append([float(x) for x in line.split()[1:4]])
            elif line.startswith('f '):
                idx = [int(x.split('/')[0]) - 1 for x in line.split()[1:]]
                ts += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]
    return np.array(vs), np.array(ts)


def _blender_meshes(names, scene_name=None):
    import bpy
    scene = bpy.data.scenes[scene_name] if scene_name else bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for name in names:
        ob = scene.objects[name]
        me = ob.evaluated_get(dg).to_mesh()
        me.calc_loop_triangles()
        v = np.array([tuple(ob.matrix_world @ p.co) for p in me.vertices])
        t = np.array([tuple(tr.vertices) for tr in me.loop_triangles])
        ob.evaluated_get(dg).to_mesh_clear()
        out.append((name, v, t))
    return out


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):        # 콘솔 인코딩이 한글·기호를 못 쓰는 환경에서 --help 출력이 죽지 않게
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass
    if argv is None:
        argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mesh', nargs='?')
    ap.add_argument('--objects', action='append', default=[])
    ap.add_argument('--scene')
    ap.add_argument('--label')
    ap.add_argument('--z', type=float, action='append', required=True)
    ap.add_argument('--directions', type=float, nargs='*', default=[])
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    if a.objects:
        meshes = _blender_meshes(a.objects, a.scene)
    else:
        v, t = _load_file(a.mesh, a.label)
        meshes = [(os.path.basename(a.mesh) + (f'[{a.label}]' if a.label else ''), v, t)]
    sections = [(name, z, section_loops(v, t, z)) for name, v, t in meshes for z in a.z]
    rep = analyze(sections, a.directions)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as fp:
            json.dump(rep, fp, ensure_ascii=False, indent=1)
    print(json.dumps({k: rep[k] for k in ('sections', 'loops', 'edges', 'edges_under_2cm', 'edges_under_5cm',
                                          'directions_deg', 'counts', 'passed')}, ensure_ascii=False))
    return 0 if rep['passed'] else 1


if __name__ == '__main__':
    code = main()
    if 'bpy' not in sys.modules:
        sys.exit(code)
