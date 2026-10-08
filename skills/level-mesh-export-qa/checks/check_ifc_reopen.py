"""원본 재열기(IFC 원본일 때): 저장한 IFC 를 다시 열어 형상이 있는 요소가 모두 테셀레이션되는지 센다.

Blender 화면의 임시 메시가 아니라 저장된 파일이 게임 메시의 원본이어야 한다. ifcopenshell 이 있는 파이썬이면 Blender 밖에서도 돈다.

  blender -b --factory-startup --python check_ifc_reopen.py -- --ifc model.ifc --out ifc_reopen.json
  python check_ifc_reopen.py --ifc model.ifc
"""
import argparse
import json
import sys


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ifc', required=True)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    import ifcopenshell
    import ifcopenshell.geom
    f = ifcopenshell.open(a.ifc)          # 파일 객체를 변수에 잡아 둔다(임시 객체를 넘기면 해제된 파일을 읽다 죽는다)
    settings = ifcopenshell.geom.settings()
    settings.set('use-world-coords', True)
    count, failed, by_class = 0, [], {}
    for e in f.by_type('IfcProduct'):
        if e.is_a('IfcOpeningElement') or e.is_a('IfcSpatialStructureElement') or e.is_a('IfcSpace') or not e.Representation:
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, e)
            if len(shape.geometry.faces) == 0:
                raise ValueError('삼각형 0')
            count += 1
            by_class[e.is_a()] = by_class.get(e.is_a(), 0) + 1
        except Exception as exc:
            failed.append({'name': e.Name, 'class': e.is_a(), 'guid': e.GlobalId, 'error': str(exc)})
    rep = {'ifc': a.ifc, 'schema': f.schema, 'tessellated': count, 'by_class': by_class, 'failed': failed, 'passed': count > 0 and not failed}
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as handle:
            json.dump({'generated_by': 'check_ifc_reopen.py', 'passed': rep['passed'], 'checks': {'ifc_reopen': rep}}, handle, ensure_ascii=False, indent=1)
    print('QA_RESULT', json.dumps({'passed': rep['passed'], 'tessellated': count, 'failed': len(failed)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
