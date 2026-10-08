"""checks/ 공용 함수: FBX·씬 읽기, manifest·표식 읽기, 메시 배열, 섬, 단면 다각형, 보고서 쓰기.

모든 검사는 Blender 파이썬 안에서 돈다(FBX 임포터와 BVH 를 쓴다). 좌표는 Blender 미터, Z 위.
입력은 둘 중 하나다.
  --fbx <파일>      빈 씬에 들여와 검사한다(게임 엔진이 받는 것과 같은 float32 메시)
  --scene <이름>    blender -b <파일.blend> --python ... 로 연 파일의 씬을 그대로 검사한다
표식(DOOR_/ROOM_/...)은 --manifest 가 있으면 그 JSON 에서, 없으면 FBX·씬의 empty 에서 읽는다.
"""
import argparse
import json
import math
import os
import re
import sys
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

T0 = time.time()
for _stream in (sys.stdout, sys.stderr):        # 콘솔 인코딩이 한글·기호를 못 쓰는 환경에서 출력하다 죽지 않게
    try:
        _stream.reconfigure(errors='replace')
    except Exception:
        pass


def log(msg):
    print('[qa] %6.1fs %s' % (time.time() - T0, msg), flush=True)


def r6(v):
    return round(float(v), 6)


def script_argv():
    return sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def base_parser(doc):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fbx', action='append', default=[], help='검사할 FBX(빈 씬에 들여온다). 여러 번 줄 수 있다')
    ap.add_argument('--scene', help='FBX 대신 지금 열린 .blend 의 이 씬을 검사한다')
    ap.add_argument('--manifest', help='메시 목록·표식의 정본 JSON(files[].meshes / files[].markers). 없으면 empty 를 표식으로 읽는다')
    ap.add_argument('--collision-prefix', default='COL_', help='충돌 메시 이름 접두어')
    ap.add_argument('--leaf-regex', default='DoorLeaf', help='manifest 가 없을 때 문짝 메시를 가려내는 이름 정규식')
    ap.add_argument('--marker-prefixes', default='DOOR_,ROOM_,STAIR_,OPEN_', help='empty 를 표식으로 읽을 때의 이름 접두어')
    ap.add_argument('--room-prefix', default='ROOM_', help='방 표식 이름 접두어')
    ap.add_argument('--door-prefix', default='DOOR_', help='문 표식 이름 접두어')
    ap.add_argument('--door-require', action='append', default=[], metavar='KEY=V1|V2',
                    help='이 속성 값을 가진 문만 통과·통과 폭을 요구한다(예: access=passage). 없으면 모든 문')
    ap.add_argument('--skip-floor-kinds', default='void', help='바닥이 없는 공간으로 보고 방 검사에서 빼는 floor_kind(쉼표로 여러 개)')
    ap.add_argument('--out', help='결과 JSON 경로')
    return ap


# ---------------------------------------------------------------------------------------------------------------------
# 메시
# ---------------------------------------------------------------------------------------------------------------------

def mesh_arrays(ob):
    """(월드 좌표 float64 N×3, 삼각형 M×3). 정점은 저장된 float32 값에서 올린다."""
    me = ob.data
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3).astype(np.float64)
    m = np.array(ob.matrix_world, dtype=np.float64)
    co = co @ m[:3, :3].T + m[:3, 3]
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get('vertices', tri)
    return co, tri.reshape(-1, 3)


def islands(tri, nverts):
    """정점마다 이어진 조각(섬)의 대표 정점 번호."""
    lab = np.arange(nverts)
    a = np.concatenate([tri[:, 0], tri[:, 1]])
    b = np.concatenate([tri[:, 1], tri[:, 2]])
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


def bvh_of(co, tri):
    return BVHTree.FromPolygons([Vector(p) for p in co], tri.tolist(), all_triangles=True, epsilon=0.0)


def point_inside(bvh, p):
    """+X 로 쏜 광선이 지나는 면의 수가 홀수면 안"""
    origin, hits = Vector(p) + Vector((0.0, 1.3e-5, 2.7e-5)), 0
    direction = Vector((1.0, 0.0, 0.0))
    for _ in range(1000):
        loc, _, _, _ = bvh.ray_cast(origin, direction)
        if loc is None:
            break
        hits += 1
        origin = loc + direction * 1e-6
    return hits % 2 == 1


# ---------------------------------------------------------------------------------------------------------------------
# 단면: 닫힌 메시를 z 평면으로 잘라 shapely 도형으로
# ---------------------------------------------------------------------------------------------------------------------

def section_segments(co, tri, z, eps=1e-6):
    """z 평면을 지나는 삼각형마다 단면 선분 (K×2×2)과 그 삼각형 번호. 평면 위 정점은 eps 만큼 올려서 자른다.
    교점은 모서리의 작은 번호 정점에서 큰 번호 쪽으로 계산해, 모서리를 나눠 쓰는 두 삼각형이 같은 값을 얻는다."""
    zz = co[:, 2] - z
    zz = np.where(np.abs(zz) < eps, eps, zz)
    up = zz > 0
    tu = up[tri]
    cross = np.nonzero(tu.any(axis=1) & ~tu.all(axis=1))[0]
    T, U = tri[cross], tu[cross]
    if not len(T):
        return np.zeros((0, 2, 2)), cross
    hit, pts = [], []
    with np.errstate(divide='ignore', invalid='ignore'):
        for i in range(3):
            a, b = T[:, i], T[:, (i + 1) % 3]
            lo, hi = np.minimum(a, b), np.maximum(a, b)
            s = zz[lo] / (zz[lo] - zz[hi])
            pts.append(co[lo, :2] + (co[hi, :2] - co[lo, :2]) * s[:, None])
            hit.append(U[:, i] != U[:, (i + 1) % 3])
    hit, pts = np.stack(hit, axis=1), np.stack(pts, axis=1)
    pick = np.argsort(~hit, axis=1, kind='stable')[:, :2]          # 지나는 모서리 두 개
    seg = pts[np.arange(len(pts))[:, None], pick]
    keep = np.hypot(*(seg[:, 0] - seg[:, 1]).T) > 1e-9
    return seg[keep], cross[keep]


def _even_odd_faces(seg):
    """한 섬의 단면 선분으로 만든 면 중 고체 안쪽인 것(+X 광선이 선분을 홀수 번 지나는 면)의 합."""
    import shapely
    faces = [g for g in shapely.get_parts(shapely.polygonize(list(shapely.linestrings(seg)))) if g.geom_type == 'Polygon']
    if len(faces) <= 1:
        return faces
    y0, y1, x0, x1 = seg[:, 0, 1], seg[:, 1, 1], seg[:, 0, 0], seg[:, 1, 0]
    out = []
    for f in faces:
        p = f.representative_point()
        m = (y0 > p.y) != (y1 > p.y)
        xi = x0[m] + (p.y - y0[m]) * (x1[m] - x0[m]) / (y1[m] - y0[m])
        if int((xi > p.x).sum()) % 2 == 1:
            out.append(f)
    return out


def section_parts(co, tri, z, labels=None):
    """[(섬 대표 번호, shapely 도형)] — 닫힌 섬마다 z 단면의 고체 영역. 섬끼리 겹치거나 맞닿아도 따로 만든다."""
    import shapely
    seg, ti = section_segments(co, tri, z)
    if not len(seg):
        return []
    lab = (islands(tri, len(co)) if labels is None else labels)[tri[ti, 0]]
    order = np.argsort(lab, kind='stable')
    lab, seg = lab[order], seg[order]
    cuts = np.nonzero(np.r_[True, lab[1:] != lab[:-1], True])[0]
    out = []
    for i0, i1 in zip(cuts[:-1], cuts[1:]):
        faces = _even_odd_faces(seg[i0:i1])
        if faces:
            out.append((int(lab[i0]), shapely.union_all(faces) if len(faces) > 1 else faces[0]))
    return out


def section_union(co, tri, z, labels=None):
    import shapely
    parts = [g for _, g in section_parts(co, tri, z, labels)]
    return shapely.union_all(parts) if parts else shapely.Polygon()


# ---------------------------------------------------------------------------------------------------------------------
# 레벨: 메시 + manifest + 표식
# ---------------------------------------------------------------------------------------------------------------------

class Level:
    def __init__(self, a):
        self.args = a
        self.prefix = a.collision_prefix
        self.manifest, self.files = None, []
        fbx = [os.path.abspath(p) for p in a.fbx]
        if a.manifest:
            with open(a.manifest, encoding='utf-8') as handle:
                self.manifest = json.load(handle)
            self.files = self.manifest.get('files', [])
            base = os.path.dirname(os.path.abspath(a.manifest))
            if fbx:
                wanted = {os.path.basename(p).lower() for p in fbx}
                matched = [f for f in self.files if os.path.basename(f.get('path', '')).lower() in wanted]
                self.files = matched or self.files
            elif not a.scene:
                fbx = [os.path.join(base, f['path']) for f in self.files]
        self.fbx = fbx
        if fbx:
            bpy.ops.wm.read_homefile(use_empty=True)
            for path in fbx:
                bpy.ops.import_scene.fbx(filepath=path)
            scene = bpy.context.scene
        elif a.scene:
            scene = bpy.data.scenes[a.scene]
        else:
            raise SystemExit('--fbx, --scene, --manifest 중 하나는 있어야 한다')
        bpy.context.view_layer.update()
        self.scene = scene
        self.objects = {o.name: o for o in scene.objects}
        self.meshes = {n: o for n, o in self.objects.items() if o.type == 'MESH'}
        self.entries = {m['name']: m for f in self.files for m in f.get('meshes', [])}
        self._world, self._islands = {}, {}
        self.markers = self._read_markers()

    # ---- 표식
    def _read_markers(self):
        out = []
        if self.manifest is not None:
            for f in self.files:
                for m in f.get('markers', []):
                    rot = m.get('rotation_euler_rad') or [0.0, 0.0, 0.0]
                    out.append({'name': m['name'], 'position': [float(v) for v in m['position_m']], 'yaw': float(rot[2]),
                                'properties': dict(m.get('properties') or {}), 'source': 'manifest'})
            return out
        prefixes = tuple(p for p in self.args.marker_prefixes.split(',') if p)
        for name, ob in sorted(self.objects.items()):
            if ob.type == 'EMPTY' and name.startswith(prefixes):
                props = {k: (ob[k] if isinstance(ob[k], (int, float, str, bool)) else str(ob[k])) for k in ob.keys() if not k.startswith('_')}
                p = ob.matrix_world.translation
                out.append({'name': name, 'position': [p.x, p.y, p.z], 'yaw': ob.matrix_world.to_euler().z, 'properties': props, 'source': 'empty'})
        return out

    def markers_with(self, prefix):
        return [m for m in self.markers if m['name'].startswith(prefix)]

    # ---- 메시 이름 묶음
    def is_collision(self, name):
        e = self.entries.get(name)
        return bool(e['collision']) if e is not None and 'collision' in e else name.startswith(self.prefix)

    @property
    def collision_names(self):
        return sorted(n for n in self.meshes if self.is_collision(n))

    @property
    def render_names(self):
        return sorted(n for n in self.meshes if not self.is_collision(n))

    @property
    def leaf_names(self):
        """문짝 렌더 메시. manifest 에서는 충돌 메시가 properties.render_mesh 로 가리키는 메시다."""
        if self.entries:
            paired = {e['properties']['render_mesh'] for e in self.entries.values()
                      if e.get('collision') and 'render_mesh' in (e.get('properties') or {})}
            return sorted(n for n in self.render_names if n in paired)
        rx = re.compile(self.args.leaf_regex)
        return sorted(n for n in self.render_names if rx.search(n))

    @property
    def static_names(self):
        leaves = set(self.leaf_names)
        return [n for n in self.render_names if n not in leaves]

    @property
    def static_collision_names(self):
        if self.entries:
            return sorted(n for n in self.collision_names if 'render_mesh' not in (self.entries.get(n, {}).get('properties') or {}))
        rx = re.compile(self.args.leaf_regex)
        return sorted(n for n in self.collision_names if not rx.search(n))

    def select(self, pattern, default):
        """정규식(쉼표로 여러 개)과 맞는 메시 이름. pattern 이 없으면 default 목록."""
        if not pattern:
            return list(default)
        rxs = [re.compile(p) for p in pattern.split(',') if p]
        return sorted(n for n in self.meshes if any(rx.search(n) for rx in rxs))

    def world(self, name):
        if name not in self._world:
            self._world[name] = mesh_arrays(self.meshes[name])
        return self._world[name]

    def islands(self, name):
        if name not in self._islands:
            co, tri = self.world(name)
            self._islands[name] = islands(tri, len(co))
        return self._islands[name]

    @property
    def floor_z(self):
        zs = [f.get('floor_z_m') for f in self.files if f.get('floor_z_m') is not None]
        return float(min(zs)) if zs else 0.0

    def describe(self):
        return {'fbx': [os.path.basename(p) for p in self.fbx], 'scene': self.scene.name,
                'manifest': os.path.basename(self.args.manifest) if self.args.manifest else None,
                'markers_from': 'manifest' if self.manifest is not None else 'empties', 'meshes': len(self.meshes),
                'markers': len(self.markers), 'blender': bpy.app.version_string}


def prop(marker, *names, default=None):
    for n in names:
        if n in marker['properties'] and marker['properties'][n] not in (None, ''):
            return marker['properties'][n]
    return default


def door_requirements(a):
    return [(k, set(v.split('|'))) for k, v in (item.split('=', 1) for item in a.door_require)]


def door_required(marker, require):
    return all(str(prop(marker, k)) in vs for k, vs in require)


def skipped(reason):
    """돌리지 못한 검사. 통과로 치지 않는다(passed 는 None, 전체 요약의 not_run 에 들어간다)."""
    return {'skipped': reason, 'passed': None}


def finish(checks, level, a, script):
    passed = {k: (None if v.get('passed') is None else bool(v['passed'])) for k, v in checks.items()}
    ran = [v for v in passed.values() if v is not None]
    rep = {'generated_by': os.path.basename(script), 'inputs': level.describe(), 'seconds': round(time.time() - T0, 1),
           'passed': bool(ran) and all(ran), 'summary': passed, 'not_run': sorted(k for k, v in passed.items() if v is None), 'checks': checks}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, 'w', encoding='utf-8') as handle:
            json.dump(rep, handle, ensure_ascii=False, indent=1, default=float)
    print('QA_RESULT', json.dumps({'passed': rep['passed'], 'summary': passed, 'not_run': rep['not_run'], 'out': a.out}, ensure_ascii=False), flush=True)
    return rep


def main(doc, add_args, run, script):
    ap = base_parser(doc)
    add_args(ap)
    a = ap.parse_args(script_argv())
    level = Level(a)
    return finish(run(level, a), level, a, script)


def guard(fn):
    """Blender 백그라운드에서 예외가 나도 끝까지 출력하고 0 이 아닌 코드로 끝낸다."""
    try:
        fn()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        print('QA_ERROR', flush=True)
        os._exit(1)
