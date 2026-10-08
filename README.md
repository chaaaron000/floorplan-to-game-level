# floorplan-trace

평면도 이미지·PDF를 **직접 읽고 재서** 벽·문·기둥·코어·계단을 추적 파일에 적고, 공용 빌더로 IFC, 게임용 메시, 검사 결과, 평면 겹침 그림을 만드는 AI 에이전트용 스킬입니다(Claude Code 스킬 형식). 자동 윤곽 추출이 아니라 "읽어서 정한 벽 목록"을 정본으로 삼는 것이 핵심입니다.

작동 방식과 절차는 [SKILL.md](SKILL.md)에 있습니다(한국어).

## 구성

| 파일 | 내용 |
|---|---|
| `SKILL.md` | 스킬 본문: 절차(그림 준비 → 축척 → 읽기 → 재기 → 적기 → 빌드 → 대조), 규칙 |
| `plan_tools.py` | 렌더링, 좌표 격자 타일·크롭, 벽 띠 재기, 원 맞춤, 축척 대조, 층 맞춤 (`python plan_tools.py -h`) |
| `plan_build.py` | 추적 파일 → IFC·blend·`validation.json`·겹침 그림·렌더 |
| `references/` | 추적 파일 틀(`trace_template.py`), 빌더 사용법, 문·창 기호 읽는 법, 도면 계열별 주의점 |
| `profiles/`, `tests/` | 도면 계열별 프로필, 축척 근거 예시(`scale.json`, `source.json`) |

## 설치

Claude Code 프로젝트의 스킬 폴더에 이 저장소를 둡니다.

```
git clone https://github.com/chaaaron000/floorplan-trace .claude/skills/floorplan-trace
```

## 요구 사항

- Python 3, `numpy`, `Pillow`. PDF를 다루려면 `PyMuPDF`.
- 빌더(`plan_build.py`)는 Blender 5.2의 백그라운드 모드에서 Bonsai(IFC) 확장의 `ifcopenshell`·`shapely`를 씁니다.
  ```
  blender -b --python plan_build.py -- <project.py>
  ```
- `python plan_tools.py selftest` 로 도구의 자체 시험을 돌릴 수 있습니다.

## 알아 둘 것

- `tests/*/source.json`의 `refs/floorplans/...` 경로는 원작자의 로컬 도면을 가리키는 예시입니다. 도면 원본은 이 저장소에 포함되어 있지 않습니다. 도면의 저작권은 각 권리자에게 있으니, 본인이 사용 권한이 있는 도면으로 쓰세요.
- 빌더가 IFC에 쓰는 속성 집합 이름에 `Caldera_` 접두사가 남아 있습니다(원 프로젝트 이름). 동작에는 영향이 없습니다.
- 이 스킬은 한 프로젝트(PvE 게임 레벨 제작)에서 쓰던 것을 그대로 공개한 것이라 다른 환경에서는 다듬어야 할 수 있습니다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
