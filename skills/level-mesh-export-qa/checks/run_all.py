"""checks/ 의 검사를 한 번에 돌려 validation JSON 하나로 남긴다. FBX 는 한 번만 들여온다.

검사마다 필요한 인자가 없으면 그 검사는 돌지 않고 not_run 에 남는다(통과로 치지 않는다). 인자는 각 검사 파일 머리말을 본다.

  blender -b --factory-startup --python run_all.py -- --fbx level.fbx --manifest manifest.json --out validation.json
      --floating-objects "Walls$|Columns$|Casework$" --gap-objects "Walls$|Columns$|Glass$|Frame$" --shape-objects "Walls$"
      --capsule-radius <m> --capsule-height <m> --step-height <m> --seed-marker <표식 이름>
      [--only reachability,door_swing] [--skip zfighting]
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common as qa  # noqa: E402
import check_ceiling_height  # noqa: E402
import check_door_swing  # noqa: E402
import check_fbx_roundtrip  # noqa: E402
import check_floating  # noqa: E402
import check_interpenetration  # noqa: E402
import check_junction_gaps  # noqa: E402
import check_mesh_topology  # noqa: E402
import check_reachability  # noqa: E402
import check_section_shape  # noqa: E402
import check_zfighting  # noqa: E402

MODULES = [('fbx_roundtrip', check_fbx_roundtrip), ('mesh_topology', check_mesh_topology), ('interpenetration', check_interpenetration),
           ('zfighting', check_zfighting), ('floating', check_floating), ('junction_gaps', check_junction_gaps),
           ('ceiling_height', check_ceiling_height), ('section_shape', check_section_shape), ('reachability', check_reachability),
           ('door_swing', check_door_swing)]


def main():
    ap = qa.base_parser(__doc__)
    for _, module in MODULES:
        module.add_args(ap)
    ap.add_argument('--only', help='이 검사만(쉼표): ' + ', '.join(k for k, _ in MODULES))
    ap.add_argument('--skip', help='이 검사는 빼고(쉼표)')
    a = ap.parse_args(qa.script_argv())
    only = {k for k in (a.only or '').split(',') if k}
    skip = {k for k in (a.skip or '').split(',') if k}
    level = qa.Level(a)
    checks, seconds = {}, {}
    for key, module in MODULES:
        if (only and key not in only) or key in skip:
            continue
        t = time.time()
        try:
            got = module.run(level, a)
        except Exception:
            traceback.print_exc()
            got = {key: {'error': traceback.format_exc().strip().splitlines()[-1], 'passed': False}}
        checks.update(got)
        seconds[key] = round(time.time() - t, 1)
        qa.log('%s %s' % (key, {k: v.get('passed') for k, v in got.items()}))
    rep = qa.finish(checks, level, a, __file__)
    qa.log('검사별 시간(초) %s' % seconds)
    return rep


if __name__ == '__main__':
    qa.guard(main)
