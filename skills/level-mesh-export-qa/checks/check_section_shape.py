"""단면 형상: 벽·바닥판 셸을 수평으로 잘라 짧은 변·턱·비틀림·불규칙한 곡선을 센다(mesh_shape_qa.py 를 FBX·씬에 돌린다).

창틀·문틀·유리처럼 원래 몇 cm 짜리 부재가 있는 메시는 짧은 변이 정상이라 넣지 않는다. 실제 사선이 있으면 --shape-directions 로 각도를 준다.
걸리면 메시를 고치지 말고 형상의 원본(평면 추적 값)으로 돌아가 그 벽의 가장자리를 다시 잰다.

  blender -b --factory-startup --python check_section_shape.py -- --fbx level.fbx --shape-objects "Walls$" [--shape-z 0.15 --shape-z 1.2] --out shape.json
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402


def add_args(ap):
    ap.add_argument('--shape-objects', help='단면을 볼 메시 이름 정규식(벽·바닥판 셸)')
    ap.add_argument('--shape-z', type=float, action='append', help='자르는 높이(m, 바닥 기준). 기본 0.15 와 1.2')
    ap.add_argument('--shape-directions', type=float, nargs='*', default=[], help='실제로 있는 사선 방향(도)')


def run(level, a):
    names = level.select(a.shape_objects, []) if a.shape_objects else []
    if not names:
        return {'section_shape': qa.skipped('--shape-objects 로 벽·바닥판 메시를 골라야 한다')}
    import mesh_shape_qa as msq
    zs = a.shape_z or [0.15, 1.2]
    out = {}
    for name in names:
        co, tri = level.world(name)
        rep = msq.analyze([(name, z, msq.section_loops(co, tri, level.floor_z + z)) for z in zs], tuple(a.shape_directions))
        out[name] = {k: rep[k] for k in ('loops', 'edges', 'edges_under_2cm', 'edges_under_5cm', 'counts', 'passed')}
        out[name]['issues'] = rep['issues'][:40]
    return {'section_shape': {'z_m': list(zs), 'meshes': out, 'issue_count': int(sum(sum(v['counts'].values()) for v in out.values())),
                              'passed': all(v['passed'] for v in out.values())}}


if __name__ == '__main__':
    qa.guard(lambda: qa.main(__doc__, add_args, run, __file__))
