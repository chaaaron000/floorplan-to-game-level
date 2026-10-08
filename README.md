# floorplan-trace

평면도 이미지·PDF를 **직접 읽고 재서** 벽·문·기둥·코어·계단을 추적 파일에 적고, 공용 빌더로 IFC, 게임용 메시, 검사 결과, 평면 겹침 그림을 만드는 AI 에이전트용 스킬입니다(Claude Code 스킬 형식). 자동 윤곽 추출이 아니라 "읽어서 정한 벽 목록"을 정본으로 삼는 것이 핵심입니다.

작동 방식과 절차는 [SKILL.md](SKILL.md)에 있습니다(한국어).

## 구성

| 파일 | 내용 |
|---|---|
| `SKILL.md` | 스킬 본문: 절차(그림 준비 → 축척 → 읽기 → 재기 → 적기 → 빌드 → 대조), 규칙 |
| `plan_tools.py` | 렌더링, 좌표 격자 타일·크롭, 벽 띠 재기, 원 맞춤, 축척 대조, 층 맞춤 (`python plan_tools.py -h`) |
| `plan_build.py` | 추적 파일 → IFC·blend·`validation.json`·겹침 그림·렌더 |
| `INSTALL.md`, `check_env.py` | 에이전트용 설치 안내와 설치 확인 스크립트 |
| `references/` | 추적 파일 틀(`trace_template.py`), 빌더 사용법, 문·창 기호 읽는 법, 도면 계열별 주의점 |
| `profiles/`, `tests/` | 도면 계열별 프로필, 축척 근거 예시(`scale.json`, `source.json`) |

## 설치

**에이전트에게 맡기려면:** 저장소를 `.claude/skills/floorplan-trace` 에 받은 뒤 에이전트에게 이렇게 말하면 됩니다.

> `.claude/skills/floorplan-trace/INSTALL.md` 를 읽고 그대로 설치하고 확인까지 끝내줘.

[INSTALL.md](INSTALL.md) 는 에이전트가 순서대로 따라 하며 스스로 확인하도록 쓴 안내입니다(Blender 설치처럼 시스템에 영향을 주는 단계는 사용자 승인을 먼저 받게 되어 있습니다).

직접 하려면:

```
git clone https://github.com/chaaaron000/floorplan-trace .claude/skills/floorplan-trace
python -m pip install numpy pillow pymupdf
python .claude/skills/floorplan-trace/check_env.py
```

## 의존성

| 필요한 것 | 용도 | 비고 |
|---|---|---|
| Python 3 + `numpy`, `Pillow` | `plan_tools.py` (측정 도구) | 필수 |
| `PyMuPDF` (`pymupdf`) | PDF 도면 렌더링, 축척 막대·치수 글자 읽기 | PDF 입력 시 |
| Blender 5.1 이상 (작성자 5.2.2 LTS) | `plan_build.py` 실행(백그라운드 `-b`) | 필수 |
| Bonsai 확장 (0.8.5 이상, 작성자 0.9.0) | `ifcopenshell`, `shapely` 를 Blender 파이썬에 제공, IFC 원본 씬 | 필수 |
| BlenderMCP, ifc-bonsai-mcp | 에이전트가 열린 Blender 를 보며 작업·확인 | 권장(빌드 자체에는 불필요) |

Blender·Bonsai·MCP 설치는 작성자가 쓴 [ProfRino/bonsai-bim-skills](https://github.com/ProfRino/bonsai-bim-skills) 의 INSTALL.md 를 따릅니다(자세한 대응은 이 저장소의 [INSTALL.md](INSTALL.md) §3-2).

작성자가 확인한 환경은 Windows 11 x64 + Python 3.14 + Blender 5.2.2 LTS + Bonsai 0.9.0 입니다. 다른 OS 는 확인하지 못했습니다. 설치 확인은 `python check_env.py`(시스템 Python)와 `blender -b --python check_env.py`(Blender) 두 번입니다.

## 이 스킬로 모델링을 시키려면

에이전트에게 평면도 파일과 함께 이렇게 요청합니다.

> `floorplan-trace` 스킬로 `<도면 파일>` 의 `<층/세대>` 를 모델링해줘.

에이전트는 SKILL.md 의 절차를 따릅니다: 그림 준비 → 축척(근거 두 종류 이상) → 평면 읽기 → 원본 픽셀에서 재기 → 추적 파일 쓰기 → 빌드 → 겹침 그림으로 대조. 한 평면은 한 에이전트가 한 컨텍스트에서 끝까지 하는 것을 권장합니다.

## 알아 둘 것

- `tests/*/source.json`의 `refs/floorplans/...` 경로는 원작자의 로컬 도면을 가리키는 예시입니다. 도면 원본은 이 저장소에 포함되어 있지 않습니다. 도면의 저작권은 각 권리자에게 있으니, 본인이 사용 권한이 있는 도면으로 쓰세요.
- 빌더가 IFC에 쓰는 속성 집합 이름에 `Caldera_` 접두사가 남아 있습니다(원 프로젝트 이름). 동작에는 영향이 없습니다.
- 이 스킬은 한 프로젝트(PvE 게임 레벨 제작)에서 쓰던 것을 그대로 공개한 것이라 다른 환경에서는 다듬어야 할 수 있습니다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
