"""접합 틈: 벽·기둥·유리·창틀 메시를 수평으로 잘라, 닫기 반지름으로 메워지는 좁은 틈을 찾는다.

단면(z)을 각진 팽창·수축으로 닫았을 때 새로 생기는 조각이 폭 2×반지름 이하의 틈이다. 조각의 둘레가 빈 공간에
두 군데 이상 닿으면 양쪽이 뚫린 틈(gap), 한 군데만 닿으면 한쪽이 막힌 턱(notch), 닿지 않으면 고체 속에 갇힌 빈틈(cavity)이다.
판정은 gap 의 평균 폭이 기준(1mm)을 넘는 것 0. notch·cavity 는 수치만 남긴다. 평균 폭 0.1mm 미만은 실금(hairline)으로 따로 센다.
원본의 연결 정보(IFC 경로 연결 등) 없이 결과 메시만 본다. 대상 메시는 닫혀 있어야 한다.

  blender -b --factory-startup --python check_junction_gaps.py -- --fbx level.fbx --gap-objects 'Walls|Columns|Glass|Frame' [--gap-z 1.2] --out gaps.json
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


HAIRLINE = 1e-4        # 평균 폭이 이보다 좁은 조각은 실금으로 따로 센다(float32 반올림·미세한 기울기)


def add_args(ap):
    ap.add_argument('--gap-objects', help='틈을 볼 메시 이름 정규식(벽·기둥·유리·창틀. 닫힌 메시)')
    ap.add_argument('--gap-z', type=float, action='append', help='자르는 높이(m, 바닥 기준). 여러 번 줄 수 있다. 기본 1.2')
    ap.add_argument('--gap-close', type=float, default=0.015, help='닫기 반지름(m). 폭이 이 값의 두 배 이하인 틈을 찾는다')
    ap.add_argument('--gap-min-width', type=float, default=0.001, help='틈으로 치는 평균 폭(m)')
    ap.add_argument('--gap-weld', type=float, default=5e-5, help='float32 반올림으로 벌어진 맞닿은 면을 붙이는 거리(m)')


def gaps_at(level, names, z, a):
    import shapely
    solids = []
    for n in names:
        co, tri = level.world(n)
        solids += [(n, g) for _, g in qa.section_parts(co, tri, z, level.islands(n))]
    if not solids:
        return {'z_m': z, 'section_parts': 0, 'pieces': [], 'gaps_above_min_width': 0, 'gaps': 0, 'notches': 0, 'cavities': 0, 'hairlines': 0}
    geoms = [g for _, g in solids]
    tree = shapely.STRtree(geoms)
    mitre = dict(join_style='mitre', mitre_limit=10.0)
    U = shapely.union_all(geoms)
    U = U.buffer(a.gap_weld, **mitre).buffer(-a.gap_weld, **mitre)
    closed = U.buffer(a.gap_close, **mitre).buffer(-a.gap_close, **mitre)
    near_solid = U.buffer(1e-6)
    pieces = []
    for p in [g for g in shapely.get_parts(closed.difference(U)) if g.geom_type == 'Polygon' and g.area > 1e-9]:
        mouth = p.boundary.difference(near_solid)
        if mouth.geom_type in ('LineString', 'MultiLineString'):
            mouths = len(shapely.get_parts(shapely.line_merge(mouth)))
        else:
            mouths = len(shapely.get_parts(mouth))
        width = p.area / max(p.length / 2.0, 1e-12)        # 가늘고 긴 조각의 평균 폭
        c = p.representative_point()
        kind = 'gap' if mouths >= 2 else ('notch' if mouths == 1 else 'cavity')
        pieces.append({'kind': kind, 'centre_m': [qa.r6(c.x), qa.r6(c.y)], 'bounds_m': [qa.r6(v) for v in p.bounds], 'area_m2': float('%.3g' % p.area),
                       'mean_width_m': qa.r6(width), 'open_sides': mouths, 'hairline': bool(width < HAIRLINE),
                       'gap_above_min_width': bool(kind == 'gap' and width > a.gap_min_width),
                       'near': sorted({solids[i][0] for i in tree.query(p.buffer(1e-3)) if geoms[i].distance(p) < 1e-3})})

    def count(kind):
        return sum(1 for p in pieces if p['kind'] == kind and not p['hairline'])
    return {'z_m': z, 'section_parts': len(solids), 'pieces': pieces, 'gaps_above_min_width': sum(1 for p in pieces if p['gap_above_min_width']),
            'gaps': count('gap'), 'notches': count('notch'), 'cavities': count('cavity'), 'hairlines': sum(1 for p in pieces if p['hairline'])}


def run(level, a):
    names = level.select(a.gap_objects, []) if a.gap_objects else []
    if not names:
        return {'junction_gaps': qa.skipped('--gap-objects 로 벽·기둥·유리·창틀 메시를 골라야 한다')}
    floor = level.floor_z
    sections = [gaps_at(level, names, floor + z, a) for z in (a.gap_z or [1.2])]
    bad = sum(s['gaps_above_min_width'] for s in sections)
    return {'junction_gaps': {'meshes': names, 'closing_radius_m': a.gap_close, 'min_width_m': a.gap_min_width, 'sections': sections,
                              'gaps_above_min_width': bad, 'notches': sum(s['notches'] for s in sections),
                              'cavities': sum(s['cavities'] for s in sections), 'hairlines': sum(s['hairlines'] for s in sections), 'passed': bad == 0}}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
