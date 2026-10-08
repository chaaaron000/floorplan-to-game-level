"""평면 추적 데이터 → IFC 원본 → 게임 메시 blend → 자체 검사 → 평면 겹침 그림·렌더.

프로젝트마다 복사해 고치지 않는 공용 빌더다. 작업자는 층별 추적 파일과 프로젝트 정의만 쓴다
(틀: references/trace_template.py, 형식과 검사 항목: references/plan_build.md).

실행(백그라운드 Blender 5.2 + Bonsai 확장의 ifcopenshell·shapely·PIL·numpy):
    "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" -b --python plan_build.py -- <project.py>
    ... -- <project.py> --no-render               렌더 생략
    ... -- <project.py> --no-source               Bonsai 로 IFC 원본 씬을 불러오지 않는다
    ... -- <project.py> --render-only [컷 ...]     저장된 blend 로 렌더만 다시(컷 이름을 적으면 그것만)
    ... -- <project.py> --out <폴더>               산출물 폴더(기본: 프로젝트 정의가 있는 폴더)

흐름
 1. compute()      층마다 추적 데이터(그 층 그림의 px)를 기준 층 px → 미터 평면 도형으로 바꾼다(shapely, 0.5mm 격자).
 2. write_ifc()    층(IfcBuildingStorey)마다 요소를 IFC4 로 쓴다. 모든 형상은 다각형 압출(곡선은 직선 조각).
 3. read_ifc()     저장한 IFC 를 다시 읽어 (다각형, z0, z1) 목록으로 되돌린다. 게임 메시는 이것만 쓴다.
 4. build_game()   평면 배치의 칸마다 높이 구간을 주고 겉면만 만든다 → 닫힌 메시, 안쪽 면 없음. Boolean 연산 없음.
 5. validate()     벽 틈, 문, 공간, 비매니폴드 엣지, 겹친 면, 층간 일치, 평면 대비 일치도를 validation.json 에 쓴다.
 6. plan_overlay() / render()
좌표: 원점은 기준 층 그림의 origin_px, 이미지 위쪽이 +Y, 맨 아래층 바닥 윗면이 Z=0.
"""
import importlib.util
import json
import math
import os
import sys
import time

try:
    import bpy
    from mathutils import Vector, Matrix
except ImportError:      # 추적 파일이 곡선 도우미만 불러 쓸 때(Blender 밖)
    bpy = None
try:
    import numpy as np
    import shapely
    import shapely.geometry as sg
    import shapely.ops as so
except ImportError:
    np = shapely = sg = so = None
try:
    import ifcopenshell
    import ifcopenshell.api
except ImportError:
    ifcopenshell = None


# ---------------------------------------------------------------- 추적 파일에서 불러 쓰는 곡선 도우미(px, 각도는 이미지 기준)
def arc(cx, cy, r, a0, a1, step=6.0):
    """호 위의 점들(a0→a1, 0°=동, 90°=남. 값이 커지면 화면에서 시계 방향). 고른 간격의 직선 조각으로 나눈다."""
    n = max(1, int(math.ceil(abs(a1 - a0) / step - 1e-9)))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def arc_pts(cx, cy, r, p0, p1, seg_px=60.0):
    """원 위에서 p0 쪽 각도에서 p1 쪽 각도까지(짧은 쪽으로) 고른 간격 조각. 큰 반지름 호에 쓴다."""
    a0 = math.degrees(math.atan2(p0[1] - cy, p0[0] - cx))
    a1 = math.degrees(math.atan2(p1[1] - cy, p1[0] - cx))
    d = (a1 - a0 + 180.0) % 360.0 - 180.0
    n = max(1, int(math.ceil(abs(math.radians(d)) * r / seg_px)))
    return [(cx + r * math.cos(math.radians(a0 + d * i / n)), cy + r * math.sin(math.radians(a0 + d * i / n))) for i in range(n + 1)]


def offset_pts(circle, pts, d):
    """원 (cx, cy, r) 위의 점들을 중심에서 멀어지는 쪽으로 d 만큼 옮긴다(d<0 이면 중심 쪽)."""
    cx, cy, r = circle
    out = []
    for x, y in pts:
        k = math.hypot(x - cx, y - cy)
        out.append((cx + (x - cx) * (k + d) / k, cy + (y - cy) * (k + d) / k))
    return out


def on_circle_x(cx, cy, r, x, lower):
    """원 위에서 주어진 x 의 점(lower=True 면 화면 아래쪽 점)"""
    h = math.sqrt(r * r - (x - cx) ** 2)
    return (x, cy + h if lower else cy - h)


# ---------------------------------------------------------------- 프로젝트 정의
PREC = 0.0005            # 평면 좌표 격자(m)

DEFAULTS = dict(
    name=None,               # 산출물 이름: <name>.ifc, <name>.blend, 씬 <name>_Game / <name>_IFC_Source
    title=None,              # IfcProject 이름(없으면 name)
    building='Building',     # IfcBuilding 이름
    prefix=None,             # 게임 재질 이름 M_<prefix>_* (없으면 name)
    scale=None,              # m/px (기준 층 그림)
    origin_px=None,          # 기준 층 그림에서 월드 원점이 되는 px
    floors=None,             # 아래층부터 [dict(key='L58', trace='trace_l58.py')]. 층마다 wall_h 를 따로 줄 수 있다
    wall_h=None,             # 층고 = 벽 높이(바닥 윗면 ~ 위 슬래브 아랫면)
    slab_t=0.25,             # 슬래브 두께
    finish_t=0.05,           # 게임 메시에서 위층 슬래브 윗부분을 바닥 마감 층으로 따로 뗀 두께
    door_h=2.30,             # 문 높이
    door_h_by_kind={},       # 문 종류별 높이(없는 종류는 door_h)
    leaf_t=0.045, leaf_gap=0.003, leaf_floor_gap=0.008,     # 문짝 두께, 옆·위 틈, 바닥 틈
    rail_h=0.10,             # 커튼월 아래·위 가로대 높이
    mullion_w=0.06,          # 멀리언 폭
    glass_t=0.024,           # 커튼월 유리 두께
    post_r=0.09,             # 유리가 꺾이는 점의 모서리 기둥 반지름
    glazing_ext=0.12,        # 유리 구간 묶음의 양 끝을 기둥·벽 안으로 늘리는 길이(겹친 부분은 뺀다. 구간마다 'ext' px 로 바꿀 수 있다)
    shower_glass_h=2.20, closet_front_h=2.40,
    curb_h=0.15,             # 테라스 가장자리 턱
    balustrade_h=1.10,       # 유리 난간 높이
    balustrade_glass_t=0.012,    # 턱 위 유리 난간 두께
    stair_risers=12,         # U자 계단 한 갈래 챌판 수
    stair_waist=0.15,        # U자 계단 높은 갈래 아랫면 두께
    cstair_t=0.18,           # 원형 계단 디딤판 두께
    cstair_guard=0.95,       # 원형 계단 스트링거(난간 벽) 높이: 디딤판 위로
    pool_shell=0.20,         # 수조 벽·바닥 두께
    water_below=(0.14, 0.10),    # 수면: 바닥 윗면에서 아래로 (물 아랫면, 물 윗면)
    arc_step_deg=6.0,        # 호·원을 나누는 각(호 모양 벽, 원형 파냄·구멍, 원형 계단)
    through_slab_columns=(),     # 슬래브를 뚫고 서는 기둥 종류(맨 아래층은 슬래브 아랫면부터, 위로는 위 슬래브 윗면까지)
    roof='own',              # 맨 위 천장 슬래브 범위: 'own' = 맨 위층 바닥판(테라스 포함), 'shell' = 유리 바깥 면 안쪽만
    stackable_roof=False,    # 맨 위 슬래브에도 맨 위층 HOLES·계단 구멍을 낸다(수조 제외)
    wall_kinds=None,         # 벽 종류 → (IFC ObjectType, IFC 표면 스타일, 게임 재질). 없으면 WALL_KINDS. 적힌 순서가 겹칠 때의 우선순위
    floor_kinds=None,        # 바닥 종류 → 게임 재질. 없으면 FLOOR_KINDS
    materials={},            # 게임 재질 덮어쓰기·추가: 이름(접두어 뺀 것) → (기본색, 거칠기, 금속성, 알파)
    plan_match=dict(gray=(0, 109), open_px=9, wall_kinds=None, column_kinds=None, exclude=(), exclude_margin_px=0),   # IFC 종류 또는 이름을 비교 양쪽에서 제외
    overlay_font_px=22,
    shots={},                # 실내 컷: 이름 → (층, 카메라 px, 바닥 위 높이 m, 바라보는 점 px, 그 점의 바닥 위 높이 m, 렌즈 mm). px 는 그 층 그림
    aerial=None,             # 조감 컷: dict(cam=(x, y, z), target=(x, y, z), lens=28, roof=False) 월드 m. 없으면 크기에 맞춰 잡는다. roof=True 면 천장을 덮은 채 찍는다
    light_energy=22.0,       # 방 조명 세기(면적^0.6 을 곱한다)
    light_radius=12.0,       # 실내 컷에서 카메라에서 이 거리(m) 안의 방 조명만 켠다
    light_limit=8,           # 실내 컷에서 반경 안 가장 가까운 방 조명 최대 수(POINT 그림자 버퍼)
    light_skip=('Shaft',),   # 이름에 이 글자가 든 공간에는 조명을 두지 않는다
)

WALL_KINDS = {   # 종류: (IFC ObjectType, IFC 표면 스타일, 게임 재질)
    'C': ('CORE', 'core', 'CoreConcrete'), 'M': ('MASS', 'core', 'CoreConcrete'), 'X': ('OUTSIDE_UNIT', 'core', 'OutsideUnit'),
    'R': ('RACK', 'rack', 'WineRack'), 'P': ('PARTITION', 'paint', 'WallPaint'), 'T': ('THIN', 'paint', 'WallPaint'), 'G': ('GLASS', 'glass', 'Glass'),
}
FLOOR_KINDS = {'wood': 'FloorWood', 'stone': 'FloorStone', 'bath': 'FloorBath', 'core': 'FloorCore', 'terrace': 'FloorTerrace', 'pool': 'FloorPoolDeck'}
ST = {   # IFC 표면 스타일: (이름, 색[, 투명도])
    'paint': ('Wall_Paint', (0.92, 0.91, 0.88)), 'core': ('Core_Concrete', (0.62, 0.62, 0.60)), 'column': ('Column_Concrete', (0.42, 0.42, 0.42)),
    'slab': ('Slab_Concrete', (0.55, 0.55, 0.55)), 'frame': ('CW_Frame', (0.12, 0.12, 0.13)), 'glass': ('CW_Glass', (0.55, 0.72, 0.80), 0.7),
    'door': ('Door_Leaf', (0.30, 0.22, 0.15)), 'metal': ('Elevator_Metal', (0.62, 0.63, 0.65)), 'case': ('Casework', (0.78, 0.74, 0.66)),
    'stair': ('Stair_Concrete', (0.50, 0.50, 0.50)), 'rack': ('Wine_Rack', (0.25, 0.16, 0.10)), 'pool': ('Pool_Tile', (0.35, 0.62, 0.70)),
    'water': ('Pool_Water', (0.30, 0.65, 0.85), 0.6),
}
GAME_MATS = {  # 이름(접두어 뺀 것): (기본색, 거칠기, 금속성, 알파)
    'WallPaint': ((0.86, 0.85, 0.82), 0.85, 0.0, 1.0),
    'CoreConcrete': ((0.50, 0.50, 0.48), 0.9, 0.0, 1.0),
    'OutsideUnit': ((0.42, 0.42, 0.41), 0.9, 0.0, 1.0),
    'ColumnConcrete': ((0.26, 0.26, 0.27), 0.8, 0.0, 1.0),
    'WineRack': ((0.16, 0.09, 0.05), 0.5, 0.0, 1.0),
    'Slab': ((0.40, 0.40, 0.40), 0.9, 0.0, 1.0),
    'FloorWood': ((0.42, 0.28, 0.16), 0.55, 0.0, 1.0),
    'FloorStone': ((0.78, 0.76, 0.72), 0.35, 0.0, 1.0),
    'FloorBath': ((0.60, 0.62, 0.64), 0.3, 0.0, 1.0),
    'FloorCore': ((0.36, 0.36, 0.36), 0.8, 0.0, 1.0),
    'FloorTerrace': ((0.50, 0.47, 0.42), 0.8, 0.0, 1.0),
    'FloorPoolDeck': ((0.70, 0.68, 0.62), 0.6, 0.0, 1.0),
    'PoolTile': ((0.20, 0.50, 0.62), 0.25, 0.0, 1.0),
    'PoolWater': ((0.25, 0.62, 0.85), 0.05, 0.0, 0.45),
    'Ceiling': ((0.90, 0.90, 0.90), 0.9, 0.0, 1.0),
    'Frame': ((0.05, 0.05, 0.055), 0.4, 0.8, 1.0),
    'Glass': ((0.70, 0.85, 0.90), 0.05, 0.0, 0.18),
    'Door': ((0.20, 0.13, 0.08), 0.5, 0.0, 1.0),
    'ElevatorMetal': ((0.55, 0.56, 0.58), 0.3, 0.9, 1.0),
    'Casework': ((0.70, 0.65, 0.56), 0.5, 0.0, 1.0),
    'Stair': ((0.45, 0.45, 0.45), 0.85, 0.0, 1.0),
}


class Frame:
    """층 그림 px ↔ 기준 층 px. TO_REF=(a, bx, by): 기준 = a·px + b / FROM_REF=(s, tx, ty): px = s·기준 + t / 둘 다 없으면 그대로."""

    def __init__(self, to_ref=None, from_ref=None):
        assert not (to_ref and from_ref), 'TO_REF 와 FROM_REF 는 하나만 적는다'
        self.to_ref, self.from_ref = to_ref, from_ref

    def R(self, p):
        """층 px → 기준 px"""
        if self.from_ref:
            s, tx, ty = self.from_ref
            return ((p[0] - tx) / s, (p[1] - ty) / s)
        if self.to_ref:
            a, bx, by = self.to_ref
            return (a * p[0] + bx, a * p[1] + by)
        return (float(p[0]), float(p[1]))

    def inv(self, p):
        """기준 px → 층 px"""
        if self.from_ref:
            s, tx, ty = self.from_ref
            return (p[0] * s + tx, p[1] * s + ty)
        if self.to_ref:
            a, bx, by = self.to_ref
            return ((p[0] - bx) / a, (p[1] - by) / a)
        return (p[0], p[1])

    def Rl(self, v):
        """길이: 층 px → 기준 px"""
        if self.from_ref:
            return v / self.from_ref[0]
        if self.to_ref:
            return v * self.to_ref[0]
        return v

    def M(self, v):
        """길이: 층 px → m"""
        if self.from_ref:
            return v * C.s / self.from_ref[0]
        if self.to_ref:
            return v * C.s * self.to_ref[0]
        return v * C.s

    def P(self, p):
        """층 px → 월드(m)"""
        return W(self.R(p))


class Floor:
    def px(self, x, y):
        """월드(m) → 이 층 그림 px"""
        return self.fr.inv((x / C.s + C.ox, C.oy - y / C.s))


class _Cfg:
    pass


C = _Cfg()
IDENT = Frame()
LOG = {}


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod      # 위층 추적 파일이 아래층 추적 파일을 import 로 불러 쓸 수 있게
    spec.loader.exec_module(mod)
    return mod


def load_project(path, out=None):
    path = os.path.abspath(path)
    here = os.path.dirname(path)
    sys.dont_write_bytecode = True      # 프로젝트 폴더에 __pycache__ 를 남기지 않는다
    for d in (os.path.dirname(os.path.abspath(__file__)), here):
        if d not in sys.path:
            sys.path.insert(0, d)
    P = _load_module('plan_project', path).PROJECT
    unknown = [k for k in P if k not in DEFAULTS]
    if unknown:
        raise ValueError('프로젝트 정의에 모르는 항목: %s' % unknown)
    cfg = dict(DEFAULTS)
    cfg.update(P)
    for k in ('name', 'scale', 'origin_px', 'floors', 'wall_h'):
        if cfg[k] is None:
            raise ValueError('프로젝트 정의에 %s 가 없다' % k)
    C.__dict__.clear()
    C.__dict__.update(cfg)
    C.here, C.out = here, os.path.abspath(out or here)
    C.s = cfg['scale']
    C.ox, C.oy = cfg['origin_px']
    C.title = cfg['title'] or cfg['name']
    C.prefix = cfg['prefix'] or cfg['name']
    C.wall_kinds = cfg['wall_kinds'] or WALL_KINDS
    C.floor_kinds = cfg['floor_kinds'] or FLOOR_KINDS
    C.mats = dict(GAME_MATS)
    C.mats.update(cfg['materials'])
    pm = dict(DEFAULTS['plan_match'])
    pm.update(cfg['plan_match'] or {})
    C.plan_match = pm
    C.game_scene, C.source_scene = cfg['name'] + '_Game', cfg['name'] + '_IFC_Source'
    C.ifc_path = os.path.join(C.out, cfg['name'] + '.ifc')
    C.blend_path = os.path.join(C.out, cfg['name'] + '.blend')
    C.valid_path = os.path.join(C.out, 'validation.json')
    C.render_dir = os.path.join(C.out, 'renders')
    C.floors, C.fl, z = [], {}, 0.0
    for i, f in enumerate(cfg['floors']):
        fl = Floor()
        tpath = os.path.join(here, f['trace'])
        fl.key, fl.index, fl.z, fl.h = f['key'], i, z, f.get('wall_h', cfg['wall_h'])
        fl.T = _load_module(os.path.splitext(os.path.basename(tpath))[0], tpath)
        fl.fr = Frame(getattr(fl.T, 'TO_REF', None), getattr(fl.T, 'FROM_REF', None))
        fl.image = os.path.join(os.path.dirname(tpath), fl.T.IMAGE)
        z = fl.z + fl.h + C.slab_t
        C.floors.append(fl)
        C.fl[fl.key] = fl
    C.keys = [fl.key for fl in C.floors]
    LOG.clear()
    return C


def storey_h(fl):
    """이 층 바닥 윗면에서 위층 바닥 윗면까지"""
    return fl.h + C.slab_t


def rows(fl, name):
    """추적 파일의 표 name(그 층 그림 px)과 name_REF(기준 층 px)의 항목을 (좌표틀, 항목) 으로 차례로 낸다. 사전이면 항목은 (이름, 값)."""
    for fr, nm in ((fl.fr, name), (IDENT, name + '_REF')):
        t = getattr(fl.T, nm, None)
        if not t:
            continue
        for it in (t.items() if isinstance(t, dict) else t):
            yield fr, it


# ---------------------------------------------------------------- 좌표·도형 도우미
def W(p):
    """기준 층 px → 월드(m)"""
    return ((p[0] - C.ox) * C.s, (C.oy - p[1]) * C.s)


def snap(g):
    return shapely.set_precision(g, PREC)


def _areal(g):
    if g.geom_type == 'GeometryCollection':
        return sg.MultiPolygon([p for p in g.geoms if p.geom_type == 'Polygon'] + [q for p in g.geoms if p.geom_type == 'MultiPolygon' for q in p.geoms])
    return g if g.geom_type in ('Polygon', 'MultiPolygon') else sg.Polygon()


def UNI(gs):
    gs = [g for g in gs if g is not None and not g.is_empty]
    if not gs:
        return sg.Polygon()
    return _areal(shapely.union_all(gs, grid_size=PREC))


def DIFF(a, b):
    if a.is_empty or b.is_empty:
        return a
    return _areal(shapely.difference(a, b, grid_size=PREC))


def INT(a, b):
    if a.is_empty or b.is_empty:
        return sg.Polygon()
    return _areal(shapely.intersection(a, b, grid_size=PREC))


def polys(g, min_area=1e-7):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        out = [g]
    elif g.geom_type in ('MultiPolygon', 'GeometryCollection'):
        out = [p for p in g.geoms if p.geom_type == 'Polygon']
    else:
        out = []
    return [p for p in out if p.area > min_area]


def biggest(g):
    ps = polys(g)
    return max(ps, key=lambda p: p.area) if ps else sg.Polygon()


def rect(x0, y0, x1, y1):
    """기준 px 사각형"""
    (ax, ay), (bx, by) = W((x0, y0)), W((x1, y1))
    return snap(sg.box(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))


def poly(pts):
    """기준 px 다각형"""
    g = sg.Polygon([W(p) for p in pts])
    if not g.is_valid:
        g = biggest(_areal(g.buffer(0)))
    return snap(g)


def rect_f(fr, x0, y0, x1, y1):
    a, b = fr.R((x0, y0)), fr.R((x1, y1))
    return rect(a[0], a[1], b[0], b[1])


def poly_f(fr, pts):
    return poly([fr.R(p) for p in pts])


def sector(fr, cx, cy, r0, r1, a0, a1):
    """고리 조각(안 반지름 r0, 밖 반지름 r1, 각 a0→a1)"""
    c, r0, r1, st = fr.R((cx, cy)), fr.Rl(r0), fr.Rl(r1), C.arc_step_deg
    return poly(arc(c[0], c[1], r1, a0, a1, st) + arc(c[0], c[1], r0, a1, a0, st))


def octagon(x0, y0, x1, y1, c):
    return poly([(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)])


def shape(fr, s, column=False):
    """('disk', cx, cy, r) | ('rect', x0, y0, x1, y1) | ('poly', 점들) | ('oct', x0, y0, x1, y1, 모따기).
    끝에 (x0, y0, x1, y1) 을 하나 더 적으면 그 범위 안만 남긴다. column=True 면 원을 32각 기둥으로 만든다."""
    n = {'disk': 3, 'rect': 4, 'poly': 1, 'oct': 5}[s[0]]
    a, clip = s[1:1 + n], (s[1 + n] if len(s) > 1 + n else None)
    if s[0] == 'disk':
        if column:
            g = snap(sg.Point(fr.P(a[:2])).buffer(fr.M(a[2]), quad_segs=8))
        else:
            c = fr.R(a[:2])
            g = poly(arc(c[0], c[1], fr.Rl(a[2]), 0, 360, C.arc_step_deg)[:-1])
    elif s[0] == 'rect':
        g = rect_f(fr, *a)
    elif s[0] == 'poly':
        g = poly_f(fr, a[0])
    else:
        p, q = fr.R(a[:2]), fr.R(a[2:4])
        g = octagon(p[0], p[1], q[0], q[1], fr.Rl(a[4]))
    return INT(g, rect_f(fr, *clip)) if clip else g


# ---------------------------------------------------------------- 1. 추적 데이터 → 평면 도형
def _snap_rects(rects, below, tol):
    """기준 px 로 옮긴 이 층 벽 사각형의 가장자리를, 옆에 놓인 아래층 벽 가장자리와 tol(px) 안이면 그 값에 맞춘다."""
    margin, moved, out = 40.0, 0, {}
    for name, (x0, y0, x1, y1) in rects.items():
        vals = [x0, y0, x1, y1]
        for k in range(4):
            horiz = k in (0, 2)     # x 가장자리
            best = None
            for qx0, qy0, qx1, qy1 in below:
                if horiz and (qy1 < y0 - margin or qy0 > y1 + margin):
                    continue
                if not horiz and (qx1 < x0 - margin or qx0 > x1 + margin):
                    continue
                for c in ((qx0, qx1) if horiz else (qy0, qy1)):
                    d = abs(c - vals[k])
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, c)
            if best is not None and best[0] > 1e-9:
                vals[k] = best[1]
                moved += 1
        if vals[2] - vals[0] < 4 or vals[3] - vals[1] < 4:      # 두 가장자리가 한 값으로 붙으면 맞추지 않는다
            vals = [x0, y0, x1, y1]
        out[name] = tuple(vals)
    return out, moved


def compute_floor(fl, below, span_cols):
    """한 층의 평면 도형. below 는 바로 아래층의 결과, span_cols 는 아래층에서 올라와 이 층을 지나는 기둥."""
    T, f0, key = fl.T, fl.fr, fl.key
    G = dict(key=key)
    t = f0.M(T.GLAZING_THICKNESS_PX)
    interior = poly_f(f0, T.OUTLINE)
    inner = biggest(snap(interior.buffer(-t / 2, join_style='mitre', mitre_limit=5.0)))     # 유리 안쪽 면
    shell = biggest(snap(interior.buffer(t / 2, join_style='mitre', mitre_limit=5.0)))      # 유리 바깥 면
    curtain = getattr(T, 'CURTAIN', None)
    clip = (lambda g: INT(g, inner)) if curtain is not None else (lambda g: g)   # 닫힌 중심선 유리가 있으면 벽·붙박이는 유리 안쪽 면에서 잘린다
    G.update(interior=interior, inner=inner, shell=shell, t=t)
    cut = UNI([shape(f, s) for f, s in rows(fl, 'CUTOUTS')])

    # --- 벽
    rects, meta = {}, {}
    for f, (name, kind, x0, y0, x1, y1) in rows(fl, 'WALLS'):
        assert name not in rects, (key, name, '벽 이름이 겹친다')
        a, b = f.R((x0, y0)), f.R((x1, y1))
        rects[name] = (a[0], a[1], b[0], b[1])
        meta[name] = (kind, 'x' if (x1 - x0) >= (y1 - y0) else 'y')
    tol = getattr(T, 'SNAP_TO_BELOW_PX', None)
    if below is not None and tol:
        rects, moved = _snap_rects(rects, [w['px'] for w in below['walls'].values() if w.get('px')], tol)
        LOG[key + '_edges_snapped_to_below'] = moved
    walls = {}
    for name, px in rects.items():
        walls[name] = dict(name=name, kind=meta[name][0], px=px, poly=DIFF(clip(rect(*px)), cut), axis=meta[name][1])
    for f, (name, kind, pts) in rows(fl, 'POLY_WALLS'):
        assert name not in walls, (key, name, '벽 이름이 겹친다')
        walls[name] = dict(name=name, kind=kind, px=None, poly=DIFF(clip(poly_f(f, pts)), cut), axis=None)
    for f, (name, kind, cx, cy, r0, r1, a0, a1) in rows(fl, 'ARC_WALLS'):
        assert name not in walls, (key, name, '벽 이름이 겹친다')
        walls[name] = dict(name=name, kind=kind, px=None, poly=clip(sector(f, cx, cy, r0, r1, a0, a1)), axis=None)
    carve = [rect_f(f, *c[1:]) for f, c in rows(fl, 'CARVES')] + [poly_f(f, pts) for f, (_, pts) in rows(fl, 'POLY_CARVES')]
    if carve:      # 방 범위에서 벽을 구한다: 유리 안쪽에서 방을 뺀 나머지가 한 덩어리 벽
        cname, ckind = getattr(T, 'CARVE_WALL', ('Mass', 'X'))
        walls[cname] = dict(name=cname, kind=ckind, px=None, poly=DIFF(inner, UNI(carve)), axis=None)
    for w in walls.values():
        assert w['kind'] in C.wall_kinds, (key, w['name'], '모르는 벽 종류 ' + w['kind'])
    G['walls'] = walls

    # --- 기둥
    cols = {}
    for f, (name, spec) in rows(fl, 'COLUMNS'):
        opt = spec[2] if len(spec) > 2 else {}
        g = shape(f, spec[1], column=True)
        cols[name] = dict(name=name, kind=spec[0], poly=g, to=opt.get('to'), from_index=fl.index,
                          center=f.P(spec[1][1:3]) if spec[1][0] == 'disk' else g.centroid.coords[0])
    G['cols'] = cols
    thruU = UNI([c['poly'] for c in cols.values() if c['kind'] in C.through_slab_columns])
    colU = UNI([c['poly'] for c in cols.values()] + [c['poly'] for c in span_cols])
    wallU = UNI([w['poly'] for w in walls.values()])
    U = UNI([w['poly'] for w in walls.values()] + [colU])
    G.update(U=U, colU=colU, wallU=wallU, thruU=thruU)

    # --- 문: 호스트 벽 사각형을 문 구간으로 자른 것이 개구부 평면
    doors = {}
    for f, (name, (host, a, b, kind, hinge, side)) in rows(fl, 'DOORS'):
        w = walls[host]
        assert w['px'], (key, name, '호스트가 사각형 벽이 아니다(RECT_DOORS 로 적는다)')
        x0, y0, x1, y1 = w['px']
        k = 1 if w['axis'] == 'y' else 0
        start, end = f.R((a, a))[k], f.R((b, b))[k]
        if start < (y0 if k else x0) - 1e-6 or end > (y1 if k else x1) + 1e-6:
            raise ValueError(f'{key} 문 {name}: 문 구간이 호스트 벽보다 길다')
        if w['axis'] == 'y':
            a2, b2 = max(f.R((0, a))[1], y0), min(f.R((0, b))[1], y1)
            opx = (x0, a2, x1, b2)
        else:
            a2, b2 = max(f.R((a, 0))[0], x0), min(f.R((b, 0))[0], x1)
            opx = (a2, y0, b2, y1)
        assert b2 - a2 > 20, (key, name, '문 구간이 호스트 벽 밖에 있다')
        doors[name] = dict(name=name, host=host, kind=kind, hinge=hinge, side=side, axis=w['axis'], px=opx, poly=rect(*opx),
                           width=(b2 - a2) * C.s, thick=min(x1 - x0, y1 - y0) * C.s, height=C.door_h_by_kind.get(kind, C.door_h))
    for f, (name, spec) in rows(fl, 'RECT_DOORS'):
        x0, y0, x1, y1, axis, kind, hinge, side = spec[:8]
        opt = spec[8] if len(spec) > 8 else {}
        a, b = f.R((x0, y0)), f.R((x1, y1))
        opx = (a[0], a[1], b[0], b[1])
        g = rect(*opx)
        host = opt['host'] if 'host' in opt else max(walls.values(), key=lambda w: g.intersection(w['poly']).area)['name']
        doors[name] = dict(name=name, host=host, kind=kind, hinge=hinge, side=side, axis=axis, px=opx, poly=g,
                           width=((b[0] - a[0]) if axis == 'x' else (b[1] - a[1])) * C.s, thick=((b[1] - a[1]) if axis == 'x' else (b[0] - a[0])) * C.s,
                           height=C.door_h_by_kind.get(kind, C.door_h))
    G['doors'] = doors
    arc_doors = {}
    for f, (name, (cx, cy, r0, r1, a0, a1)) in rows(fl, 'ARC_DOORS'):
        c = f.R((cx, cy))
        arc_doors[name] = dict(poly=sector(f, cx, cy, r0, r1, a0, a1),
                               line=sg.LineString([W(p) for p in arc(c[0], c[1], f.Rl((r0 + r1) / 2), a0, a1, C.arc_step_deg)]))
    G['arc_doors'] = arc_doors

    # --- 유리 1: 닫힌 중심선(OUTLINE) 전체가 커튼월. 멀리언은 고른 간격, 꺾이는 모서리에 모서리 기둥
    glazing = {}
    B = sg.Polygon()
    if curtain is not None:
        ring = interior.exterior
        band = DIFF(shell, inner)
        n = max(3, int(round(ring.length / curtain.get('mullion_spacing_m', 1.15))))
        mp = []
        for i in range(n):
            d = ring.length * i / n
            p, q = np.array(ring.interpolate(d).coords[0]), np.array(ring.interpolate(min(d + 0.05, ring.length)).coords[0])
            dv = (q - p) / max(np.linalg.norm(q - p), 1e-9)
            mp.append(sg.LineString([p - dv * C.mullion_w / 2, p + dv * C.mullion_w / 2]).buffer(t / 2 + 0.01, cap_style='flat'))
        for c in curtain.get('corners', []):
            mp.append(sg.Point(f0.P(c)).buffer(C.post_r))
        mull = INT(UNI([snap(m) for m in mp]), band)
        glass = [dict(poly=g, door=False) for g in polys(DIFF(INT(snap(ring.buffer(C.glass_t / 2)), band), mull))]
        glazing['CW'] = dict(name='CW', band=band, mullions=mull, rails=DIFF(band, mull), glass=glass)
        LOG[key + '_mullions'] = n
    # --- 유리 2: 구간 묶음(중심선 꺾은선마다 띠). 벽·기둥과 앞선 띠에 겹친 부분은 뺀다
    for f, (name, spec) in rows(fl, 'GLAZING'):
        pts, mull, axis, bays = spec[:4]
        opt = spec[4] if len(spec) > 4 else {}
        tb = f.M(opt.get('thick', T.GLAZING_THICKNESS_PX))
        e0, e1 = [f.M(v) for v in opt['ext']] if 'ext' in opt else (C.glazing_ext, C.glazing_ext)   # 양 끝을 기둥 안으로 늘려 사선 끝과 기둥 면 사이에 틈이 남지 않게 한다
        wp = [np.array(f.P(p)) for p in pts]
        d0 = (wp[0] - wp[1]) / np.linalg.norm(wp[0] - wp[1])
        d1 = (wp[-1] - wp[-2]) / np.linalg.norm(wp[-1] - wp[-2])
        line = sg.LineString([wp[0] + d0 * e0] + wp[1:-1] + [wp[-1] + d1 * e1])
        band = snap(line.buffer(tb / 2, cap_style='flat', join_style='mitre', mitre_limit=5.0))
        band = DIFF(DIFF(band, U), B)
        mpolys = []
        k = 0 if axis == 'x' else 1
        for c in mull:      # 멀리언: 좌표 c 에서 중심선과 만나는 점, 구간 방향으로 mullion_w
            for p, q, pw, qw in zip(pts[:-1], pts[1:], wp[:-1], wp[1:]):
                lo, hi = sorted((p[k], q[k]))
                if lo <= c <= hi and hi > lo:
                    fq = (c - p[k]) / (q[k] - p[k])
                    m, d = pw + (qw - pw) * fq, (qw - pw) / np.linalg.norm(qw - pw)
                    mpolys.append(sg.LineString([m - d * C.mullion_w / 2, m + d * C.mullion_w / 2]).buffer(tb / 2 + 0.01, cap_style='flat'))
                    break
            else:
                LOG.setdefault('problems', []).append(f'{key} 유리 {name}: 멀리언 좌표 {c} 가 중심선 범위 밖')
        if opt.get('posts', True):
            for v in wp[1:-1]:      # 꺾이는 점의 모서리 기둥
                mpolys.append(sg.Point(v).buffer(C.post_r))
        mull_fp = INT(UNI([snap(m) for m in mpolys]), band) if mpolys else sg.Polygon()
        glass_band = snap(line.buffer(C.glass_t / 2, cap_style='flat', join_style='mitre', mitre_limit=5.0))
        gl = []
        for g in polys(DIFF(INT(glass_band, band), mull_fp)):
            rp = g.representative_point()
            c = fl.px(rp.x, rp.y)[k] if f is f0 else (rp.x / C.s + C.ox, C.oy - rp.y / C.s)[k]
            gl.append(dict(poly=g, door=any(a <= c <= b for a, b in bays)))
        assert name not in glazing, (key, name, '유리 이름이 겹친다')
        glazing[name] = dict(name=name, band=band, mullions=mull_fp, rails=DIFF(band, mull_fp), glass=gl)
        B = UNI([B, band])
    G['glazing'] = glazing
    G['B'] = UNI([B] + ([glazing['CW']['band']] if curtain is not None else []))
    BB = G['B']

    # --- 바닥판, 실내, 테라스
    own = biggest(DIFF(UNI([shell] + [shape(f, s) for f, s in rows(fl, 'PLATE')]), thruU))
    free = DIFF(DIFF(inner if curtain is not None else interior, B), U)
    terrace = DIFF(DIFF(DIFF(own, shell), BB), U)
    G.update(own=own, free=free, terrace=terrace)

    # --- 난간(중심선 + 두께)
    rails = {}
    for f, (name, (pts, th)) in rows(fl, 'RAILS'):
        line = sg.LineString([f.P(p) for p in pts])
        rails[name] = dict(line=line, poly=DIFF(DIFF(snap(line.buffer(f.M(th) / 2, cap_style='flat', join_style='mitre')), U), BB))
    G['rails'] = rails

    # --- 공간: (실내 − 벽) 과 테라스를 가상 경계로 나눈 칸에 시드로 이름을 붙인다
    cutl = [free.boundary, terrace.boundary]
    for f, (_, pts) in rows(fl, 'VIRTUAL_SPLITS'):
        cutl.append(sg.LineString([f.P(p) for p in pts]))
    for d in arc_doors.values():
        cutl.append(d['line'])
    for name in getattr(T, 'SPLIT_RAILS', []):
        cutl.append(rails[name]['line'])
    net = shapely.unary_union(cutl, grid_size=PREC)
    both = UNI([free, terrace])
    faces = [fc for fc in so.polygonize(net) if fc.area > 1e-4 and both.contains(fc.representative_point())]
    spaces, used, errs = [], set(), []
    for f, (name, ko, seed, floor) in rows(fl, 'SPACES'):
        pt = sg.Point(f.P(seed))
        hit = [i for i, fc in enumerate(faces) if fc.contains(pt)]
        if len(hit) != 1 or hit[0] in used:
            errs.append((name, hit))
            continue
        if floor != 'void' and floor not in C.floor_kinds:
            LOG.setdefault('problems', []).append(f'{key} 공간 {name}: 모르는 바닥 종류 {floor}')
        used.add(hit[0])
        spaces.append(dict(name=name, ko=ko, floor=floor, poly=faces[hit[0]]))
    unnamed = [fc for i, fc in enumerate(faces) if i not in used]
    LOG[key + '_space_errors'] = errs
    LOG[key + '_unnamed_faces'] = [dict(area=round(fc.area, 3), px=[round(v) for v in fl.px(*fc.representative_point().coords[0])]) for fc in unnamed if fc.area > 0.02]
    for i, fc in enumerate(unnamed):
        if fc.area > 0.05:
            spaces.append(dict(name=f'Unnamed{i}', ko='이름 없는 칸', floor='terrace' if terrace.contains(fc.representative_point()) else 'stone', poly=fc))
    G['spaces'] = spaces

    # --- 붙박이·유리 칸막이·붙박이장 앞판
    G['casework'] = {name: dict(poly=DIFF(clip(rect_f(f, x0, y0, x1, y1)), U), h=h) for f, (name, (x0, y0, x1, y1, h)) in rows(fl, 'CASEWORK')}
    G['glass_parts'] = {name: DIFF(rect_f(f, *r), U) for f, (name, r) in rows(fl, 'GLASS_PARTITIONS')}
    fronts = {}
    for f, (name, (x0, y0, x1, y1)) in rows(fl, 'CLOSET_FRONTS'):      # 긴 쪽으로 두 짝이 겹친다
        (ax, ay), (bx, by) = f.P((x0, y1)), f.P((x1, y0))
        ov = 0.03
        if (bx - ax) >= (by - ay):
            cy, mid = (ay + by) / 2, (ax + bx) / 2
            fronts[name] = [sg.box(ax + 0.004, cy - 0.0225, mid + ov, cy - 0.0025), sg.box(mid - ov, cy + 0.0025, bx - 0.004, cy + 0.0225)]
        else:
            cx, mid = (ax + bx) / 2, (ay + by) / 2
            fronts[name] = [sg.box(cx - 0.0225, ay + 0.004, cx - 0.0025, mid + ov), sg.box(cx + 0.0025, mid - ov, cx + 0.0225, by - 0.004)]
    G['closet_fronts'] = fronts

    # --- 테라스 가장자리: 턱 사각형(가운데에 유리 난간) / 바닥판 가장자리에서 들어온 유리 난간
    curbs, curb_glass = {}, {}
    for f, (name, (x0, y0, x1, y1)) in rows(fl, 'TERRACE_CURBS'):
        a, b = f.R((x0, y0)), f.R((x1, y1))
        curbs[name] = DIFF(DIFF(DIFF(rect(a[0], a[1], b[0], b[1]), shell), U), BB)
        hw = C.balustrade_glass_t / 2 / C.s
        if abs(b[1] - a[1]) >= abs(b[0] - a[0]):
            xm = (a[0] + b[0]) / 2
            g = rect(xm - hw, a[1], xm + hw, b[1])
        else:
            ym = (a[1] + b[1]) / 2
            g = rect(a[0], ym - hw, b[0], ym + hw)
        curb_glass[name] = DIFF(DIFF(DIFF(g, shell), U), BB)
    G['curbs'], G['curb_glass'] = curbs, curb_glass
    ins = getattr(T, 'BALUSTRADE_INSET_PX', None)
    G['balustrade'] = sg.Polygon()
    if ins is not None:
        ins = f0.M(ins)
        bal = DIFF(snap(own.buffer(-(ins - C.glass_t / 2))), snap(own.buffer(-(ins + C.glass_t / 2))))
        G['balustrade'] = DIFF(DIFF(bal, snap(shell.buffer(0.03))), colU)
    return G


def stair_to_ref(f, d):
    """U자 계단 정의(층 px)를 기준 px 로 옮긴다."""
    k = 0 if d['axis'] == 'x' else 1
    out = dict(axis=d['axis'], kind=d.get('kind', 'u'), risers=d.get('risers', C.stair_risers))
    out['run'] = tuple(f.R((v, v))[k] for v in d['run'])
    out['far'] = f.R((d['far'], d['far']))[k]
    for ln in ('lane_a', 'lane_b'):
        out[ln] = tuple(f.R((v, v))[1 - k] for v in d[ln])
    sp = d.get('spine')
    out['spine'] = (f.R(sp[:2]) + f.R(sp[2:])) if sp else None
    return out


def ustair_cells(d, fl, full):
    """U자 계단(기준 px 정의). 첫 갈래(lane_a)가 run[0]→run[1] 로 올라가 중간 참(far 쪽)에서 돌아 둘째 갈래(lane_b)로 run[0] 쪽 위층 바닥에 닿는다.
    full=False(위층이 없다)면 천장에서 잘린다. 높이는 층 바닥 기준. 반환 (칸 목록, 위층 슬래브 구멍)."""
    risers = d['risers']
    if d['kind'] == 'scissor':
        return scissor_cells(d, fl, full)
    if d['kind'] != 'u':
        raise ValueError(f"모르는 계단 종류: {d['kind']}")
    r = storey_h(fl) / (2 * risers)
    top_cut = storey_h(fl) if full else fl.h
    n = risers - 1
    r0, r1 = d['run']
    step = (r1 - r0) / n

    def rc(a, b, lane):
        if d['axis'] == 'x':
            return rect(min(a, b), lane[0], max(a, b), lane[1])
        return rect(lane[0], min(a, b), lane[1], max(a, b))

    lo, hi = min(d['lane_a'][0], d['lane_b'][0]), max(d['lane_a'][1], d['lane_b'][1])
    hole = rc(r0, d['far'], (lo, hi))
    spine = rect(*d['spine']) if d['spine'] else sg.Polygon()
    cells = []
    if top_cut > fl.h:      # 구멍 안의 가운데 벽은 위층 바닥까지, 구멍 밖(위층 슬래브 아래)은 천장까지
        cells.append((DIFF(spine, hole), 0.0, fl.h))
        cells.append((INT(spine, hole), 0.0, top_cut))
    else:
        cells.append((spine, 0.0, fl.h))
    for i in range(n):
        cells.append((rc(r0 + i * step, r0 + (i + 1) * step, d['lane_a']), 0.0, (i + 1) * r))
    mid = risers * r
    cells.append((DIFF(rc(r1, d['far'], (lo, hi)), spine), mid - 0.20, mid))
    for j in range(n):
        top, z0 = mid + (j + 1) * r, mid + j * r - C.stair_waist
        if z0 >= top_cut - 0.02:
            continue
        cells.append((rc(r1 - j * step, r1 - (j + 1) * step, d['lane_b']), z0, min(top, top_cut)))
    return [(p, round(a, 4), round(b, 4)) for p, a, b in cells if not p.is_empty], hole


def scissor_cells(d, fl, full):
    """서로 반대 방향으로 한 층씩 오르는 두 직선 갈래. far 쪽은 위·아래 도착 참."""
    risers = d['risers']
    if not isinstance(risers, int) or risers < 2:
        raise ValueError('scissor risers는 2 이상의 정수여야 한다')
    rise = storey_h(fl) / risers
    top_cut = storey_h(fl) if full else fl.h
    r0, r1 = d['run']
    n = risers - 1
    step = (r1 - r0) / n

    def rc(a, b, lane):
        if d['axis'] == 'x':
            return rect(min(a, b), lane[0], max(a, b), lane[1])
        return rect(lane[0], min(a, b), lane[1], max(a, b))

    lo = min(d['lane_a'][0], d['lane_b'][0])
    hi = max(d['lane_a'][1], d['lane_b'][1])
    hole = rc(r0, d['far'], (lo, hi))
    spine = rect(*d['spine']) if d['spine'] else sg.Polygon()
    cells = [(DIFF(spine, hole), -C.slab_t, fl.h), (INT(spine, hole), -C.slab_t, top_cut)]
    cells.append((DIFF(rc(r1, d['far'], d['lane_b']), spine), -C.slab_t, 0.0))
    if full:
        cells.append((DIFF(rc(r1, d['far'], d['lane_a']), spine), top_cut - C.slab_t, top_cut))
    for lane, start, delta in ((d['lane_a'], r0, step), (d['lane_b'], r1, -step)):
        for i in range(n):
            top = min((i + 1) * rise, top_cut)
            bottom = max(-C.slab_t, i * rise - C.stair_waist)
            if bottom < top:
                cells.append((rc(start + i * delta, start + (i + 1) * delta, lane), bottom, top))
    return [(p, round(a, 4), round(b, 4)) for p, a, b in cells if not p.is_empty], hole


def circ_stair_cells(f, d, fl):
    """원형·나선 계단: 곧은 디딤판 straight 개 → 돌음 디딤판 winders 개 → 곧은 디딤판 straight 개(straight=0 이면 나선).
    반환 (디딤판 칸, 스트링거 칸, 요약). 높이는 층 바닥 기준. 올라가는 방향 = a_start → a_end."""
    cx, cy = f.R(d['center'])
    a0, a1, nw, ns = d['a_start'], d['a_end'], d['winders'], d.get('straight', 0)
    g = f.Rl(d.get('going_px', 0.0))
    sign = 1.0 if a1 > a0 else -1.0
    n = 2 * ns + nw
    h = storey_h(fl) / (n + 1)

    def m(a):   # 걸어가는 방향(이미지 좌표)
        return (-math.sin(math.radians(a)) * sign, math.cos(math.radians(a)) * sign)

    def pt(a, r, s):    # 접점 각 a, 반지름 r, 접선 방향으로 s 만큼
        mx, my = m(a)
        return (cx + r * math.cos(math.radians(a)) + mx * s, cy + r * math.sin(math.radians(a)) + my * s)

    def sec(r0, r1, b0, b1):
        st = C.arc_step_deg
        return poly(arc(cx, cy, r1, b0, b1, st) + arc(cx, cy, r0, b1, b0, st))

    da = (a1 - a0) / nw
    bands = [(d['r_tread'], 0, 0.0)] + [(d[k], 1, C.cstair_guard) for k in ('r_in', 'r_out') if d.get(k)]
    treads, guards = [], []
    for i in range(n):
        z = (i + 1) * h
        for (r0, r1), which, up in bands:
            r0, r1 = f.Rl(r0), f.Rl(r1)
            if i < ns:              # 들어가는 곧은 디딤판(접점 뒤쪽)
                s0, s1 = -(ns - i) * g, -(ns - i - 1) * g
                p = poly([pt(a0, r0, s0), pt(a0, r1, s0), pt(a0, r1, s1), pt(a0, r0, s1)])
            elif i < ns + nw:
                j = i - ns
                p = sec(r0, r1, a0 + j * da, a0 + (j + 1) * da)
            else:                   # 나가는 곧은 디딤판(접점 앞쪽)
                k = i - ns - nw
                s0, s1 = k * g, (k + 1) * g
                p = poly([pt(a1, r0, s0), pt(a1, r1, s0), pt(a1, r1, s1), pt(a1, r0, s1)])
            (guards if which else treads).append((p, round(max(0.0, z - C.cstair_t), 4), round(z + up, 4)))
    rm = (f.Rl(d['r_tread'][0]) + f.Rl(d['r_tread'][1])) / 2
    return treads, guards, dict(treads=n, risers=n + 1, riser_m=round(h, 4), tread_deg=round(da, 2), going_mid_m=round(math.radians(abs(da)) * rm * C.s, 3))


def pool_cells(f, d, fl, G):
    """수조. parts: [dict(outline=점들, depth=m, steps=dict(edge=점들, n=단 수, width_px=단 폭, drop=단 높이))] 앞선 것이 우선.
    rim_px 가 있으면 외곽선이 수조 벽 바깥 면이고 물은 그만큼 안쪽, 없으면 외곽선이 물 가장자리이고 벽은 바깥으로 pool_shell 만큼."""
    z = fl.z
    outer = UNI([poly_f(f, p['outline']) for p in d['parts']])
    rim = d.get('rim_px')
    if rim:
        basin = snap(outer.buffer(-f.M(rim), join_style='mitre'))
    else:
        basin = DIFF(outer, UNI([G['U'], G['B']]))
        outer = DIFF(snap(basin.buffer(C.pool_shell, join_style='mitre')), G['thruU'])
    bottom = z - max(p['depth'] for p in d['parts']) - C.pool_shell
    cells, done = [], sg.Polygon()
    for p in d['parts']:
        g = snap(poly_f(f, p['outline']).buffer(-f.M(rim), join_style='mitre')) if rim else poly_f(f, p['outline'])
        g = DIFF(g, done)
        st = p.get('steps')
        if st:      # 가장자리 선에서 안쪽으로 내려가는 단
            edge = sg.LineString([f.P(q) for q in st['edge']])
            sdone = sg.Polygon()
            for k in range(st['n']):
                bandk = DIFF(INT(g, snap(edge.buffer((k + 1) * f.M(st['width_px'])))), sdone)
                cells.append((bandk, -(k + 1) * st['drop']))
                sdone = UNI([sdone, bandk])
            cells.append((DIFF(g, sdone), -p['depth']))
        else:
            cells.append((g, -p['depth']))
        done = UNI([done, g])
    shell = [(DIFF(outer, basin), bottom, z - C.slab_t)] + [(INT(c, basin), bottom, z + zt) for c, zt in cells]
    return dict(basin=basin, shell=shell, water=[(basin, z - C.water_below[0], z - C.water_below[1])], bottom=bottom)


def holes_of(fl):
    """이 층 바닥 슬래브에 뚫는 구멍(HOLES): add 에서 sub 를 뺀 모양."""
    return [DIFF(UNI([shape(f, s) for s in h.get('add', [])]), UNI([shape(f, s) for s in h.get('sub', [])])) for f, (_, h) in rows(fl, 'HOLES')]


def compute():
    M, sh = {}, {}
    below, span = None, []
    for fl in C.floors:
        G = compute_floor(fl, below, [c for c in span if C.fl[c['to']].index >= fl.index])
        span += [c for c in G['cols'].values() if c['to']]
        M[fl.key] = below = G
    # --- U자 계단. repeat_up 이면 같은 자리에 위층마다 되풀이한다. 위층이 있으면 그 슬래브에 구멍을 낸다
    holes = {k: [] for k in C.keys}
    roof_stair_holes = []
    LOG['scissor_stairs'] = {}
    stairs = {k: {} for k in C.keys}
    overlap = {}
    for fl in C.floors:
        for f, (name, d) in rows(fl, 'STAIRS'):
            dr = stair_to_ref(f, d)
            for g in C.floors[fl.index:(None if d.get('repeat_up') else fl.index + 1)]:
                full = g.index + 1 < len(C.floors) or C.stackable_roof
                cells, hole = ustair_cells(dr, g, full)
                if C.stackable_roof and g.index == 0:
                    holes[g.key].append(hole)
                if dr['kind'] == 'scissor':
                    LOG['scissor_stairs'][f'{g.key}_{name}'] = dict(risers_per_lane=dr['risers'], riser_m=round(storey_h(g) / dr['risers'], 6), treads_per_lane=dr['risers'] - 1)
                overlap[f'{g.key}_{name}'] = round(INT(UNI([p for p, _, _ in cells]), M[g.key]['U']).area, 4)
                stairs[g.key][name] = [(DIFF(p, M[g.key]['U']), a, b) for p, a, b in cells]
                if g.index + 1 < len(C.floors):
                    holes[C.floors[g.index + 1].key].append(hole)
                elif C.stackable_roof:
                    roof_stair_holes.append(hole)
    sh['stairs'] = stairs
    LOG['stair_wall_overlap_m2'] = overlap
    # --- 원형·나선 계단(이 층 바닥 → 위층 바닥). 위층 슬래브 구멍은 위층 추적의 HOLES 에 적는다
    sh['cstairs'] = {k: {} for k in C.keys}
    LOG['circ_stairs'] = {}
    for fl in C.floors:
        for f, (name, d) in rows(fl, 'CIRC_STAIRS'):
            tr, gd, info = circ_stair_cells(f, d, fl)
            sh['cstairs'][fl.key][name] = dict(treads=[(p, fl.z + a, fl.z + b) for p, a, b in tr], guards=[(p, fl.z + a, fl.z + b) for p, a, b in gd])
            LOG['circ_stairs'][f'{fl.key}_{name}'] = info
    # --- 아래로 열린 곳(바닥 종류 void): 이 층 칸 ∩ VOID_OVER 의 아래층 공간들(없으면 아래층 유리 안쪽 전체)
    sh['voids'] = {}
    for fl in C.floors[1:]:
        G, Gb = M[fl.key], M[C.floors[fl.index - 1].key]
        over, below_sp, voids = getattr(fl.T, 'VOID_OVER', {}), {s['name']: s['poly'] for s in Gb['spaces']}, []
        for s in G['spaces']:
            if s['floor'] == 'void':
                s['void'] = INT(s['poly'], UNI([below_sp[n] for n in over[s['name']] if n in below_sp]) if s['name'] in over else Gb['inner'])
                voids.append(s['void'])
        sh['voids'][fl.key] = voids
    # --- 수조
    sh['pools'] = {k: {} for k in C.keys}
    for fl in C.floors:
        for f, (name, d) in rows(fl, 'POOLS'):
            sh['pools'][fl.key][name] = pool_cells(f, d, fl, M[fl.key])
    # --- 슬래브: 층마다 바닥, 맨 위에 천장
    sh['slabs'], sh['holes'] = {}, {}
    for fl in C.floors:
        G = M[fl.key]
        sh['holes'][fl.key] = UNI(holes[fl.key] + holes_of(fl))
        cut = [sh['holes'][fl.key]] + sh['voids'].get(fl.key, []) + [p['basin'] for p in sh['pools'][fl.key].values()]
        base = G['own']
        if fl.index > 0:
            Gb = M[C.floors[fl.index - 1].key]
            base = UNI([Gb['shell'], G['own']])
            cut += [Gb['thruU']] + [c['poly'] for c in span if c['from_index'] < fl.index <= C.fl[c['to']].index]
        sh['slabs'][fl.key] = DIFF(base, UNI(cut))
    sh['slabs']['Roof'] = M[C.keys[-1]]['own' if C.roof == 'own' else 'shell']
    if C.stackable_roof:
        top = C.floors[-1]
        cut = UNI([sh['holes'][top.key], M[top.key]['thruU']] + roof_stair_holes + sh['voids'].get(top.key, []))
        sh['slabs']['Roof'] = DIFF(sh['slabs']['Roof'], cut)
    M['shared'] = sh
    return M


# ---------------------------------------------------------------- 2. IFC 쓰기
class Ifc:
    def __init__(self):
        api = ifcopenshell.api
        f = api.run('project.create_file', version='IFC4')
        self.f = f
        self.project = api.run('root.create_entity', f, ifc_class='IfcProject', name=C.title)
        api.run('unit.assign_unit', f, length={'is_metric': True, 'raw': 'METERS'})
        model = api.run('context.add_context', f, context_type='Model')
        self.body = api.run('context.add_context', f, context_type='Model', context_identifier='Body', target_view='MODEL_VIEW', parent=model)
        site = api.run('root.create_entity', f, ifc_class='IfcSite', name='Site')
        bld = api.run('root.create_entity', f, ifc_class='IfcBuilding', name=C.building)
        api.run('aggregate.assign_object', f, products=[site], relating_object=self.project)
        api.run('aggregate.assign_object', f, products=[bld], relating_object=site)
        self.storeys = {}
        for fl in C.floors:
            st = api.run('root.create_entity', f, ifc_class='IfcBuildingStorey', name=fl.key)
            st.Elevation = float(fl.z)
            api.run('aggregate.assign_object', f, products=[st], relating_object=bld)
            self.storeys[fl.key] = st
        self.zdir = f.createIfcDirection((0.0, 0.0, 1.0))
        self.styles = {}
        self.pending = {k: [] for k in C.keys}

    def _curve(self, ring):
        f = self.f
        pts = [f.createIfcCartesianPoint((float(x), float(y))) for x, y in list(ring.coords)[:-1]]
        return f.createIfcPolyline(pts + [pts[0]])

    def solid(self, poly, z0, z1):
        f = self.f
        poly = sg.polygon.orient(poly, 1.0)
        if poly.interiors:
            prof = f.createIfcArbitraryProfileDefWithVoids('AREA', None, self._curve(poly.exterior), [self._curve(r) for r in poly.interiors])
        else:
            prof = f.createIfcArbitraryClosedProfileDef('AREA', None, self._curve(poly.exterior))
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None)
        return f.createIfcExtrudedAreaSolid(prof, pos, self.zdir, float(z1 - z0))

    def style(self, name, rgb, transparency=0.0):
        if name not in self.styles:
            api = ifcopenshell.api
            st = api.run('style.add_style', self.f, name=name)
            api.run('style.add_surface_style', self.f, style=st, ifc_class='IfcSurfaceStyleShading',
                    attributes={'SurfaceColour': {'Name': None, 'Red': rgb[0], 'Green': rgb[1], 'Blue': rgb[2]}, 'Transparency': transparency})
            self.styles[name] = st
        return self.styles[name]

    def add(self, key, ifc_class, name, solids, object_type=None, predefined=None, long_name=None, contain=True, style=None, psets=None):
        """solids: [(Polygon, z0, z1)] 절대 높이. 이름 앞에 층 키를 붙인다. 빈 조각은 건너뛴다."""
        api = ifcopenshell.api
        f = self.f
        items = []
        for g, z0, z1 in solids:
            if z1 - z0 < 1e-4:
                continue
            for p in polys(g):
                items.append(self.solid(p, z0, z1))
        if not items:
            return None
        e = api.run('root.create_entity', f, ifc_class=ifc_class, name=f'{key}_{name}')
        if object_type and hasattr(e, 'ObjectType'):
            e.ObjectType = object_type
        if predefined:
            try:
                e.PredefinedType = predefined
            except Exception:
                pass
        if long_name and hasattr(e, 'LongName'):
            e.LongName = long_name
        rep = f.createIfcShapeRepresentation(self.body, 'Body', 'SweptSolid', items)
        api.run('geometry.assign_representation', f, product=e, representation=rep)
        api.run('geometry.edit_object_placement', f, product=e, matrix=np.eye(4))
        if style:
            api.run('style.assign_representation_styles', f, shape_representation=rep, styles=[self.style(*style)])
        if contain is True:
            self.pending[key].append(e)
        if psets:
            for pname, props in psets.items():
                ps = api.run('pset.add_pset', f, product=e, name=pname)
                api.run('pset.edit_pset', f, pset=ps, properties=props)
        return e

    def flush(self):
        for key, lst in self.pending.items():
            if lst:
                ifcopenshell.api.run('spatial.assign_container', self.f, products=lst, relating_structure=self.storeys[key])
        self.pending = {k: [] for k in C.keys}

    def void(self, opening, wall):
        try:
            ifcopenshell.api.run('feature.add_feature', self.f, feature=opening, element=wall)
        except Exception:
            ifcopenshell.api.run('void.add_opening', self.f, opening=opening, element=wall)

    def fill(self, opening, door):
        try:
            ifcopenshell.api.run('feature.add_filling', self.f, opening=opening, element=door)
        except Exception:
            ifcopenshell.api.run('void.add_filling', self.f, opening=opening, element=door)


def door_leaves(d):
    """닫힌 문짝 평면과 힌지 점. 반환 [(Polygon, hinge_xy, free_xy)]"""
    if d['kind'] == 'open':
        return []
    x0, y0, x1, y1 = d['px']
    (ax, ay), (bx, by) = W((x0, y1)), W((x1, y0))      # 월드 최소·최대
    t = {'glass': 0.012, 'slide_glass': 0.012, 'slide_double_glass': 0.012, 'elev': 0.03}.get(d['kind'], C.leaf_t)
    gap = C.leaf_gap
    out = []
    if d['axis'] == 'y':      # 벽이 세로(월드 Y 방향)로 길다 → 문짝은 Y 로 길고 X 로 얇다
        cx = (ax + bx) / 2
        lo, hi = ay + gap, by - gap
        if d['kind'] in ('double', 'elev', 'slide_double', 'slide_double_glass'):
            mid = (lo + hi) / 2
            out.append((sg.box(cx - t / 2, lo, cx + t / 2, mid - 0.002), (cx, lo), (cx, mid)))
            out.append((sg.box(cx - t / 2, mid + 0.002, cx + t / 2, hi), (cx, hi), (cx, mid)))
        else:
            # 'a' 는 px 로 작은 쪽 = 이미지 위 = 월드 Y 큰 쪽
            h = (cx, hi) if d['hinge'] == 'a' else (cx, lo)
            e = (cx, lo) if d['hinge'] == 'a' else (cx, hi)
            out.append((sg.box(cx - t / 2, lo, cx + t / 2, hi), h, e))
    else:
        cy = (ay + by) / 2
        lo, hi = ax + gap, bx - gap
        if d['kind'] in ('double', 'elev', 'slide_double', 'slide_double_glass'):
            mid = (lo + hi) / 2
            out.append((sg.box(lo, cy - t / 2, mid - 0.002, cy + t / 2), (lo, cy), (mid, cy)))
            out.append((sg.box(mid + 0.002, cy - t / 2, hi, cy + t / 2), (hi, cy), (mid, cy)))
        else:
            h = (lo, cy) if d['hinge'] == 'a' else (hi, cy)
            e = (hi, cy) if d['hinge'] == 'a' else (lo, cy)
            out.append((sg.box(lo, cy - t / 2, hi, cy + t / 2), h, e))
    return out


def slide_offsets(d, leaves):
    """닫힌 문짝마다 열림 이동 벡터(m). 한 짝은 side 방향, 두 짝은 가운데에서 바깥으로."""
    if d['kind'] not in ('slide', 'slide_glass', 'slide_double', 'slide_double_glass'):
        return []
    if d['kind'] in ('slide_double', 'slide_double_glass'):
        return [(hg[0] - e[0], hg[1] - e[1], 0.0) for _, hg, e in leaves]
    side = {'N': (0, 1), 'S': (0, -1), 'E': (1, 0), 'W': (-1, 0)}[d['side']]
    if (d['axis'] == 'x') != (side[0] != 0):
        raise ValueError(f"{d['name']}: 미닫이 side는 벽 축과 나란해야 한다")
    return [(side[0] * d['width'], side[1] * d['width'], 0.0)]


def col_z(fl, c):
    """기둥 높이 구간: 위층까지 관통(to) / 슬래브를 뚫는 종류 / 그 밖은 이 층 벽 높이."""
    if c['to']:
        return (fl.z, C.fl[c['to']].z + C.fl[c['to']].h)
    if c['kind'] in C.through_slab_columns:
        return (fl.z - C.slab_t if fl.index == 0 else fl.z, fl.z + fl.h + C.slab_t)
    return (fl.z, fl.z + fl.h)


def _floor_key(name):
    """'<층>_<이름>' → (층, 이름). 층 이름으로 시작하지 않으면 (None, 이름)."""
    for k in sorted(C.keys, key=len, reverse=True):
        if name == k:
            return k, ''
        if name and name.startswith(k + '_'):
            return k, name[len(k) + 1:]
    return None, name


def write_ifc(M):
    I = Ifc()
    sh = M['shared']
    api = ifcopenshell.api
    for fl in C.floors:
        key, z, h = fl.key, fl.z, fl.h
        G = M[key]
        walls = {}
        for w in G['walls'].values():
            ot, st, _ = C.wall_kinds[w['kind']]
            walls[w['name']] = I.add(key, 'IfcWall', w['name'], [(w['poly'], z, z + h)], object_type=ot, style=ST[st])
        for c in G['cols'].values():
            I.add(key, 'IfcColumn', c['name'], [(c['poly'], *col_z(fl, c))], object_type=c['kind'], style=ST['column'])
        for d in G['doors'].values():
            op = I.add(key, 'IfcOpeningElement', 'Opening_' + d['name'], [(d['poly'], z, z + d['height'])], contain=False) if d['host'] is not None else None
            if op is not None and walls.get(d['host']) is not None:
                I.void(op, walls[d['host']])
            leaves = door_leaves(d)
            if not leaves:
                continue
            mat = {'glass': ST['glass'], 'slide_glass': ST['glass'], 'slide_double_glass': ST['glass'], 'elev': ST['metal']}.get(d['kind'], ST['door'])
            door = I.add(key, 'IfcDoor', d['name'], [(lf, z + C.leaf_floor_gap, z + d['height'] - C.leaf_gap) for lf, _, _ in leaves], object_type=d['kind'].upper(), style=mat,
                         psets={'Caldera_Door': {'Kind': d['kind'], 'Hinge': d['hinge'] or '', 'SwingSide': d['side'], 'Axis': d['axis'],
                                                 'WidthM': round(d['width'], 4), 'HeightM': d['height'], 'HostWall': d['host'] or '',
                                                 'Hinges': json.dumps([[round(v, 4) for v in hg + e] for _, hg, e in leaves]),
                                                 'SlideOffsets': json.dumps(slide_offsets(d, leaves))}})
            door.OverallWidth, door.OverallHeight = float(d['width']), float(d['height'])
            if op is not None:
                I.fill(op, door)
        for n, d in G['arc_doors'].items():
            I.add(key, 'IfcDoor', n, [(d['poly'], z + C.leaf_floor_gap, z + C.door_h)], object_type='ELEV_CURVED', style=ST['metal'])
        for g in G['glazing'].values():
            cw = api.run('root.create_entity', I.f, ifc_class='IfcCurtainWall', name=f"{key}_{g['name']}")
            api.run('geometry.edit_object_placement', I.f, product=cw, matrix=np.eye(4))
            I.pending[key].append(cw)
            parts = []
            for i, p in enumerate(polys(g['rails'])):
                parts.append(I.add(key, 'IfcMember', f"{g['name']}_Rail{i}", [(p, z, z + C.rail_h), (p, z + h - C.rail_h, z + h)], object_type='RAIL', contain=False, style=ST['frame']))
            for i, p in enumerate(polys(g['mullions'])):
                parts.append(I.add(key, 'IfcMember', f"{g['name']}_Mullion{i}", [(p, z, z + h)], object_type='MULLION', predefined='MULLION', contain=False, style=ST['frame']))
            for i, gl in enumerate(g['glass']):
                parts.append(I.add(key, 'IfcPlate', f"{g['name']}_Glass{i}", [(gl['poly'], z + C.rail_h, z + h - C.rail_h)], object_type='TERRACE_SLIDING' if gl['door'] else 'GLAZING',
                                   contain=False, style=ST['glass']))
            parts = [p for p in parts if p]
            if parts:
                api.run('aggregate.assign_object', I.f, products=parts, relating_object=cw)
        sp = []
        for s in G['spaces']:
            void = s['floor'] == 'void'
            e = I.add(key, 'IfcSpace', s['name'], [(s['poly'], z, z + h)], long_name=s['ko'], contain=False, object_type='VOID' if void else None,
                      psets={'Caldera_Space': {'FloorKind': s['floor'], 'AreaM2': round(s['poly'].area, 3)}})
            if e:
                sp.append(e)
        if sp:
            api.run('aggregate.assign_object', I.f, products=sp, relating_object=I.storeys[key])
        for n, c in G['casework'].items():
            I.add(key, 'IfcFurniture', n, [(c['poly'], z, z + c['h'])], object_type='CASEWORK', style=ST['case'])
        for n, p in G['glass_parts'].items():
            I.add(key, 'IfcPlate', n, [(p, z, z + C.shower_glass_h)], object_type='SHOWER_GLASS', style=ST['glass'])
        for n, panels in G['closet_fronts'].items():
            I.add(key, 'IfcDoor', n, [(p, z + 0.01, z + C.closet_front_h) for p in panels], object_type='CLOSET_SLIDING', style=ST['door'])
        for n, p in G['curbs'].items():
            I.add(key, 'IfcBuildingElementProxy', 'TerraceCurb_' + n, [(p, z, z + C.curb_h)], object_type='TERRACE_CURB', style=ST['slab'])
        for n, p in G['curb_glass'].items():
            I.add(key, 'IfcRailing', 'TerraceBalustrade_' + n, [(p, z + C.curb_h, z + C.curb_h + C.balustrade_h)], object_type='GLASS_BALUSTRADE', style=ST['glass'])
        I.add(key, 'IfcRailing', 'TerraceBalustrade', [(G['balustrade'], z, z + C.balustrade_h)], object_type='GLASS_BALUSTRADE', style=ST['glass'])
        for n, r in G['rails'].items():
            I.add(key, 'IfcRailing', 'Rail_' + n, [(r['poly'], z, z + C.balustrade_h)], object_type='GLASS_RAIL', style=ST['glass'])
        for n, cells in sh['stairs'][key].items():
            I.add(key, 'IfcStair', n, [(p, z + a, z + b) for p, a, b in cells], object_type='U_STAIR', style=ST['stair'])
        for n, c in sh['cstairs'][key].items():
            I.add(key, 'IfcStair', n, c['treads'], object_type='CIRCULAR_STAIR', style=ST['stair'])
            I.add(key, 'IfcRailing', n + '_Stringers', c['guards'], object_type='STAIR_STRINGER', style=ST['paint'])
        for n, p in sh['pools'][key].items():
            I.add(key, 'IfcBuildingElementProxy', n, p['shell'], object_type='POOL_SHELL', style=ST['pool'])
            I.add(key, 'IfcBuildingElementProxy', n + 'Water', p['water'], object_type='POOL_WATER', style=ST['water'])
        I.add(key, 'IfcSlab', 'Slab_Floor', [(sh['slabs'][key], z - C.slab_t, z)], predefined='FLOOR', style=ST['slab'])
    top = C.floors[-1]
    I.add(top.key, 'IfcSlab', 'Slab_Roof', [(sh['slabs']['Roof'], top.z + top.h, top.z + top.h + C.slab_t)], predefined='ROOF', object_type='CEILING_SLAB', style=ST['slab'])
    I.flush()
    os.makedirs(C.out, exist_ok=True)
    I.f.write(C.ifc_path)
    counts = {}
    for e in I.f.by_type('IfcProduct'):
        fk = _floor_key(e.Name)[0] or 'other'
        counts.setdefault(e.is_a(), {})
        counts[e.is_a()][fk] = counts[e.is_a()].get(fk, 0) + 1
    LOG['ifc_counts'] = counts
    return counts


# ---------------------------------------------------------------- 3. IFC 다시 읽기
def _ring(curve):
    return [tuple(p.Coordinates[:2]) for p in curve.Points]


def solids_of(e):
    out = []
    if not e.Representation:
        return out
    for rep in e.Representation.Representations:
        for it in rep.Items:
            if not it.is_a('IfcExtrudedAreaSolid'):
                continue
            prof = it.SweptArea
            holes = [_ring(c) for c in prof.InnerCurves] if prof.is_a('IfcArbitraryProfileDefWithVoids') else []
            z0 = round(it.Position.Location.Coordinates[2], 4)
            out.append((snap(sg.Polygon(_ring(prof.OuterCurve), holes)), z0, round(z0 + it.Depth, 4)))
    return out


def pset(e, name):
    for rel in getattr(e, 'IsDefinedBy', []) or []:
        if rel.is_a('IfcRelDefinesByProperties') and rel.RelatingPropertyDefinition.Name == name:
            return {p.Name: p.NominalValue.wrappedValue for p in rel.RelatingPropertyDefinition.HasProperties}
    return {}


CLASSES = {'IfcWall': 'walls', 'IfcColumn': 'cols', 'IfcOpeningElement': 'openings', 'IfcDoor': 'doors', 'IfcSlab': 'slabs', 'IfcMember': 'members',
           'IfcPlate': 'plates', 'IfcSpace': 'spaces', 'IfcFurniture': 'furniture', 'IfcBuildingElementProxy': 'proxies', 'IfcRailing': 'railings', 'IfcStair': 'stairs'}


def read_ifc():
    f = ifcopenshell.open(C.ifc_path)
    D = {k: {v: [] for v in CLASSES.values()} for k in C.keys}
    for cls, slot in CLASSES.items():
        for e in f.by_type(cls):
            key, name = _floor_key(e.Name)
            rec = dict(name=name, kind=getattr(e, 'ObjectType', None), solids=solids_of(e), guid=e.GlobalId)
            if cls == 'IfcDoor':
                rec['props'] = pset(e, 'Caldera_Door')
            if cls == 'IfcSpace':
                rec['props'] = pset(e, 'Caldera_Space')
                rec['ko'] = e.LongName
            D[key][slot].append(rec)
    # 층마다 그 높이에 걸친 기둥(아래층에서 올라온 것 포함)의 평면
    for fl in C.floors:
        D[fl.key]['col_polys'] = [(c['kind'], p) for k in C.keys for c in D[k]['cols'] for p, z0, z1 in c['solids'] if z0 < fl.z + fl.h - 1e-3 and z1 > fl.z + 1e-3]
    return D


# ---------------------------------------------------------------- 4. 칸 → 닫힌 메시
def _sub_intervals(a, b):
    """구간 목록 a 에서 b 를 뺀다."""
    out = []
    for lo, hi in a:
        segs = [(lo, hi)]
        for blo, bhi in b:
            nxt = []
            for s0, s1 in segs:
                if bhi <= s0 + 1e-9 or blo >= s1 - 1e-9:
                    nxt.append((s0, s1))
                else:
                    if blo > s0 + 1e-9:
                        nxt.append((s0, blo))
                    if bhi < s1 - 1e-9:
                        nxt.append((bhi, s1))
            segs = nxt
        out += segs
    return out


def _merge_intervals(iv):
    out = []
    for lo, hi in sorted(iv):
        if out and lo <= out[-1][1] + 1e-4:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _triangles(face):
    try:
        tris = shapely.constrained_delaunay_triangles(face)
        out = []
        for t in tris.geoms:
            c = list(t.exterior.coords)[:3]
            if sg.Polygon(c).area < 1e-12:
                continue
            if not sg.polygon.LinearRing(c).is_ccw:
                c = c[::-1]
            out.append(c)
        return out
    except Exception:
        from mathutils.geometry import tessellate_polygon
        f = sg.polygon.orient(face, 1.0)
        loops = [list(f.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in f.interiors]
        flat = [p for lp in loops for p in lp]
        tris = tessellate_polygon([[Vector((x, y, 0.0)) for x, y in lp] for lp in loops])
        out = []
        for a, b, c in tris:
            t = [flat[a], flat[b], flat[c]]
            if not sg.polygon.LinearRing(t).is_ccw:
                t = t[::-1]
            out.append(t)
        return out


def cells_to_mesh(name, cells, mat_names, coll):
    """cells: [(Polygon, [(z0, z1), ...], 재질 번호)]. 칸끼리 평면에서 겹쳐도 된다(겹친 곳은 높이 구간을 합친다. 재질은 앞선 칸).
    칸 경계를 한데 노드화한 배치의 면마다 높이 구간을 주고, 이웃 면이 덮지 않는 옆면과 위·아래 면만 만든다.
    세로 모서리에는 그 점에 닿는 모든 면의 높이 값을 꼭짓점으로 넣어 T 접합이 생기지 않게 한다. 쓰이지 않은 재질은 메시에 넣지 않는다."""
    cells = [(p, [(round(a, 4), round(b, 4)) for a, b in iv if b - a > 1e-4], m) for g, iv, m in cells for p in polys(g)]
    cells = [c for c in cells if c[1]]
    if not cells:
        return None
    cp = [c[0] for c in cells]
    net = shapely.unary_union([p.boundary for p in cp], grid_size=PREC)
    tree = shapely.STRtree(cp)
    faces = []
    for fc in so.polygonize(net):
        if fc.area < 1e-8:
            continue
        rp = fc.representative_point()
        hit = sorted(int(i) for i in tree.query(rp, predicate='within'))
        if not hit:
            continue
        faces.append((sg.polygon.orient(fc, 1.0), _merge_intervals([iv for h in hit for iv in cells[h][1]]), cells[hit[0]][2]))
    K = lambda p: (round(p[0], 5), round(p[1], 5))
    edge_owner = {}
    vz = {}
    for fi, (fc, iv, m) in enumerate(faces):
        for ring in [fc.exterior] + list(fc.interiors):
            cs = [K(c) for c in ring.coords]
            for a, b in zip(cs[:-1], cs[1:]):
                if a == b:
                    continue
                edge_owner[(a, b)] = fi
            for c in cs:
                s = vz.setdefault(c, set())
                for lo, hi in iv:
                    s.add(round(lo, 5))
                    s.add(round(hi, 5))
    verts, vidx, out_faces, out_mats = [], {}, [], []

    def V(p, z):
        k = (p[0], p[1], round(z, 5))
        if k not in vidx:
            vidx[k] = len(verts)
            verts.append((p[0], p[1], z))
        return vidx[k]

    for fi, (fc, iv, m) in enumerate(faces):
        for ring in [fc.exterior] + list(fc.interiors):
            cs = [K(c) for c in ring.coords]
            for a, b in zip(cs[:-1], cs[1:]):
                if a == b:
                    continue
                nb = edge_owner.get((b, a))
                niv = faces[nb][1] if nb is not None else []
                for z0, z1 in _sub_intervals(iv, niv):
                    loop = [V(a, z0), V(b, z0)]
                    loop += [V(b, z) for z in sorted(vz[b]) if z0 + 1e-6 < z < z1 - 1e-6]
                    loop += [V(b, z1), V(a, z1)]
                    loop += [V(a, z) for z in sorted(vz[a], reverse=True) if z0 + 1e-6 < z < z1 - 1e-6]
                    out_faces.append(loop)
                    out_mats.append(m)
        tris = _triangles(fc)
        for z0, z1 in iv:
            for t in tris:
                k = [K(c) for c in t]
                if len(set(k)) < 3:
                    continue
                out_faces.append([V(k[0], z1), V(k[1], z1), V(k[2], z1)])
                out_mats.append(m)
                out_faces.append([V(k[2], z0), V(k[1], z0), V(k[0], z0)])
                out_mats.append(m)
    used = sorted(set(out_mats))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], out_faces)
    for mi in used:
        me.materials.append(game_material(mat_names[mi]))
    me.polygons.foreach_set('material_index', [used.index(m) for m in out_mats])
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def game_material(short):
    name = f'M_{C.prefix}_{short}'
    m = bpy.data.materials.get(name)
    if m:
        return m
    col, rough, metal, alpha = C.mats[short]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    b.inputs['Base Color'].default_value = (*col, 1.0)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    b.inputs['Alpha'].default_value = alpha
    m.diffuse_color = (*col, alpha)
    if alpha < 1.0:
        for attr, val in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND')):
            try:
                setattr(m, attr, val)
            except Exception:
                pass
    return m


def box_object(name, solids, mat, coll, origin=None):
    ob = cells_to_mesh(name, [(p, [(z0, z1)], 0) for p, z0, z1 in solids], [mat], coll)
    if ob is not None and origin is not None:
        ox, oy = origin
        ob.data.transform(Matrix.Translation((-ox, -oy, 0.0)))
        ob.location = (ox, oy, 0.0)
    return ob


def tag(ob, floor):
    if ob is not None:
        ob['floor'] = floor
    return ob


def build_game(D):
    sc = bpy.data.scenes.new(C.game_scene)
    if bpy.context.window:
        bpy.context.window.scene = sc
    root = bpy.data.collections.new(C.game_scene)
    sc.collection.children.link(root)
    colls = {}
    for key in C.keys:
        c = bpy.data.collections.new(key)
        root.children.link(c)
        colls[key] = c
        for sub in ('Doors', 'Rooms'):
            cc = bpy.data.collections.new(f'{key}_{sub}')
            c.children.link(cc)
            colls[f'{key}_{sub}'] = cc
    SO = lambda recs, kind=None: [(p, z0, z1) for e in recs if kind is None or e['kind'] == kind for p, z0, z1 in e['solids']]
    one = lambda nm, solids, mat, coll, fk: tag(cells_to_mesh(nm, [(p, [(z0, z1)], 0) for p, z0, z1 in solids], [mat], coll), fk)
    # 벽 재질: wall_kinds 에 적힌 순서가 겹칠 때의 우선순위
    wall_mats, by_mat = [], {}
    for ot, _, gm in C.wall_kinds.values():
        if gm not in wall_mats:
            wall_mats.append(gm)
        by_mat.setdefault(gm, []).append(ot)
    floor_mats = ['Slab'] + list(dict.fromkeys(C.floor_kinds.values()))
    for ki, fl in enumerate(C.floors):
        key, z, h = fl.key, fl.z, fl.h
        d, coll = D[key], colls[key]
        one(f'SM_{key}_Columns', SO(d['cols']), 'ColumnConcrete', coll, key)
        # --- 벽: 재질 무리마다 앞선 무리와 기둥에 겹친 부분을 뺀 영역. 문 개구부 자리는 인방(문 높이 위)만 남긴다
        regs, prev = [], UNI([p for _, p in d['col_polys']])
        for mi, gm in enumerate(wall_mats):
            r = DIFF(UNI([p for w in d['walls'] if w['kind'] in by_mat[gm] for p, _, _ in w['solids']]), prev)
            regs.append((r, mi))
            prev = UNI([prev, r])
        d['U'] = prev
        open_by_h = {}
        for o in d['openings']:
            for p, z0, z1 in o['solids']:
                open_by_h.setdefault(z1, []).append(p)
        o_all = UNI([p for ps in open_by_h.values() for p in ps])
        cells = []
        for region, mi in regs:
            cells.append((DIFF(region, o_all), [(z, z + h)], mi))
            for oh, ps in open_by_h.items():
                cells.append((INT(region, UNI(ps)), [(oh, z + h)], mi))
        tag(cells_to_mesh(f'SM_{key}_Walls', cells, wall_mats, coll), key)
        # --- 커튼월: 가로대(아래·위) + 멀리언 한 메시, 유리 한 메시, 테라스 미닫이 베이는 따로
        cells = [(m['solids'][0][0], [(z0, z1) for _, z0, z1 in m['solids']], 0) for m in d['members'] if m['solids']]
        tag(cells_to_mesh(f'SM_{key}_CurtainFrame', cells, ['Frame'], coll), key)
        one(f'SM_{key}_CurtainGlass', SO(d['plates'], 'GLAZING'), 'Glass', coll, key)
        for e in d['plates']:
            if e['kind'] == 'TERRACE_SLIDING':
                ob = tag(box_object(f"SM_{key}_TerraceDoor_{e['name']}", e['solids'], 'Glass', colls[key + '_Doors']), key)
                if ob is not None:
                    ob['door_kind'] = 'terrace_sliding'
        one(f'SM_{key}_ShowerGlass', SO(d['plates'], 'SHOWER_GLASS'), 'Glass', coll, key)
        one(f'SM_{key}_Casework', SO(d['furniture']), 'Casework', coll, key)
        one(f'SM_{key}_TerraceCurb', SO(d['proxies'], 'TERRACE_CURB'), 'Slab', coll, key)
        one(f'SM_{key}_TerraceBalustrade', SO(d['railings'], 'GLASS_BALUSTRADE'), 'Glass', coll, key)
        one(f'SM_{key}_Rails', SO(d['railings'], 'GLASS_RAIL'), 'Glass', coll, key)
        one(f'SM_{key}_Stairs', SO(d['stairs'], 'U_STAIR'), 'Stair', coll, key)
        cells = [(p, [(z0, z1)], 0) for p, z0, z1 in SO(d['stairs'], 'CIRCULAR_STAIR')] + [(p, [(z0, z1)], 1) for p, z0, z1 in SO(d['railings'], 'STAIR_STRINGER')]
        tag(cells_to_mesh(f'SM_{key}_CircularStairs', cells, ['Stair', 'WallPaint'], coll), key)
        one(f'SM_{key}_Pool', SO(d['proxies'], 'POOL_SHELL'), 'PoolTile', coll, key)
        one(f'SM_{key}_PoolWater', SO(d['proxies'], 'POOL_WATER'), 'PoolWater', coll, key)
        # --- 바닥: 공간마다 재질, 나머지(벽 아래)는 슬래브 재질. 위층 슬래브는 윗면 마감 층과 아래층 천장 층으로 나눈다
        fs = next(s for s in d['slabs'] if s['name'] == 'Slab_Floor')
        floor = UNI([p for p, _, _ in fs['solids']])
        top = (z - C.slab_t, z) if ki == 0 else (z - C.finish_t, z)
        cells, used = [], []
        for s in d['spaces']:
            if s['kind'] == 'VOID':
                continue
            p = INT(s['solids'][0][0], floor)
            cells.append((p, [top], floor_mats.index(C.floor_kinds.get(s['props'].get('FloorKind'), 'FloorStone'))))
            used.append(p)
        cells.append((DIFF(floor, UNI(used)), [top], 0))
        tag(cells_to_mesh(f'SM_{key}_Floor', cells, floor_mats, coll), key)
        if ki > 0:
            tag(cells_to_mesh(f'SM_{C.keys[ki - 1]}_Ceiling', [(floor, [(z - C.slab_t, z - C.finish_t)], 0)], ['Ceiling'], coll), key)
        for s in d['slabs']:
            if s['name'] == 'Slab_Roof':
                one(f'SM_{key}_Ceiling', s['solids'], 'Ceiling', coll, 'roof')
        # --- 문: DOOR_<층>_<이름> 표식 아래 문짝(원점 = 힌지). 닫힌 상태로 둔다
        for dr in d['doors']:
            dc = colls[key + '_Doors']
            c = UNI([p for p, _, _ in dr['solids']]).centroid
            mk = bpy.data.objects.new(f"DOOR_{key}_{dr['name']}", None)
            mk.location = (c.x, c.y, z)
            mk.empty_display_size = 0.4
            mk['ifc_guid'] = dr['guid']
            mk['floor'] = key
            dc.objects.link(mk)
            if dr['kind'] in ('CLOSET_SLIDING', 'ELEV_CURVED'):
                mk['door_kind'] = dr['kind'].lower()
                mat = 'Door' if dr['kind'] == 'CLOSET_SLIDING' else 'ElevatorMetal'
                for i, sol in enumerate(dr['solids']):
                    ob = tag(box_object(f"SM_{key}_DoorPanel_{dr['name']}_{i}", [sol], mat, dc, origin=(c.x, c.y)), key)
                    ob.parent = mk
                    ob.location = (0, 0, -z)
                continue
            pr = dr['props']
            for k in ('Kind', 'Hinge', 'SwingSide', 'WidthM', 'HeightM', 'HostWall'):
                mk['door_' + k.lower()] = pr[k]
            mat = {'GLASS': 'Glass', 'SLIDE_GLASS': 'Glass', 'SLIDE_DOUBLE_GLASS': 'Glass', 'ELEV': 'ElevatorMetal'}.get(dr['kind'], 'Door')
            offsets = json.loads(pr.get('SlideOffsets', '[]'))
            if offsets:
                mk['open_translation_m'] = json.dumps(offsets)
            side = {'N': (0, 1), 'S': (0, -1), 'E': (1, 0), 'W': (-1, 0)}[pr['SwingSide']]
            for i, (sol, (hx, hy, ex, ey)) in enumerate(zip(dr['solids'], json.loads(pr['Hinges']))):
                ob = tag(box_object(f"SM_{key}_DoorLeaf_{dr['name']}_{i}", [sol], mat, dc, origin=(hx, hy)), key)
                ob.parent = mk
                ob.location = (hx - c.x, hy - c.y, -z)
                if offsets:
                    ob['open_translation_m'] = offsets[i]
                    mk['open_distance_m'] = math.sqrt(sum(v * v for v in offsets[i]))
                else:
                    ux, uy = ex - hx, ey - hy
                    cross = ux * side[1] - uy * side[0]
                    ob['open_rot_z'] = 0.0 if dr['kind'] == 'ELEV' else math.copysign(math.pi / 2, cross)
        # --- 방 표식
        for s in d['spaces']:
            p = s['solids'][0][0]
            rp = p.representative_point()
            e = bpy.data.objects.new(f"ROOM_{key}_{s['name']}", None)
            e.location = (rp.x, rp.y, z)
            e.empty_display_type = 'SPHERE'
            e.empty_display_size = 0.25
            e['room_name_ko'] = s['ko']
            e['area_m2'] = round(p.area, 2)
            e['floor_kind'] = s['props'].get('FloorKind', '')
            e['ifc_guid'] = s['guid']
            e['floor'] = key
            colls[key + '_Rooms'].objects.link(e)
    return root


# ---------------------------------------------------------------- 5. 자체 검사
def validate(M, D):
    res = {}
    problems = []
    bpy.data.scenes[C.game_scene].view_layers[0].update()   # 백그라운드에서는 부모·위치를 준 뒤 matrix_world 가 저절로 갱신되지 않는다
    for fl in C.floors:
        key = fl.key
        G, U = M[key], D[key]['U']
        PX = lambda g: [round(v) for v in fl.px(*g.representative_point().coords[0])]
        r = {}
        # (a) 벽 접합부 틈: 벽·기둥 합집합을 d 만큼 닫았을 때 메워지는 면적, 그리고 벽 사이에 남은 6cm 보다 좁은 빈 틈
        for dd in (0.003, 0.01, 0.03):
            closed = U.buffer(dd, join_style='mitre').buffer(-dd, join_style='mitre')
            gaps = [g for g in polys(closed.difference(U), 1e-8)]
            r[f'wall_gap_closing_{int(dd * 1000)}mm'] = dict(count=len(gaps), area_m2=round(sum(g.area for g in gaps), 6), where_px=[PX(g) for g in gaps[:20]])
        free = G['free']
        opened = free.buffer(-0.03, join_style='mitre').buffer(0.03, join_style='mitre')
        sl = [g for g in polys(free.difference(opened), 1e-5)]
        r['free_space_slivers_under_6cm'] = dict(count=len(sl), area_m2=round(sum(g.area for g in sl), 5),
                                                 where_px=[PX(g) + [round(g.area, 5)] for g in sorted(sl, key=lambda g: -g.area)[:30]])
        # (b) 문이 벽 안에 온전히 들어가는지, 통행 폭
        outside = [n for n, d in G['doors'].items() if d['host'] is not None and d['poly'].difference(G['wallU']).area > 1e-6]
        r['doors'] = dict(count=len(G['doors']), min_width_m=round(min([d['width'] for d in G['doors'].values()], default=0.0), 3), outside_wall=outside)
        r['spaces'] = {s['name']: round(s['poly'].area, 2) for s in G['spaces']}
        r['space_errors'] = LOG.get(key + '_space_errors', [])
        r['unnamed_faces'] = LOG.get(key + '_unnamed_faces', [])
        r['counts'] = dict(walls=len(G['walls']), doors=len(G['doors']) + len(G['arc_doors']), spaces=len(G['spaces']), columns=len(G['cols']),
                           closet_fronts=len(G['closet_fronts']), casework=len(G['casework']), rails=len(G['rails']), mullions=LOG.get(key + '_mullions'))
        r['areas_m2'] = dict(interior=round(G['inner'].area, 1), walls=round(G['wallU'].area, 1), terrace=round(G['terrace'].area, 1), plate=round(G['own'].area, 1))
        r['plan_match'] = plan_match(fl, D[key])
        res[key] = r
        problems += [f'{key} 문 {n}: 개구부가 벽 밖으로 나간다' for n in outside]
        problems += [f'{key} 공간 {n}: 시드가 칸 {h} 에 걸린다(칸이 없거나 다른 시드와 같은 칸)' for n, h in r['space_errors']]
        problems += [f"{key} 이름 없는 칸 {u['area']}㎡ @ px {u['px']}" for u in r['unnamed_faces'] if u['area'] > 0.05]
    # (c) 층간 일치(바로 아래층과 비교)
    v = {}
    for lo, up in zip(C.floors[:-1], C.floors[1:]):
        a, b, pair = M[lo.key], M[up.key], f'{lo.key}_{up.key}'
        PX = lambda g: [round(q) for q in lo.px(*g.representative_point().coords[0])]
        core = [UNI([w['poly'] for w in G['walls'].values() if w['kind'] == 'C']) for G in (a, b)]
        sd = core[0].symmetric_difference(core[1])
        thin = [g for g in polys(sd, 1e-6) if g.area / max(g.length, 1e-9) * 2 < 0.04]
        uncovered = INT(core[0], b['inner']).difference(b['wallU'])
        v[pair] = dict(
            core_walls=dict(area_below=round(core[0].area, 2), area_above=round(core[1].area, 2), common=round(core[0].intersection(core[1]).area, 2), differ_m2=round(sd.area, 2),
                            differ_pieces_over_0_3m2=[PX(g) + [round(g.area, 2)] for g in sorted(polys(sd, 1e-4), key=lambda g: -g.area) if g.area > 0.3][:20]),
            core_face_offsets_under_40mm=dict(count=len(thin), area_m2=round(sum(g.area for g in thin), 4)),
            core_below_not_under_wall_above_m2=round(uncovered.area, 2),
            column_shift_m={n: [round(b['cols'][n]['center'][0] - c['center'][0], 3), round(b['cols'][n]['center'][1] - c['center'][1], 3)] for n, c in a['cols'].items() if n in b['cols']},
            glazing={n: dict(common_m2=round(g['band'].intersection(b['glazing'][n]['band']).area, 3), only_below_m2=round(g['band'].difference(b['glazing'][n]['band']).area, 3),
                             only_above_m2=round(b['glazing'][n]['band'].difference(g['band']).area, 3)) for n, g in a['glazing'].items() if n in b['glazing']},
            shafts={s['name']: dict(below_m2=round(s['poly'].area, 2), above_m2=round(t['poly'].area, 2), common_m2=round(s['poly'].intersection(t['poly']).area, 2))
                    for s in a['spaces'] if s['floor'] == 'core' for t in b['spaces'] if t['name'] == s['name']},
            shell_only_below_m2=round(a['shell'].difference(b['shell']).area, 1), shell_only_above_m2=round(b['shell'].difference(a['shell']).area, 1),
            edges_snapped_to_below=LOG.get(up.key + '_edges_snapped_to_below'))
    sh = M['shared']
    v['slabs_m2'] = {k: round(g.area, 1) for k, g in sh['slabs'].items()}
    v['slab_holes_m2'] = {k: dict(holes=round(sh['holes'][k].area, 1), voids=round(UNI(sh['voids'].get(k, [])).area, 1),
                                  pools=round(UNI([p['basin'] for p in sh['pools'][k].values()]).area, 1)) for k in C.keys}
    v['stair_wall_overlap_m2'] = LOG.get('stair_wall_overlap_m2')
    res['vertical_alignment'] = v
    res['circ_stairs'] = LOG.get('circ_stairs')
    res['scissor_stairs'] = LOG.get('scissor_stairs')
    # (d) 메시: 비매니폴드 엣지, 면적 0 면
    import bmesh
    meshes = [o for o in bpy.data.scenes[C.game_scene].objects if o.type == 'MESH']
    mres, tris_total = {}, 0
    for o in meshes:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bad = [e for e in bm.edges if len(e.link_faces) != 2]
        nm = len(bad)
        zero = sum(1 for f in bm.faces if f.calc_area() < 1e-9)
        tris = sum(len(f.verts) - 2 for f in bm.faces)
        tris_total += tris
        if not any(s in o.name for s in ('DoorLeaf', 'DoorPanel', 'TerraceDoor')) or nm or zero:
            mres[o.name] = dict(verts=len(bm.verts), faces=len(bm.faces), tris=tris, non_manifold_edges=nm, zero_area_faces=zero)
            if bad:   # 어디인지(월드 m, 그 엣지에 붙은 면 수)
                mw = o.matrix_world
                mres[o.name]['non_manifold_at'] = [[round(c, 3) for c in mw @ ((e.verts[0].co + e.verts[1].co) / 2)] + [len(e.link_faces)] for e in bad[:10]]
        if nm or zero:
            problems.append(f'메시 {o.name}: 비매니폴드 엣지 {nm}, 면적 0 면 {zero}')
        bm.free()
    res['meshes'] = mres
    res['mesh_totals'] = dict(objects=len(meshes), tris=tris_total, non_manifold_edges=sum(x['non_manifold_edges'] for x in mres.values()),
                              zero_area_faces=sum(x['zero_area_faces'] for x in mres.values()))
    # (e) 같은 평면에 겹친 면: 평면(법선·거리)별로 묶어 2D 겹침 면적을 잰다. 같은 방향 = z-fighting, 반대 방향 = 맞닿은 숨은 면
    planes = {}
    for o in meshes:
        mw = o.matrix_world
        me = o.data
        for f in me.polygons:
            n = (mw.to_3x3() @ f.normal).normalized()
            pts = [mw @ me.vertices[i].co for i in f.vertices]
            sign = 1
            key_n = (round(n.x, 3), round(n.y, 3), round(n.z, 3))
            if key_n < (-key_n[0], -key_n[1], -key_n[2]):
                key_n = (-key_n[0], -key_n[1], -key_n[2])
                sign = -1
            nn = Vector(key_n).normalized()
            dist = round(sum(p.dot(nn) for p in pts) / len(pts) / 0.0005) * 0.0005
            if abs(nn.z) > 0.9:
                uu, vv = Vector((1, 0, 0)), Vector((0, 1, 0))
            else:
                uu = Vector((-nn.y, nn.x, 0)).normalized()
                vv = Vector((0, 0, 1))
            p2 = sg.Polygon([(p.dot(uu), p.dot(vv)) for p in pts])
            if not p2.is_valid or p2.area < 1e-8:
                continue
            planes.setdefault((key_n, round(dist, 4)), []).append((o.name, sign, p2))
    same, opp = {}, {}
    for fl_ in planes.values():
        if len(fl_) < 2:
            continue
        geoms = [f[2] for f in fl_]
        tree = shapely.STRtree(geoms)
        a_idx, b_idx = tree.query(geoms, predicate='intersects')
        for i, j in zip(a_idx.tolist(), b_idx.tolist()):
            if i >= j or fl_[i][0] == fl_[j][0] and fl_[i][1] != fl_[j][1]:
                continue
            ar = geoms[i].intersection(geoms[j]).area
            if ar < 1e-6:
                continue
            pair = ' | '.join(sorted((fl_[i][0], fl_[j][0])))
            tgt = same if fl_[i][1] == fl_[j][1] else opp
            e = tgt.setdefault(pair, [0, 0.0])
            e[0] += 1
            e[1] += ar
    res['coplanar_same_facing_overlap'] = {k: dict(pairs=x[0], area_m2=round(x[1], 5)) for k, x in sorted(same.items())}
    res['coplanar_opposite_contact'] = dict(object_pairs=len(opp), area_m2=round(sum(x[1] for x in opp.values()), 1),
                                            largest={k: round(x[1], 1) for k, x in sorted(opp.items(), key=lambda kv: -kv[1][1])[:12]})
    res['ifc_counts'] = LOG.get('ifc_counts', {})
    res['bonsai_source_objects'] = LOG.get('bonsai_source_objects')
    res['problems'] = list(LOG.get('problems', [])) + problems
    with open(C.valid_path, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return res


def _mask(fl, geoms, size):
    from PIL import Image, ImageDraw
    m = Image.new('L', size, 0)
    md = ImageDraw.Draw(m)
    for g in geoms:
        for p in polys(g, 1e-9):
            md.polygon([fl.px(x, y) for x, y in p.exterior.coords], fill=255)
            for r in p.interiors:
                md.polygon([fl.px(x, y) for x, y in r.coords], fill=0)
    return m


def plan_match(fl, d):
    """평면의 벽 채움과 모델 벽·기둥(문 개구부 제외)의 일치도(IoU). 차이 그림을 renders/plan_diff_<층>.png 로 남긴다.
    plan_match 설정: gray=(최소, 최대) 벽 채움 회색값, open_px 그 값의 화소를 이 크기로 열어 선·글자를 지운다,
    wall_kinds 견줄 벽 종류(없으면 전부), column_kinds 견줄 기둥 종류(없으면 전부)."""
    from PIL import Image, ImageFilter
    if not os.path.isfile(fl.image):
        LOG.setdefault('problems', []).append(f'{fl.key} 평면 그림이 없다: {fl.image}')
        return None
    os.makedirs(C.render_dir, exist_ok=True)
    pm = C.plan_match
    g0, g1 = pm['gray']
    src = Image.open(fl.image).convert('L')
    fill = src.point(lambda v: 255 if g0 <= v <= g1 else 0)
    opened = fill.filter(ImageFilter.MinFilter(pm['open_px'])).filter(ImageFilter.MaxFilter(pm['open_px']))
    wk = None if pm['wall_kinds'] is None else {C.wall_kinds[k][0] for k in pm['wall_kinds']}
    solid = [p for w in d['walls'] if wk is None or w['kind'] in wk for p, _, _ in w['solids']]
    solid += [p for kind, p in d['col_polys'] if pm['column_kinds'] is None or kind in pm['column_kinds']]
    model = _mask(fl, [DIFF(UNI(solid), UNI([p for o in d['openings'] for p, _, _ in o['solids']]))], src.size)
    a, b = np.asarray(opened) > 0, np.asarray(model) > 0
    excluded = [p for group in d.values() if isinstance(group, list) for e in group
                if isinstance(e, dict) and (e.get('kind') in pm['exclude'] or e.get('name') in pm['exclude'])
                for p, _, _ in e.get('solids', [])]
    margin = fl.fr.M(pm['exclude_margin_px'])
    ignored = np.asarray(_mask(fl, [UNI(excluded).buffer(margin)], src.size)) > 0 if excluded else np.zeros(a.shape, bool)
    a &= ~ignored
    b &= ~ignored
    inter, union = int((a & b).sum()), int((a | b).sum())
    diff = np.zeros(a.shape + (3,), np.uint8) + 255
    diff[a & b] = (170, 170, 170)       # 회색: 일치
    diff[a & ~b] = (230, 0, 0)          # 빨강: 평면에만 있다
    diff[~a & b] = (0, 80, 230)         # 파랑: 모델에만 있다
    diff[ignored] = (235, 225, 245)     # 옅은 보라: 비교 제외
    Image.fromarray(diff).resize((src.size[0] // 2, src.size[1] // 2)).save(os.path.join(C.render_dir, f'plan_diff_{fl.key}.png'))
    return dict(iou=round(inter / max(union, 1), 4), plan_px=int(a.sum()), model_px=int(b.sum()), plan_only_px=int((a & ~b).sum()), model_only_px=int((~a & b).sum()))


# ---------------------------------------------------------------- 6. 평면 겹침 그림
def plan_overlay(fl, d):
    """원본 평면 위에 IFC 에서 되읽은 요소를 색으로 얹는다(renders/plan_overlay_<층>.png).
    주황 코어·덩어리 벽 / 빨강 칸막이 / 분홍 얇은 벽 / 보라 세대 밖 덩어리 / 갈색 랙 / 파랑 기둥 / 하늘 유리·가로대 / 남색 멀리언 / 자홍 미닫이 베이
    / 노랑 붙박이 / 청록 난간 / 연보라 계단 / 초록 문 개구부와 열림 호 / 자홍 테두리 슬래브 구멍."""
    from PIL import Image, ImageDraw, ImageFont
    if not os.path.isfile(fl.image):
        return None
    os.makedirs(C.render_dir, exist_ok=True)
    base = Image.open(fl.image).convert('RGBA')
    P = lambda x, y: fl.px(x, y)
    fs_px = C.overlay_font_px
    try:
        font = ImageFont.truetype('malgun.ttf', fs_px)
    except Exception:
        font = ImageFont.load_default()

    def layer(geoms, colr):
        m = _mask(fl, geoms, base.size)
        lay = Image.new('RGBA', base.size, (0, 0, 0, 0))
        lay.paste(Image.new('RGBA', base.size, colr), (0, 0), m)
        return lay

    U = lambda recs, kinds=None: UNI([p for e in recs if kinds is None or e['kind'] in kinds for p, _, _ in e['solids']])
    known = ('CORE', 'MASS', 'OUTSIDE_UNIT', 'PARTITION', 'THIN', 'RACK', 'GLASS')
    out = base
    for geoms, colr in (([U(d['walls'], ('CORE', 'MASS'))], (255, 120, 0, 130)), ([U(d['walls'], ('OUTSIDE_UNIT',))], (150, 90, 200, 110)),
                        ([U(d['walls'], ('PARTITION',)), UNI([p for w in d['walls'] if w['kind'] not in known for p, _, _ in w['solids']])], (255, 0, 0, 130)),
                        ([U(d['walls'], ('THIN',))], (255, 0, 120, 200)), ([U(d['walls'], ('RACK',))], (150, 80, 0, 150)), ([U(d['walls'], ('GLASS',))], (0, 200, 255, 170)),
                        ([UNI([p for _, p in d['col_polys']])], (0, 60, 255, 130)),
                        ([U(d['members'], ('RAIL',))], (0, 200, 255, 120)), ([U(d['members'], ('MULLION',))], (0, 0, 160, 230)),
                        ([U(d['plates'], ('GLAZING', 'SHOWER_GLASS'))], (0, 255, 255, 255)), ([U(d['plates'], ('TERRACE_SLIDING',))], (255, 0, 255, 255)),
                        ([U(d['furniture'])], (255, 200, 0, 80)), ([U(d['railings'], ('GLASS_RAIL', 'GLASS_BALUSTRADE'))], (0, 170, 170, 230)),
                        ([U(d['railings'], ('STAIR_STRINGER',))], (90, 0, 160, 150)), ([U(d['proxies'], ('POOL_SHELL',))], (0, 120, 255, 70)),
                        ([U(d['stairs'])], (120, 120, 255, 60)), ([U(d['openings'])], (0, 230, 0, 200))):
        out = Image.alpha_composite(out, layer(geoms, colr))
    ov = Image.new('RGBA', base.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    for e in d['stairs'] + [x for x in d['proxies'] if x['kind'] == 'POOL_SHELL']:
        for p, _, _ in e['solids']:
            for q in polys(p, 1e-9):
                dr.line([P(x, y) for x, y in q.exterior.coords], fill=(60, 60, 200, 200), width=1)
    fs = next(s for s in d['slabs'] if s['name'] == 'Slab_Floor')   # 이 층 바닥 슬래브의 구멍(계단·빈 곳·수조)을 자홍 테두리로
    for p, _, _ in fs['solids']:
        for r in p.interiors:
            dr.line([P(x, y) for x, y in r.coords], fill=(220, 0, 200, 255), width=4)
    for s in d['spaces']:
        rp = s['solids'][0][0].representative_point()
        x, y = P(rp.x, rp.y)
        dr.text((x - 2.5 * fs_px, y + fs_px), s['name'], fill=(0, 90, 200, 255) if s['kind'] != 'VOID' else (200, 0, 180, 255), font=font)
    for dd in d['doors']:
        if dd['kind'] in ('CLOSET_SLIDING', 'ELEV', 'ELEV_CURVED', 'SLIDE', 'SLIDE_GLASS', 'SLIDE_DOUBLE', 'SLIDE_DOUBLE_GLASS'):
            for p, _, _ in dd['solids']:
                for q in polys(p.buffer(0.01), 1e-9):
                    dr.polygon([P(x, y) for x, y in q.exterior.coords], fill=(160, 0, 200, 230))
            for (hx, hy, ex, ey), offset in zip(json.loads(dd['props'].get('Hinges', '[]')), json.loads(dd['props'].get('SlideOffsets', '[]'))):
                dr.line([P((hx + ex) / 2, (hy + ey) / 2), P((hx + ex) / 2 + offset[0], (hy + ey) / 2 + offset[1])], fill=(0, 120, 0, 255), width=4)
            continue
        pr = dd['props']
        side = {'N': (0, 1), 'S': (0, -1), 'E': (1, 0), 'W': (-1, 0)}[pr['SwingSide']]
        for hx, hy, ex, ey in json.loads(pr['Hinges']):
            ln = math.hypot(ex - hx, ey - hy)
            tip = (hx + side[0] * ln, hy + side[1] * ln)
            dr.line([P(hx, hy), P(*tip)], fill=(0, 120, 0, 255), width=4)
            a0 = math.atan2(ey - hy, ex - hx)
            a1 = math.atan2(side[1], side[0])
            da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            dr.line([P(hx + ln * math.cos(a0 + da * k / 12), hy + ln * math.sin(a0 + da * k / 12)) for k in range(13)], fill=(0, 160, 0, 255), width=2)
    out = Image.alpha_composite(out, ov).convert('RGB')
    path = os.path.join(C.render_dir, f'plan_overlay_{fl.key}.png')
    out.save(path)
    return path


# ---------------------------------------------------------------- 7. 렌더
def render(only=None):
    """층별 위 모습(top_<층>: 그 위층들과 천장을 숨긴다), 조감(aerial: 기본은 천장만 숨긴다), 실내 컷(shots). 렌더용 조명·카메라와 열린 문은 저장하지 않는다."""
    sc = bpy.data.scenes[C.game_scene]
    os.makedirs(C.render_dir, exist_ok=True)
    rig = bpy.data.collections.new('RenderRig')
    sc.collection.children.link(rig)
    sc.render.engine = 'BLENDER_EEVEE'
    ee = sc.eevee
    for attr, val in (('taa_render_samples', 48), ('use_raytracing', True), ('use_shadows', True), ('fast_gi_method', 'GLOBAL_ILLUMINATION'),
                      ('shadow_pool_size', '1024')):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except Exception:
                pass
    try:
        sc.view_settings.view_transform = 'AgX'
    except TypeError:
        pass
    world = bpy.data.worlds.new('RenderWorld')
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
    bg.inputs[0].default_value = (0.60, 0.68, 0.80, 1.0)
    bg.inputs[1].default_value = 1.0
    sc.world = world
    sun = bpy.data.objects.new('RenderSun', bpy.data.lights.new('RenderSun', 'SUN'))
    sun.data.energy = 3.0
    sun.data.angle = math.radians(2)
    rig.objects.link(sun)
    lights = []
    for o in sc.objects:
        if o.name.startswith('ROOM_') and o.get('floor_kind') != 'terrace' and not any(s in o.name for s in C.light_skip):
            area = o.get('area_m2', 10.0)
            void = o.get('floor_kind') == 'void'
            L = bpy.data.objects.new('RL_' + o.name, bpy.data.lights.new('RL_' + o.name, 'POINT'))
            L.data.energy = (30.0 if void else C.light_energy) * max(1.0, area ** 0.6)
            L.data.specular_factor = 0.0
            L.data.shadow_soft_size = 0.5
            L.data.color = (1.0, 0.94, 0.86)
            L.location = (o.location.x, o.location.y, o.location.z + C.fl[o['floor']].h - 0.4)
            L['floor'], L['void'] = o['floor'], void
            rig.objects.link(L)
            lights.append(L)
    cam = bpy.data.objects.new('RenderCam', bpy.data.cameras.new('RenderCam'))
    rig.objects.link(cam)
    sc.camera = cam
    for o in sc.objects:
        if o.type == 'EMPTY':
            o.hide_render = True
        if o.type == 'MESH' and 'open_rot_z' in o:       # 렌더에서는 평면처럼 문을 연다(저장하지 않는다)
            o.rotation_euler.z = o['open_rot_z']
        if o.type == 'MESH' and 'open_translation_m' in o:
            o.location += Vector(o['open_translation_m'])
    done = []

    def show(hidden):
        for o in sc.objects:
            if o.type == 'MESH':
                o.hide_render = o.get('floor') in hidden
            elif o.type == 'LIGHT' and o is not sun:
                o.hide_render = True

    def shot(name):
        sc.render.filepath = os.path.join(C.render_dir, name + '.png')
        bpy.ops.render.render(write_still=True, scene=sc.name)
        done.append(sc.render.filepath)

    ref = next((fl for fl in C.floors if not (fl.fr.to_ref or fl.fr.from_ref)), C.floors[0])
    iw, ih = ref.T.IMAGE_SIZE
    x0, y0 = W((0, ih))
    x1, y1 = W((iw, 0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ztop = C.floors[-1].z + C.floors[-1].h
    sc.view_settings.exposure = -0.8
    for i, fl in enumerate(C.floors):       # 위 모습: 그림 전체 범위, 평면 그림의 절반 해상도
        name = 'top_' + fl.key
        if only and name not in only:
            continue
        show(tuple(C.keys[i + 1:]) + ('roof',))
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = max(x1 - x0, y1 - y0)
        cam.location = (cx, cy, ztop + 60.0)
        cam.rotation_euler = (0, 0, 0)
        cam.data.clip_end = 300
        sun.rotation_euler = (math.radians(18), 0, math.radians(-115))   # 위에서 볼 때는 그림자를 짧게
        sc.render.resolution_x, sc.render.resolution_y = iw // 2, ih // 2
        shot(name)
    if not only or 'aerial' in only:
        ae = C.aerial or {}
        show(() if ae.get('roof') else ('roof',))
        pts = [o.matrix_world @ Vector(c) for o in sc.objects if o.type == 'MESH' for c in o.bound_box]     # 기본 구도는 메시 전체 범위에 맞춘다
        bx, by = (min(p.x for p in pts) + max(p.x for p in pts)) / 2, (min(p.y for p in pts) + max(p.y for p in pts)) / 2
        size = max(max(p.x for p in pts) - min(p.x for p in pts), max(p.y for p in pts) - min(p.y for p in pts))
        cam.data.type = 'PERSP'
        cam.data.lens = ae.get('lens', 28)
        cam.data.clip_end = 500
        cam.location = ae.get('cam', (bx + size * 0.75, by - size * 0.9, ztop + size * 0.85))
        cam.rotation_euler = (Vector(ae.get('target', (bx, by, ztop / 2 - 1.0))) - Vector(cam.location)).to_track_quat('-Z', 'Y').to_euler()
        sun.rotation_euler = (math.radians(40), 0, math.radians(-115))
        sc.render.resolution_x, sc.render.resolution_y = 1920, 1200
        shot('aerial')
    show(())
    sun.rotation_euler = (math.radians(52), 0, math.radians(-115))   # 비스듬한 햇빛(방향은 임의)
    sc.view_settings.exposure = -1.5
    cam.data.type = 'PERSP'
    cam.data.clip_start = 0.05
    sc.render.resolution_x, sc.render.resolution_y = 1600, 900
    for name, (key, cp, cz, tp, tz, lens) in C.shots.items():
        if only and name not in only:
            continue
        fl = C.fl[key]
        loc = Vector((*fl.fr.P(cp), fl.z + cz))
        cam.location = loc
        cam.rotation_euler = (Vector((*fl.fr.P(tp), fl.z + tz)) - loc).to_track_quat('-Z', 'Y').to_euler()
        cam.data.lens = lens
        nearby = sorted((L for L in lights if (L.location.xy - loc.xy).length <= C.light_radius and (L['floor'] == key or L['void'])),
                        key=lambda L: (L.location.xy - loc.xy).length_squared)[:C.light_limit]
        for L in lights:
            L.hide_render = L not in nearby
        shot(name)
    return done


# ---------------------------------------------------------------- 실행
def load_ifc_source():
    """저장한 IFC 를 Bonsai 로 불러 IFC 원본 씬을 만든다. 게임 메시는 이 씬의 객체가 아니라 IFC 파일에서 만든다."""
    try:
        bpy.ops.bim.load_project(filepath=C.ifc_path, should_start_fresh_session=False)
        bpy.context.scene.name = C.source_scene
        LOG['bonsai_source_objects'] = len(bpy.context.scene.objects)
    except Exception as e:  # Bonsai 가 없으면 원본 씬 없이 진행(IFC 파일은 그대로 원본)
        LOG['bonsai_source_objects'] = 'load failed: %r' % (e,)
    print('IFC_SOURCE', LOG['bonsai_source_objects'])


def build_all(do_render=True, source=True):
    t0 = time.time()
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    M = compute()
    write_ifc(M)
    if source:
        load_ifc_source()
    D = read_ifc()
    build_game(D)
    res = validate(M, D)
    bpy.ops.wm.save_as_mainfile(filepath=C.blend_path)
    for fl in C.floors:
        print('OVERLAY', plan_overlay(fl, D[fl.key]))
    print('BUILD_DONE %.1fs' % (time.time() - t0), json.dumps({k: res[k] for k in ('mesh_totals', 'circ_stairs', 'coplanar_same_facing_overlap')}, ensure_ascii=False))
    for key in C.keys:
        r = res[key]
        print(key, json.dumps({k: r[k] for k in ('counts', 'doors', 'plan_match', 'areas_m2', 'wall_gap_closing_3mm', 'wall_gap_closing_10mm', 'wall_gap_closing_30mm',
                                                 'free_space_slivers_under_6cm')}, ensure_ascii=False))
    print('VERTICAL', json.dumps(res['vertical_alignment'], ensure_ascii=False))
    print('IFC', json.dumps(res['ifc_counts']))
    print('PROBLEMS', len(res['problems']))
    for p in res['problems']:
        print('  -', p)
    if do_render:
        print('RENDER_DONE', render())
    return res


def main(argv):
    if not argv or argv[0].startswith('--'):
        print(__doc__)
        return
    opts = [a for a in argv[1:] if a.startswith('--')]
    out = argv[argv.index('--out') + 1] if '--out' in argv else None
    rest = [a for a in argv[1:] if not a.startswith('--') and a != out]
    unknown = [o for o in opts if o not in ('--no-render', '--no-source', '--render-only', '--out')]
    if unknown:
        raise SystemExit('모르는 옵션: %s' % unknown)
    load_project(argv[0], out)
    if '--render-only' in opts:
        bpy.ops.wm.open_mainfile(filepath=C.blend_path)
        print('RENDER_DONE', render(rest or None))
    else:
        res = build_all(do_render='--no-render' not in opts, source='--no-source' not in opts)
        return 2 if res['problems'] else 0


def fail_exit(code, message):
    """Python과 Blender C stdio를 먼저 비워 원인 한 줄이 실제 마지막 줄이 되게 한다."""
    import ctypes
    sys.stdout.flush()
    sys.stderr.flush()
    for runtime in (('ucrtbase', 'msvcrt') if os.name == 'nt' else (None,)):
        ctypes.CDLL(runtime).fflush(None)
    print(message, flush=True)
    os._exit(code)


if __name__ == '__main__':
    sys.modules.setdefault('plan_build', sys.modules['__main__'])      # 추적 파일의 from plan_build import ... 가 이 모듈을 다시 읽지 않게
    try:
        code = main(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []) or 0
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        fail_exit(1, f'BUILD_ERROR {type(exc).__name__}: {exc}')
    if code:
        fail_exit(code, f'BUILD_INVALID PROBLEMS: validation.json을 확인한다 (exit {code})')
