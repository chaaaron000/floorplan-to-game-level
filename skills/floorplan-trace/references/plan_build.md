# plan_build.py — 추적 형식과 공용 빌더

`plan_build.py`는 층별 추적 파일과 프로젝트 정의를 읽어 IFC, blend, 검사 결과, 그림을 만든다. 작업자가 쓰는 것은 추적 파일과 프로젝트 정의뿐이다. 문법과 예시 값은 [`trace_template.py`](trace_template.py)에 있고(그대로 빌드되는 예시다), 이 문서는 빌더가 그 값을 어떻게 쓰는지와 결과를 읽는 법을 적는다.

## 실행

```
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --python .claude/skills/floorplan-trace/plan_build.py -- <project.py> [옵션]
```

| 옵션 | 뜻 |
|---|---|
| (없음) | 전체: IFC → IFC 원본 씬 → 게임 메시 → 검사 → blend 저장 → 겹침 그림 → 렌더 |
| `--no-render` | 렌더 생략. 추적을 고치며 되풀이할 때 쓴다(층당 10초 안팎) |
| `--no-source` | Bonsai로 IFC 원본 씬을 불러오지 않는다 |
| `--render-only [컷 ...]` | 저장된 blend로 렌더만 다시. 컷 이름(`top_<층>`, `aerial`, `shots`의 이름)을 적으면 그것만 |
| `--out <폴더>` | 산출물 폴더. 기본은 프로젝트 정의가 있는 폴더 |

끝에 `PROBLEMS <개수>`와 항목이 찍힌다. 0이 될 때까지 추적 파일을 고친다.

종료 코드: **0** 정상, **1** 추적·실행 예외(마지막 줄 `BUILD_ERROR <예외 종류>: <원인>`), **2** 빌드는 끝났지만 `PROBLEMS > 0`(마지막 줄 `BUILD_INVALID ...`). Blender의 스크립트 예외 처리에 맡기지 않고 비정상 코드를 프로세스에 전달한다. 검사 기준은 그대로다. 문 구간이 호스트보다 길면 잘라서 통과시키지 않고 예외로 끝난다.

## 산출물

| 파일 | 내용 |
|---|---|
| `<name>.ifc` | IFC4 원본. 층마다 `IfcBuildingStorey`, 요소 이름은 `<층>_<이름>`, 형상은 모두 다각형 압출 |
| `<name>.blend` | 씬 `<name>_IFC_Source`(Bonsai로 불러온 IFC)와 `<name>_Game`(게임 메시) |
| `validation.json` | 검사 결과(아래 "검사 항목") |
| `renders/plan_overlay_<층>.png` | 원본 평면 위에 IFC에서 되읽은 요소를 색으로 얹은 그림 |
| `renders/plan_diff_<층>.png` | 평면의 벽 채움과 모델 벽의 차이(회색 일치, 빨강 평면에만, 파랑 모델에만). 절반 크기 |
| `renders/top_<층>.png` | 그 층까지만 보이는 위 모습(위층과 천장을 숨긴다) |
| `renders/aerial.png`, `renders/<컷>.png` | 조감, `shots`의 실내 컷(문은 연 상태로 찍는다) |

게임 씬의 객체: 층 컬렉션마다 `SM_<층>_Walls`·`Columns`·`Floor`·`CurtainFrame`·`CurtainGlass`·`Casework`·`Stairs`·`CircularStairs`·`Pool`… 메시, `<층>_Doors` 아래 `DOOR_<층>_<이름>` 표식과 문짝(원점이 힌지, `open_rot_z`가 열림 각), `<층>_Rooms` 아래 `ROOM_<층>_<이름>` 표식(`room_name_ko`, `area_m2`, `floor_kind`). 위층 슬래브는 윗면 마감 층 `SM_<층>_Floor`와 아래층 천장 `SM_<아래층>_Ceiling`으로 나뉜다. 메시는 닫혀 있고 안쪽 면이 없다.

미닫이 표식의 `door_kind`는 `slide`, `slide_double`, `slide_glass`, `slide_double_glass`다. `open_translation_m`은 문짝별 월드 축 이동 벡터의 JSON 목록, `open_distance_m`은 한 문짝의 이동 거리(m). 문짝에도 자기 `open_translation_m` 벡터가 있다. 한 짝은 벽 축 방향 `side`로 개구부 폭만큼, 두 짝은 가운데에서 바깥으로 반 폭만큼 열린다. IFC와 저장된 blend는 닫힌 상태, 렌더는 회전 대신 이동한다. 유리는 두께 12mm·Glass 재질이다. 독립 샤워 문은 `RECT_DOORS`의 9번째 값 `{'host': None}`으로 적어 벽 개구부 없이 만든다. 일반 벽 문 검사에는 영향을 주지 않는다.

## 좌표

- 추적 좌표는 그 층 그림의 px다. 빌더는 `TO_REF`/`FROM_REF`로 기준 층 px로 옮기고, `origin_px`와 `scale`로 미터로 바꾼다. 이미지 위쪽이 +Y, 맨 아래층 바닥 윗면이 Z=0, 층 바닥은 `wall_h + slab_t`씩 올라간다.
- 길이(두께, 반지름, 모따기)도 그 층 그림의 px로 적는다. 빌더가 층 배율을 곱한다.
- 이름이 `_REF`로 끝나는 표는 기준 층 px 그대로 쓴다.
- 평면 도형은 0.5mm 격자에 맞춘다.

## 추적 파일의 표

문법은 `trace_template.py`의 주석에 있다. 아래는 빌더가 하는 일이다.

| 표 | 만드는 것 | 빌더의 처리 |
|---|---|---|
| `WALLS` | `IfcWall` 사각형 | `CUTOUTS`를 뺀다. `SNAP_TO_BELOW_PX`가 있으면 가장자리를 아래층 벽 가장자리에 맞춘다 |
| `POLY_WALLS` | `IfcWall` 다각형 | `CUTOUTS`를 뺀다 |
| `ARC_WALLS` | `IfcWall` 고리 조각 | `arc_step_deg` 조각 |
| `CARVES`, `POLY_CARVES` | `IfcWall` 하나(`CARVE_WALL`) | 유리 안쪽에서 방 범위를 뺀 나머지 |
| `DOORS`, `RECT_DOORS` | `IfcOpeningElement` + `IfcDoor` | 개구부는 문 높이까지, 그 위는 인방으로 남는다. `open`은 문짝이 없다 |
| `ARC_DOORS` | `IfcDoor` 곡면 문짝 | 개구부를 만들지 않는다. 중심선이 공간을 가른다 |
| `CLOSET_FRONTS`, `GLASS_PARTITIONS` | `IfcDoor`(미닫이 두 짝), `IfcPlate` | 유리 칸막이는 벽에 겹친 부분을 뺀다 |
| `COLUMNS` | `IfcColumn` | 높이는 이 층 벽 높이. `to`가 있으면 그 층 벽 꼭대기까지 한 기둥이고 위층의 벽 합집합·일치도에도 든다. `through_slab_columns` 종류는 위·아래 슬래브를 뚫는다 |
| `OUTLINE`, `GLAZING_THICKNESS_PX` | 실내 경계, 유리 안쪽·바깥 면 | 필수. 바닥판·공간·빈 곳의 기준 |
| `CURTAIN` | `IfcCurtainWall` `CW` | `OUTLINE` 전체가 띠. 벽·붙박이를 유리 안쪽 면에서 자른다 |
| `GLAZING` | 구간마다 `IfcCurtainWall` | 띠에서 벽·기둥과 앞선 구간을 뺀다. 가로대 `IfcMember`, 멀리언 `IfcMember`, 유리 `IfcPlate`(미닫이 베이는 `TERRACE_SLIDING`) |
| `PLATE` | 바닥판(`IfcSlab`의 범위) | 유리 바깥 면 안쪽 ∪ `PLATE` − 슬래브를 뚫는 기둥 중 가장 큰 조각 |
| `TERRACE_CURBS`, `BALUSTRADE_INSET_PX`, `RAILS` | 턱 `IfcBuildingElementProxy`, 난간 `IfcRailing` | 유리 띠와 벽·기둥에 겹친 부분을 뺀다 |
| `SPACES`, `VIRTUAL_SPLITS`, `SPLIT_RAILS` | `IfcSpace` | (실내 − 유리 띠 − 벽·기둥)과 테라스를 가상 경계·호 문·난간 선으로 나눈 칸에 시드로 이름을 붙인다 |
| `VOID_OVER` | 슬래브에서 빠지는 범위 | 바닥 종류 `void`인 칸 ∩ 아래층의 그 공간들 |
| `CASEWORK` | `IfcFurniture` | 벽에 겹친 부분을 뺀다 |
| `STAIRS` | 층마다 `IfcStair` | 디딤판·참·가운데 벽에서 벽에 겹친 부분을 뺀다. 위층 슬래브에 구멍을 낸다 |
| `CIRC_STAIRS` | `IfcStair` + 스트링거 `IfcRailing` | 이 층 바닥에서 위층 바닥까지. 구멍은 위층의 `HOLES` |
| `HOLES` | 이 층 슬래브의 구멍 | `add` 합 − `sub` 합 |
| `POOLS` | 수조 `IfcBuildingElementProxy` + 물 | 수조 자리를 이 층 슬래브에서 뺀다 |

게임 메시에서 겹칠 때의 우선순위: 기둥 > 벽(코어·덩어리 > 세대 밖 덩어리 > 랙 > 칸막이·얇은 벽 > 유리 칸막이) > 구간 묶음 유리. `CURTAIN`의 띠는 벽보다 앞선다.

벽이 다른 벽이나 기둥에 붙는 곳은 면으로 닿거나 겹치게 적는다. 모서리 점 하나로만 닿으면 그 세로 모서리가 비매니폴드로 잡힌다.

## 프로젝트 정의

`PROJECT` 사전 하나다. 필수는 `name`, `scale`, `origin_px`, `floors`, `wall_h`. 나머지 항목과 기본값은 `plan_build.py`의 `DEFAULTS`에 주석과 함께 있다. 모르는 항목을 적으면 멈춘다.

평면마다 정해야 하는 항목:

| 항목 | 정하는 기준 |
|---|---|
| `through_slab_columns` | 층을 관통해 서는 구조 기둥 종류. 바닥판과 슬래브에서 빠진다 |
| `roof` | 맨 위 테라스 위가 덮여 있으면 `own`, 열려 있으면 `shell` |
| `arc_step_deg` | 호·원을 나누는 각. 작을수록 곡면이 매끄럽고 삼각형이 늘어난다 |
| `plan_match` | 그 평면의 벽 채움 회색값 범위와, 채움으로 그려진 벽·기둥 종류. 선으로만 그린 것은 뺀다 |
| `wall_kinds`, `floor_kinds`, `materials` | 기본에 없는 벽 종류·바닥 종류·재질이 필요할 때 |
| `shots`, `aerial` | 실내 컷은 그 층 그림 px와 바닥 위 높이로 적는다 |

### 반복 모듈과 계단

- `STAIRS.kind` 기본은 `u`(기존 두 갈래가 반 층씩). `scissor`는 `lane_a`가 `run[0]→run[1]`, `lane_b`가 반대로 각각 한 층을 오른다. `risers`는 갈래마다 한 층의 챌판 수, 디딤판은 `risers-1`. `far` 쪽은 회전 참이 아니라 위·아래 도착 참이다. 가운데 `spine`·벽 빼기·위층 구멍·`repeat_up`은 같은 규칙. `validation.json.scissor_stairs`의 `riser_m`·`risers_per_lane`·`treads_per_lane`을 평면 분할과 대조한다.
- `stackable_roof=True`는 맨 위 슬래브에서 맨 위층 `HOLES`, 계단 구멍, void, 관통 기둥을 뺀다. 한 층 모듈의 맨 아래 바닥에도 그 계단 구멍을 내어 위·아래 적층 통로를 맞춘다. 수조는 맨 위에 복사하지 않는다. 기본 `False`는 기존 닫힌 캡·계단 천장 자르기 그대로.
- `plan_match.exclude`는 비교에서 뺄 IFC `ObjectType` 또는 요소 이름 목록(예: `('CASEWORK', 'CLOSET_SLIDING')`). `exclude_margin_px`는 그 층 그림 px로 바깥 여유 폭. 선택 요소의 IFC 형상 자리와 여유를 두 마스크에서 **모두** 뺀다. 차이 그림은 옅은 보라. 기본 `()`·0은 기존 수치 그대로. 벽 누락을 가구 제외로 덮지 않는다.
- 실내 컷은 `light_radius` 안 같은 층(또는 void)의 가장 가까운 `light_limit=8`개 방 조명만 켠다. 방이 많아도 POINT 그림자 큐브맵을 제한한다. 에너지·형상·저장 blend는 바뀌지 않는다.

### PDF 치수 준비

`python plan_tools.py pdfdims <PDF> --page <1부터 쪽>`은 ft-in/m 치수 쌍과 단독 mm 숫자 후보를 낸다. mm 후보는 끝이 0인 3~5자리 정수(선택적 `mm`), `m`은 미터 값 목록, `unit='mm'`, `candidate=True`. 방 번호·표 숫자가 섞일 수 있으므로 치수선의 글자인지 렌더에서 확인한다. `bbox_pt`는 **페이지 회전 적용 후** 렌더링 좌표(pt): `render_pdf` 메타와 `pt_to_px`로 픽셀로 바꾼다. 세 후보 이상을 실제 글자 위치와 대조한다. `survey`는 mm 후보를 metric 근거로 포함하고 `° ' "` 방위각의 분·초를 ft-in으로 세지 않는다. `python plan_tools.py selftest`로 기존 측정 기능을 검사한다.

## 검사 항목

`validation.json`은 층 키마다 한 묶음, 그리고 전체 항목으로 되어 있다. `problems`가 고쳐야 하는 것의 목록이고, 나머지는 판단에 쓰는 수치다.

`problems`에 들어가는 것:

| 항목 | 뜻 | 고치는 곳 |
|---|---|---|
| 공간 시드가 칸에 걸린다 | 시드가 든 칸이 없거나(벽 위·세대 밖) 다른 시드와 같은 칸이다 | 시드 위치, 빠진 벽·가상 경계 |
| 이름 없는 칸 | 0.05㎡ 넘는 닫힌 칸에 시드가 없다 | `SPACES`에 더하거나, 벽이 잘못 갈라 놓은 칸이면 벽 |
| 문 개구부가 벽 밖으로 나간다 | 개구부 사각형이 벽 합집합을 벗어난다 | 문 구간, 호스트 벽 |
| 비매니폴드 엣지, 면적 0 면 | 메시가 닫히지 않았다. `meshes.<이름>.non_manifold_at`에 위치(월드 m) | 점 하나로 닿는 벽·띠 |
| 멀리언 좌표가 중심선 범위 밖 | `GLAZING`의 멀리언 좌표가 어느 구간에도 들지 않는다 | 좌표축, 좌표 |
| 모르는 바닥 종류, 평면 그림이 없다 | 표기 오류 | `SPACES`, `IMAGE` |

층별 수치:

| 항목 | 뜻 | 읽는 법 |
|---|---|---|
| `plan_match.iou` | 평면의 벽 채움과 모델 벽·기둥(문 개구부 제외)의 겹침 비율 | 낮으면 `plan_diff_<층>.png`에서 빨강(빠뜨린 벽)과 파랑(없는 벽·두꺼운 벽)을 찾는다. 문 구간과 글자 자리는 남는다 |
| `wall_gap_closing_<d>mm` | 벽·기둥 합집합을 d만큼 닫으면 메워지는 틈의 수와 면적, 위치 px | 맞닿아야 할 벽 사이의 틈이다. 가장자리 값을 맞춘다 |
| `free_space_slivers_under_6cm` | 실내 빈 곳 중 폭 6cm 미만인 조각 | 벽과 벽, 벽과 유리 사이에 남은 틈. 플레이어가 못 들어가는 틈이면 벽을 붙인다 |
| `doors.min_width_m`, `doors.outside_wall` | 가장 좁은 문 폭, 벽 밖으로 나간 문 | 통행 폭 판단 |
| `spaces`, `unnamed_faces`, `space_errors` | 공간별 면적, 시드 없는 칸, 시드 오류 | 면적이 평면 표기와 맞는지 |
| `counts`, `areas_m2` | 요소 수, 실내·벽·테라스·바닥판 면적 | |

전체 수치:

| 항목 | 뜻 |
|---|---|
| `vertical_alignment.<아래>_<위>` | 층간 일치: 코어 벽(종류 C)의 공통·차이 면적과 큰 차이 조각의 위치, 40mm 미만 벽면 어긋남, 아래층 코어 위에 위층 벽이 없는 면적, 같은 이름 기둥의 이동, 같은 이름 유리 구간의 공통·차이, 같은 이름 코어 공간(샤프트)의 공통 면적, 유리 바깥 면 범위의 차이, 아래층에 맞춘 가장자리 수 |
| `vertical_alignment.slabs_m2`, `slab_holes_m2`, `stair_wall_overlap_m2` | 슬래브 면적, 구멍·빈 곳·수조 면적, 계단이 벽에 겹쳐 잘린 면적 |
| `circ_stairs` | 원형 계단의 디딤판 수, 챌판 높이, 디딤판 각, 가운데 선의 디딤판 깊이 |
| `meshes`, `mesh_totals` | 메시별 정점·면·삼각형 수, 비매니폴드 엣지, 면적 0 면 |
| `coplanar_same_facing_overlap` | 서로 다른 메시의 같은 방향 면이 한 평면에서 겹친 면적(깜빡임이 날 자리) |
| `coplanar_opposite_contact` | 메시끼리 맞닿은 숨은 면의 면적 |
| `ifc_counts` | IFC 클래스별·층별 요소 수 |
| `bonsai_source_objects` | IFC 원본 씬의 객체 수(불러오지 못하면 그 이유) |

## 겹침 그림의 색

주황 코어·덩어리 벽, 빨강 칸막이, 분홍 얇은 벽, 보라 세대 밖 덩어리, 갈색 랙, 파랑 기둥, 하늘 유리·가로대, 남색 멀리언, 자홍 미닫이 베이, 노랑 붙박이, 청록 난간, 연보라 계단·수조, 초록 문 개구부와 열림 호, 보라 띠 미닫이·승강기 문짝, 자홍 테두리 슬래브 구멍. 공간 이름은 파랑, 빈 곳 이름은 자홍.
