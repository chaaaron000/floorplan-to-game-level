# Unreal 규약과 임포트 검수

> **검증 한계.** 아래 규약은 작성자의 프로젝트에서 **UE 5.8(Interchange FBX 임포터), 2026-10**에 쓴 것이다. 내보내기와 `checks/`는 공동주택 한 층(메시 156개, 표식 175개)으로 확인했고(Blender 5.2), UE 쪽은 같은 형식의 다른 레벨 하나로 임포트·빌더·검증을 돌려 보았다(그 레벨은 원본 도면 문제로 폐기해, 끝까지 통과한 UE 검증 기록은 없다). manifest를 읽는 UE 빌더와 임포트 후 검증은 프로젝트마다 만든다. 이 스킬에는 형식과 검수 항목만 있다.

## 좌표와 FBX

- 원본은 Blender 미터·라디안, 오른손, Z 위. 메시는 **원본 월드 좌표로 구워** 내보낸다(객체 변환 항등). 문짝도 닫힌 자세의 월드 좌표다.
- Blender FBX 내보내기: `object_types={'MESH'}`(**메시만**, empty 없음), `axis_forward='-Y'`, `axis_up='Z'`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_ALL'`, `mesh_smooth_type='FACE'`, `bake_anim=False`, `use_custom_props=False`.
- UE FBX 임포터의 변환은 **(x, y, z) m → (x·100, −y·100, z·100) cm**다. 표식 좌표를 UE에 놓을 때 빌더가 같은 식을 쓴다. Y 부호가 뒤집히므로 평면 위의 각도도 부호가 뒤집힌다(원본 θ → UE yaw −θ). 경계 상자는 원본의 Y 최대가 UE의 Y 최소가 된다.
- UV0은 재질용(예: 월드 1m 상자 투영), UV1은 라이트맵용(겹치지 않게 펼친 것). 임포트에서 라이트맵 UV 자동 생성은 끈다.
- 재질 슬롯 이름(`M_...`)만 FBX에 싣고, 색·거칠기는 manifest와 빌더가 정한다(재질·텍스처 임포트 끔).

## 임포트 규약

- FBX 하나를 **메시마다 정적 메시 에셋 하나**로 들여온다(합치지 않음). 메시를 굽는 옵션(`bake_meshes`)을 켜야 에셋 이름이 오브젝트 이름이 되어 manifest와 대조된다.
- **`COL_` 메시는 UCX 볼록 껍질이 아니라 독립 메시다.** 임포터의 충돌 자동 생성은 끄고, 빌더가 그 메시의 충돌을 "복잡한 충돌을 단순 충돌로 사용(complex as simple)"으로 지정한다. 닫힌 삼각형 메시라 볼록 분해가 필요 없다. 정적 충돌 액터는 보이지 않게 두고 모든 채널을 막는다. 렌더 메시는 충돌을 끄고 내비게이션에 영향을 주지 않게 한다.
- 문짝은 렌더 메시와 `COL_` 짝이 따로 들어오고, 빌더가 문 액터(경첩이 원점)에 붙인다. 문 액터는 내비게이션을 자르지 않는다고 가정하고 보행 검사를 한다. 프로젝트의 문이 내비를 자르면 보행 검사 가정을 그에 맞춘다.

## `manifest.json` — 표식의 정본

표식(문·방)은 FBX에 없고 이 파일이 정본이다. UE 빌더는 FBX 메시를 이름으로 대조하고, 표식을 이 파일에서 읽어 액터로 놓는다. `checks/`도 `--manifest`로 같은 파일을 읽는다. 단위는 원본(미터·라디안) 그대로 적는다.

```
schema.version                 1
files[]                        층(또는 모듈)마다 하나
  floor                        층 이름
  path                         FBX 상대 경로
  floor_z_m                    층 바닥 윗면 높이
  meshes[]
    name                       FBX 오브젝트 이름 = UE 에셋 이름
    collision                  true 면 충돌 메시
    materials                  재질 슬롯 이름(순서대로). 충돌 메시는 빈 목록
    triangles                  삼각형 수
    bounds_m                   [[min x,y,z], [max x,y,z]] 월드
    pivot_m                    문짝은 경첩(층 바닥 높이), 그 밖은 원점
    properties.render_mesh     (문짝 충돌 메시) 짝이 되는 렌더 메시 이름. 이 필드로 문짝과 정적 메시를 가른다
    properties.source_ifc_guid (문짝 렌더 메시) 문 표식과 잇는 원본 ID
  markers[]
    name                       DOOR_... / ROOM_...
    position_m                 [x, y, z]
    rotation_euler_rad         [0, 0, θ]
    properties                 아래
materials[]
  name, base_color_rgba        재질 슬롯 이름과 선형 색
```

**문 표식**(`DOOR_`): `position_m`은 개구부 바닥 중심, θ는 벽이 뻗는 방향(표식 로컬 +X가 벽 방향, 로컬 +Y = (−sin θ, cos θ)).

| 필드 | 뜻 |
|---|---|
| `kind` | `hinged`(여닫이) \| `sliding`(미닫이) \| `opening`(문짝 없는 개구부) |
| `swing` | `out` = 로컬 +Y 쪽으로 열림, `in` = 반대. 여닫이만(그 밖은 `none`) |
| `hinge` | `L` = 첫 문짝의 경첩이 로컬 −X 쪽, `R` = +X 쪽. 여닫이만. 빌더가 경첩 좌표와 대조한다 |
| `hinges_world_json` | `[[경첩 x, 경첩 y, 자유 끝 x, 자유 끝 y], ...]` 문짝 순서(문자열로 넣은 JSON). 문짝 메시의 `pivot_m`과 2mm 안에서 맞아야 한다 |
| `width_m` | 개구부 폭 |
| `source_ifc_guid`, `source_ifc_name`, `source_kind` | 원본 요소와 잇는 ID·이름·원본의 문 종류 |

**방 표식**(`ROOM_`): `position_m`은 그 방에서 캐릭터가 설 수 있는 바닥 위의 점.

| 필드 | 뜻 |
|---|---|
| `floor` | 층 이름 |
| `floor_kind` | 바닥 종류(프로젝트가 정한 값). 바닥이 뚫린 공간은 `void` |
| `area_m2` | 공간 넓이 |
| `ceiling_height_m` | 바닥 윗면에서 천장 아랫면 |
| `centroid_inside_space` | 표식이 그 공간 다각형 안에 있는가(수납 앞 바닥에 둔 표식은 false) |
| `room_name_ko` 등 표시 이름 | 선택 |
| `source_ifc_guid` | 원본 공간 ID |

### 프로젝트별 확장(선택)

빌더가 읽지 않아도 되는 필드다. 넣으면 `checks/`나 프로젝트의 검증이 쓴다. 프로젝트 README에 어떤 것을 넣었는지 적는다.

| 필드 | 위치 | 쓰임 |
|---|---|---|
| `storey_pitch_m` | `files[]` | 반복층 높이. `check_reachability.py`의 `--stack` 기본값 |
| `depth_m` | 문 표식 | 개구부 깊이(벽 두께). 도달 가능성의 문 통과 띠 깊이 |
| `access` | 문 표식 | `passage`(사람 통로) \| `storage` \| `shaft` \| `elevator` 등. `--door-require access=passage`로 통로 문만 판정 |
| `max_open_deg`, `max_open_deg_per_leaf_json`, `max_open_blocker` | 문 표식 | `check_door_swing.py`가 잰 최대 열림 각을 내보내기 스크립트가 옮겨 적은 것. 엔진의 문 열림 상한으로 쓸 수 있다 |
| `asset_door_leaf`, `asset_door_frame`, `asset_exact_fit` 같은 문 에셋 교체 필드 | 문 표식 | 내보낸 문짝 대신 프로젝트의 문 에셋을 쓸 때 어느 에셋으로 바꿀지. 이름과 뜻은 프로젝트가 정한다 |
| `position_mode`, `reachable_from_core` | 방 표식 | 표식 위치를 어떻게 골랐는지(바닥 위 / 수납 앞 바닥 / 바닥 없음) |
| `STAIR_...` 표식 | `markers[]` | 계단 갈래의 범위. 층 사이 연결 검증의 `--block-rect`에 쓴다 |

## `checks/` 실행(이 규약용 인자)

```
blender -b --factory-startup --python <이 스킬>/checks/run_all.py -- --fbx <level.fbx> --manifest <manifest.json> --out validation.json \
    --floating-objects "Walls$|Columns$|Casework$" --gap-objects "Walls$|Columns$|Glass$|Frame$" --shape-objects "Walls$" \
    --capsule-radius <m> --capsule-height <m> --step-height <m> --seed-marker ROOM_<계단실> \
    --stack=-1,0,1 --zf-stack-pitch <층 높이> --door-require access=passage --min-clear-width <캡슐 지름> --nav-png nav.png
```

- 캡슐 값은 프로젝트의 내비게이션 에이전트 설정(에이전트 반지름·높이, `AgentMaxStepHeight`)과 캐릭터 캡슐에서 읽는다. 격자 칸(`--cell`)은 내비 칸 크기에 맞춘다. UE 내비는 반지름을 칸 단위로 올림하므로, 폭이 캡슐 지름에 가까운 통로는 여기서 통과해도 UE에서 끊길 수 있다. 그런 문은 폭을 보고에 적는다.
- `--swing-shrink`는 UE 쪽 문 열림 검사가 문짝 상자를 줄이는 양과 같게 맞춘다.
- 정적 메시가 요소마다 닫힌 셸이면(충돌 메시는 그것을 이어 붙인 것) 검사 대상 기본값을 그대로 쓴다.
- 반복층 모듈은 `--zf-stack-pitch`로 쌓았을 때 겹치는 면(천장판과 위층 바닥판 등)을 재서, 쌓을 때 어느 판을 뺄지 보고에 적는다.

## UE 임포트 후 검수 항목

구현(에디터 Python 빌더, 검증 커맨드릿·스크립트)은 프로젝트마다 만든다. 항목은 다음과 같다.

| 항목 | 기준 |
|---|---|
| 에셋 이름·개수 | 임포트한 정적 메시 이름 집합이 manifest `meshes[].name`과 같다 |
| 경계 대조 | 메시마다 경계 상자가 `bounds_m`을 위 식으로 바꾼 값과 맞는다(예: 0.05cm 이내). 단위·축 변환이 틀리면 여기서 걸린다 |
| 삼각형·UV·재질 슬롯 | 삼각형 수, 렌더 메시의 UV 2채널, 재질 슬롯 수·이름이 manifest와 같다 |
| 충돌 설정 | `COL_` 메시는 complex-as-simple·보이지 않음, 렌더 메시는 충돌 없음. 단순 충돌(볼록 껍질)이 자동으로 생기지 않았다 |
| 표식 배치 | 문·방 액터 수가 manifest 표식 수와 같고, 경첩 쪽(`hinge`)과 여는 방향(`swing`)이 경첩 좌표와 맞는다 |
| 방 바닥 | 바닥 있는 방 표식 아래에 충돌 바닥이 있고 천장고가 맞는다(`floor_kind=void`는 뺀다) |
| 내비 도달 | 내비 메시를 만든 뒤 시작점에서 모든 방 표식·통로 문에 경로가 있다. `checks/`의 격자 결과와 다르면 그 문·방을 보고한다 |
| 문 열림 | 문짝을 여는 각까지 돌렸을 때 정적 충돌과 겹치지 않는다(`checks/`의 최대 열림 각과 대조) |
| 문 서버 권한 | 문 여닫기가 서버에서 판정되어 모든 클라이언트와 늦게 들어온 클라이언트에서 같은 상태다. 대표 문 몇 개로 본다 |
| 걷기 | 서버·클라이언트를 띄워 캐릭터가 대표 경로(현관 → 방, 계단으로 위층)를 실제로 걷는다 |
