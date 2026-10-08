# INSTALL.md — 에이전트용 설치 안내

이 문서는 AI 에이전트(Claude Code 등)가 읽고 **스스로 설치와 확인을 끝내도록** 쓴 안내입니다. 위에서부터 순서대로 하고, 각 단계의 "확인"이 통과하기 전에는 다음으로 가지 않습니다. 설치가 끝나면 맨 아래 "끝났다고 보고하는 기준"을 채운 뒤 사용자에게 보고합니다.

사용자에게 묻지 말고 진행할 수 있는 것: 이 폴더 안에서의 파일 작업, `pip install`(사용자 Python 환경), 읽기 전용 확인 명령.
사용자에게 먼저 알리고 승인받을 것: Blender 설치(용량이 크고 시스템 설치임), 시스템 전체에 영향을 주는 설정 변경.

## 0. 이 저장소가 무엇인지

`floorplan-trace` 는 평면도 이미지·PDF를 에이전트가 직접 읽어 벽·문·기둥·코어·계단을 **추적 파일(Python)** 에 적고, 공용 빌더가 그 추적 파일에서 IFC, 게임용 메시(.blend), 검사 결과(`validation.json`), 평면 겹침 그림을 만드는 스킬입니다. 파일 구성은 [README.md](README.md) 를 봅니다.

두 가지 실행 환경이 필요합니다.

| 환경 | 쓰는 파일 | 필요한 것 |
|---|---|---|
| 시스템 Python | `plan_tools.py` (렌더링, 좌표 격자, 벽 띠 재기, 축척 대조 등 측정 도구) | Python 3, numpy, Pillow, (PDF 입력 시) PyMuPDF |
| 백그라운드 Blender | `plan_build.py` (추적 파일 → IFC·blend·검사·그림) | Blender 5.1 이상(작성자는 5.2.2 LTS), Bonsai 확장(ifcopenshell, shapely 동봉 — 설치는 [ProfRino/bonsai-bim-skills](https://github.com/ProfRino/bonsai-bim-skills) 안내를 따른다), numpy, Pillow(Blender 파이썬에 포함·동봉) |

작성자가 검증한 환경: Windows 11 x64, Python 3.14, Blender 5.2.2 LTS, Bonsai 0.9.0. macOS/Linux 는 Bonsai 확장이 지원하면 같은 방식으로 되겠지만 작성자가 확인하지 않았습니다.

## 1. 스킬 폴더 위치

이 저장소에서 스킬은 `skills/floorplan-trace/` 폴더입니다. 이 폴더를 **사용자 프로젝트의** `.claude/skills/floorplan-trace/` 에 둡니다(복사 또는 심볼릭 링크). 이후 문서의 `<SKILL>` 은 그 폴더(`<프로젝트>/.claude/skills/floorplan-trace`)를 뜻합니다.

```
git clone https://github.com/chaaaron000/floorplan-trace <받을 곳>
mkdir -p <프로젝트>/.claude/skills
cp -r <받을 곳>/skills/floorplan-trace <프로젝트>/.claude/skills/
```

(Windows PowerShell: `Copy-Item -Recurse <받을 곳>\skillsloorplan-trace <프로젝트>\.claude\skills\`.) 이미 `<SKILL>` 이 있다면 건너뜁니다. 확인: `<SKILL>/SKILL.md` 가 있다.

## 2. 시스템 Python 의존성

```
python -m pip install numpy pillow pymupdf
```

(`python` 이 없으면 `python3`, 또는 Windows 의 `py -3`.) 확인:

```
python <SKILL>/check_env.py          # 결과: PASS 가 나와야 한다. pymupdf 는 OPTIONAL
python <SKILL>/plan_tools.py selftest   # 마지막 줄이 "selftest: PASS ..." 여야 한다
```

`selftest` 는 현재 폴더에 `plan_tools_selftest/` 를 만듭니다. 확인 뒤 지워도 됩니다.

## 3. Blender 와 Bonsai

### 3-1. Blender 설치 확인

Blender 5.1 이상이 있어야 합니다. 경로를 찾습니다.

- Windows: `C:\Program Files\Blender Foundation\Blender 5.x\blender.exe`
- macOS: `/Applications/Blender.app/Contents/MacOS/Blender`
- Linux: `which blender`

```
"<blender 경로>" --version      # Blender 5.1 이상이어야 한다
```

없거나 5.1 미만이면 사용자에게 "Blender 5.2 LTS 를 설치해도 될까요"라고 먼저 알립니다(https://www.blender.org/download/). 승인 전에는 설치하지 않습니다.

### 3-2. Bonsai 와 MCP 설치 — ProfRino/bonsai-bim-skills 의 안내를 따른다

작성자는 Bonsai 환경을 **https://github.com/ProfRino/bonsai-bim-skills** 의 안내대로 설치했습니다. 이 스킬의 환경도 그 저장소가 요구하는 것과 같게 맞추면 됩니다. 에이전트는 그 저장소의 `INSTALL.md` 를 읽고(`gh api repos/ProfRino/bonsai-bim-skills/contents/INSTALL.md --jq .content | base64 -d`, 또는 브라우저로) 아래 항목을 그대로 설치합니다.

| 항목 | 버전 | 이 스킬에서의 역할 | 그 저장소 INSTALL.md 의 절 |
|---|---|---|---|
| Blender | 5.1 이상 (작성자 5.2.2 LTS) | 호스트 | §2 |
| Bonsai 확장 | 0.8.5 이상 (작성자 0.9.0) | `ifcopenshell`, `shapely` 등을 Blender 파이썬에 제공. **빌더에 필수** | §2 (Edit → Preferences → Get Extensions → "Bonsai" → Install, 이후 Blender 재시작) |
| BlenderMCP ([ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp)) | 최신 | 에이전트가 열린 Blender 에 파이썬을 보내고 화면을 볼 때(결과 눈으로 확인). 백그라운드 빌드에는 불필요 | §3 |
| ifc-bonsai-mcp ([Show2Instruct/ifc-bonsai-mcp](https://github.com/Show2Instruct/ifc-bonsai-mcp), MCP 서버 이름은 `bonsai-ifc`) | 최신 | 높은 수준의 IFC 도구, 즉흥 조회·수정용. 권장 | §4 |
| Claude Code 의 MCP 설정 | — | 위 두 MCP 를 에이전트에 연결 | §5 |

정리하면 **`plan_build.py` 를 돌리는 데 꼭 필요한 것은 Blender + Bonsai** 이고, MCP 두 개는 에이전트가 Blender 를 직접 열어 보며 작업·확인할 때 쓰는 보조 도구입니다. 사용자가 "그 저장소대로"라고 했으면 MCP 까지 설치합니다.

그 저장소의 §6~7(그 저장소의 `bonsai-*` 스킬 7개를 `~/.claude/skills/` 에 연결)은 **이 스킬과 별개의 선택 사항**입니다. 이 스킬의 빌더는 그 스킬들에 의존하지 않습니다. 같은 Bonsai 위에서 벽·문·계단 등을 손으로 더 만들고 싶을 때만 설치합니다.

이미 설치돼 있는지는 3-3 의 확인으로 먼저 봅니다. 확장 설치가 막히면(오프라인 등) Blender 를 열어 Get Extensions 화면에서 설치하도록 사용자에게 안내합니다. Blender 는 시스템 설치이므로 없으면 사용자 승인을 먼저 받습니다(3-1).

MCP 연결 확인(선택): Claude Code 에서 `/mcp` 를 열어 `Blender`(또는 `blender`)와 `bonsai-ifc` 가 Connected 인지 봅니다. 두 서버는 Blender 가 켜져 있고 각 애드온의 서버가 시작된 상태여야 연결됩니다.

### 3-3. 확인

```
"<blender 경로>" -b --python <SKILL>/check_env.py
```

출력에 다음이 모두 `OK` 이고 마지막이 `결과: PASS` 여야 합니다: `bpy`, `numpy`, `PIL`, `shapely`, `ifcopenshell`, `ifcopenshell.api`, `bonsai`. 출력에 다른 애드온의 로그가 섞여 나올 수 있으나 무시합니다.

`MISSING` 이 있으면: 해당 이름의 패키지가 Blender 파이썬에 없는 것입니다. 빠진 게 `bonsai`/`ifcopenshell`/`shapely` 이면 3-2 를 다시, `PIL`/`numpy` 이면 사용 중인 Blender 빌드가 표준 배포판인지 확인합니다(Blender 파이썬에는 보통 numpy 가, Bonsai 휠에 pillow 가 들어 있다).

## 4. 프로젝트 쪽 준비 (평면 한 장을 모델링하기 전)

1. 원본 도면 그림을 둘 폴더를 정합니다(작성자는 저장소 루트의 `refs/floorplans/<건물>/`, git 제외). `plan_tools.py` 는 상대 경로를 현재 폴더 → 추적 파일 폴더 → 그 상위 폴더들 순서로 찾습니다.
2. 새 작업 폴더에 `<SKILL>/references/trace_template.py` 를 복사해 층마다 추적 파일 하나와 `project.py` 하나를 만듭니다.
3. 빌드:
   ```
   "<blender 경로>" -b --python <SKILL>/plan_build.py -- <작업 폴더>/project.py
   ```
   옵션: `--no-render`(렌더 생략), `--no-source`(Bonsai 원본 씬 불러오기 생략), `--render-only`, `--out <폴더>`.
4. 산출물: IFC, blend, `validation.json`(벽 틈·문·공간·비매니폴드·겹친 면·층간 일치·평면 일치도), 평면 겹침 그림(`plan_overlay`, `plan_diff`), 렌더.

절차(축척 근거 모으기 → 평면 읽기 → 재기 → 적기 → 빌드 → 대조)와 규칙은 `<SKILL>/SKILL.md` 를 그대로 따릅니다. **자동 윤곽 추출 결과를 벽 목록으로 옮기지 않고, 에이전트가 직접 읽어 정한 벽을 추적 파일에 적는 것이 이 스킬의 핵심 규칙입니다.**

## 5. 선택 사항 (없어도 됨)

- **Blender MCP / Bonsai MCP:** 작성자는 눈으로 확인할 때만 썼습니다. 빌드는 위의 백그라운드 명령만으로 됩니다.
- **Git LFS:** 큰 도면 이미지를 저장소에 둘 때만 필요합니다.

## 끝났다고 보고하는 기준

아래를 모두 채우고 출력 요약을 사용자에게 보여 줍니다.

- [ ] `python <SKILL>/check_env.py` → `결과: PASS`
- [ ] `python <SKILL>/plan_tools.py selftest` → 마지막 줄 `selftest: PASS`
- [ ] `blender -b --python <SKILL>/check_env.py` → `결과: PASS` (bpy, numpy, PIL, shapely, ifcopenshell, bonsai 모두 OK)
- [ ] 사용한 Blender 경로와 버전, Bonsai 버전(`check_env.py` 출력 또는 확장 목록)을 보고
- [ ] 설치하지 못한 것이 있으면 이름과 이유를 그대로 보고(추측으로 "됐다"고 하지 않는다)
