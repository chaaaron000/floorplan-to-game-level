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
| 백그라운드 Blender | `plan_build.py` (추적 파일 → IFC·blend·검사·그림) | Blender 5.1 이상(작성자는 5.2.2 LTS), Bonsai 확장(ifcopenshell, shapely 동봉), numpy, Pillow(Blender 파이썬에 포함·동봉) |

작성자가 검증한 환경: Windows 11 x64, Python 3.14, Blender 5.2.2 LTS, Bonsai 0.9.0. macOS/Linux 는 Bonsai 확장이 지원하면 같은 방식으로 되겠지만 작성자가 확인하지 않았습니다.

## 1. 저장소 위치

이 저장소 폴더 전체가 하나의 스킬입니다. Claude Code 라면 프로젝트의 `.claude/skills/floorplan-trace/` 에 둡니다.

```
git clone https://github.com/chaaaron000/floorplan-trace .claude/skills/floorplan-trace
```

이미 그 위치에 있다면 건너뜁니다. 확인: `.claude/skills/floorplan-trace/SKILL.md` 가 있다.

## 2. 시스템 Python 의존성

```
python -m pip install numpy pillow pymupdf
```

(`python` 이 없으면 `python3`, 또는 Windows 의 `py -3`.) 확인:

```
python check_env.py          # 결과: PASS 가 나와야 한다. pymupdf 는 OPTIONAL
python plan_tools.py selftest   # 마지막 줄이 "selftest: PASS ..." 여야 한다
```

`selftest` 는 `plan_tools_selftest/` 폴더를 만듭니다. 확인 뒤 지워도 됩니다(.gitignore 에 있음).

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

### 3-2. Bonsai 확장 설치

Bonsai(IFC 저작 확장, https://bonsaibim.org/)는 Blender 확장 저장소에서 설치합니다. 이 확장이 `ifcopenshell`, `shapely` 등을 같이 가져옵니다.

먼저 이미 설치돼 있는지 확인합니다.

```
"<blender 경로>" -b --python check_env.py
```

`bonsai`, `ifcopenshell`, `shapely` 가 모두 OK 면 3-3 으로 갑니다. 아니면 명령줄로 설치합니다(인터넷 필요).

```
"<blender 경로>" -b --command extension repo-sync
"<blender 경로>" -b --command extension install --enable bonsai
```

위가 실패하면(오프라인, 저장소 설정 없음 등) Blender 를 GUI 로 열어 `Edit > Preferences > Get Extensions` 에서 `Bonsai` 를 설치·활성화하도록 사용자에게 안내합니다. 활성화 상태는 사용자 설정에 저장되어 `-b` 실행에도 적용됩니다.

Bonsai 의 휠은 Python 3.13 용입니다. Blender 5.x 가 번들하는 Python 과 맞는지는 위 확인으로 가립니다.

### 3-3. 확인

```
"<blender 경로>" -b --python check_env.py
```

출력에 다음이 모두 `OK` 이고 마지막이 `결과: PASS` 여야 합니다: `bpy`, `numpy`, `PIL`, `shapely`, `ifcopenshell`, `ifcopenshell.api`, `bonsai`. 출력에 다른 애드온의 로그가 섞여 나올 수 있으나 무시합니다.

`MISSING` 이 있으면: 해당 이름의 패키지가 Blender 파이썬에 없는 것입니다. 빠진 게 `bonsai`/`ifcopenshell`/`shapely` 이면 3-2 를 다시, `PIL`/`numpy` 이면 사용 중인 Blender 빌드가 표준 배포판인지 확인합니다(Blender 파이썬에는 보통 numpy 가, Bonsai 휠에 pillow 가 들어 있다).

## 4. 프로젝트 쪽 준비 (평면 한 장을 모델링하기 전)

1. 원본 도면 그림을 둘 폴더를 정합니다(작성자는 저장소 루트의 `refs/floorplans/<건물>/`, git 제외). `plan_tools.py` 는 상대 경로를 현재 폴더 → 추적 파일 폴더 → 그 상위 폴더들 순서로 찾습니다.
2. 새 작업 폴더에 [references/trace_template.py](references/trace_template.py) 를 복사해 층마다 추적 파일 하나와 `project.py` 하나를 만듭니다.
3. 빌드:
   ```
   "<blender 경로>" -b --python .claude/skills/floorplan-trace/plan_build.py -- <작업 폴더>/project.py
   ```
   옵션: `--no-render`(렌더 생략), `--no-source`(Bonsai 원본 씬 불러오기 생략), `--render-only`, `--out <폴더>`.
4. 산출물: IFC, blend, `validation.json`(벽 틈·문·공간·비매니폴드·겹친 면·층간 일치·평면 일치도), 평면 겹침 그림(`plan_overlay`, `plan_diff`), 렌더.

절차(축척 근거 모으기 → 평면 읽기 → 재기 → 적기 → 빌드 → 대조)와 규칙은 [SKILL.md](SKILL.md) 를 그대로 따릅니다. **자동 윤곽 추출 결과를 벽 목록으로 옮기지 않고, 에이전트가 직접 읽어 정한 벽을 추적 파일에 적는 것이 이 스킬의 핵심 규칙입니다.**

## 5. 선택 사항 (없어도 됨)

- **Blender MCP / Bonsai MCP:** 작성자는 눈으로 확인할 때만 썼습니다. 빌드는 위의 백그라운드 명령만으로 됩니다.
- **Git LFS:** 큰 도면 이미지를 저장소에 둘 때만 필요합니다.

## 끝났다고 보고하는 기준

아래를 모두 채우고 출력 요약을 사용자에게 보여 줍니다.

- [ ] `python check_env.py` → `결과: PASS`
- [ ] `python plan_tools.py selftest` → 마지막 줄 `selftest: PASS`
- [ ] `blender -b --python check_env.py` → `결과: PASS` (bpy, numpy, PIL, shapely, ifcopenshell, bonsai 모두 OK)
- [ ] 사용한 Blender 경로와 버전, Bonsai 버전(`check_env.py` 출력 또는 확장 목록)을 보고
- [ ] 설치하지 못한 것이 있으면 이름과 이유를 그대로 보고(추측으로 "됐다"고 하지 않는다)
