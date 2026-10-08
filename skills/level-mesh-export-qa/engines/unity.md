# Unity 규약과 임포트 검수

> **검증 한계.** 이 규약과 임포트 검수 코드는 작성자가 **2026-10-02에 Unity 6(6000.3), URP**에서 마지막으로 확인했다. 그 뒤 작성자의 프로젝트는 Unreal로 옮겨 Unity 쪽은 다시 확인하지 않았다. `checks/` 스크립트는 2026-10-08에 그때 내보낸 FBX(표식을 empty로 넣는 아래 규약) 한 개에 다시 돌려 확인했다(아래 "확인한 수치"). Unity 버전이나 임포트 설정이 다르면 아래 C# 결과를 먼저 의심한다.

## 내보내기 규약

- **축·단위**: Blender FBX 내보내기에서 `axis_forward='-Z'`, `axis_up='Y'`, 미터, `bake_space_transform=False`, `add_leaf_bones=False`, `bake_anim=False`, `use_custom_props=True`. Unity 임포트는 파일 단위·파일 스케일을 쓰고(`useFileUnits`, `useFileScale`, `globalScale 1`) 축 변환을 굽지 않는다(`bakeAxisConversion` 끔). 정적 메시 객체와 루트는 항등 변환으로 둔다.
- **표식은 FBX 안의 empty다.** `DOOR_...`(문 위치·폭), `ROOM_...`(방 대표점) empty에 커스텀 속성을 달아 내보낸다. 빌더와 런타임이 프리팹에서 그 표식을 읽는다. `checks/`도 `--manifest` 없이 돌리면 이 empty를 표식으로 읽는다. 검사가 읽는 속성:
  - `ROOM_`: `height_m` 또는 `ceiling_height_m`(기대 천장고), `floor_kind`(바닥이 없는 공간이면 `void`)
  - `DOOR_`: `width_m` 또는 `door_width`(도달 가능성의 문 통과), `depth_m`(선택), `hinges_world_json`·`swing`(문 최대 열림. 뜻은 `checks/check_door_swing.py` 머리말)
- **문짝**은 움직임 피벗(경첩) empty 밑의 독립 메시로 둔다. 닫힌 자세로 내보낸다.
- **UV2**: 둘째 UV 채널을 Blender에서 만들어 내보낸다(Unity의 `uv2`, 라이트맵). 임포트의 "Generate Lightmap UVs"는 끈다(`generateSecondaryUV 0`). 켜면 내보낸 UV2를 덮어쓴다.
- **렌더 메시는 재질 경계에서 열려 있어도 된다**(솔리드를 합친 뒤 재질별로 나눈 표면). 닫힘은 `COL_` 메시와 렌더 메시의 용접 합본에서 본다.
- **`COL_` 메시**: FBX에서는 이름으로만 구분된다. Unity는 이것을 **켜진 MeshRenderer**로 들여오고 **MeshCollider는 붙이지 않는다**(`addColliders 0`). 렌더러 끄기와 MeshCollider 연결(정적 지형은 비볼록 MeshCollider)은 빌더가 한다.

## `checks/` 실행(이 규약용 인자)

```
blender -b --factory-startup --python <이 스킬>/checks/run_all.py -- --fbx <level.fbx> --out validation.json \
    --allow-empties --expect-render <N> --expect-collision <N> --expect-markers DOOR_=<N> --expect-markers ROOM_=<N> \
    --closed "^COL_" --aggregate "^SM_" --pen-objects "^COL_" \
    --floating-objects "..." --gap-objects "..." --shape-objects "..." \
    --capsule-radius <m> --capsule-height <m> --step-height <m> --seed-marker ROOM_<현관 등>
```

- 재질 분할 렌더 메시는 닫혀 있지 않으므로 교차 부피·접합 틈·보행 격자에는 닫힌 `COL_` 메시를 고른다. 떠 있음·단면 형상처럼 요소별로 봐야 하는 검사는 정적 충돌 메시가 통째로 합친 한 덩어리면 뜻이 없다. 그때는 내보내기 전 Blender 씬(요소가 아직 나뉘어 있는 닫힌 메시)에 `--scene`으로 돌리고, 그렇게 했다고 보고한다.
- 캡슐 값은 플레이어의 `CharacterController`(radius, height, stepOffset)나 NavMesh 에이전트 설정에서 읽는다.

## Unity 임포트 검수

FBX를 Unity가 들여온 뒤(에디터는 임포트 확인에만 쓴다) 에디터에서 C#을 실행한다. 2026-10-02에는 Unity CLI의 `unity shell --protocol ndjson`에 `{"argv":["command","eval","--code",<아래 코드>,"--format","json"]}`을 보내 돌렸다. 에디터 스크립트나 다른 실행 수단을 써도 된다.

```csharp
var asset = UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEngine.GameObject>("Assets/<경로>/<이름>.fbx");
int uv2Ready = 0, enabledCOL = 0, renderers = 0;
var shaders = new System.Collections.Generic.HashSet<string>();
foreach (var filter in asset.GetComponentsInChildren<UnityEngine.MeshFilter>(true))
{
    if (!filter.name.StartsWith("COL_"))
    {
        if (filter.sharedMesh.uv2.Length != filter.sharedMesh.vertexCount) { throw new System.Exception("UV2 lost: " + filter.name); }
        uv2Ready++;
    }
}
foreach (var r in asset.GetComponentsInChildren<UnityEngine.MeshRenderer>(true))
{
    renderers++;
    if (r.name.StartsWith("COL_") && r.enabled) { enabledCOL++; }
    foreach (var m in r.sharedMaterials) { shaders.Add(m.shader.name); }
}
var data = new { uv2Ready, importedMeshRenderers = renderers, enabledCollisionRenderers = enabledCOL,
    meshColliderComponents = asset.GetComponentsInChildren<UnityEngine.MeshCollider>(true).Length, materialShaders = shaders };
return Newtonsoft.Json.JsonConvert.SerializeObject(data, Newtonsoft.Json.Formatting.Indented);
```

| 항목 | 기준 |
|---|---|
| `uv2Ready` | 렌더 메시 수와 같다(UV2가 임포트에서 살아남음) |
| `importedMeshRenderers` | 렌더 + `COL_` 메시 수 |
| `enabledCollisionRenderers` | 빌더가 손대기 전에는 `COL_` 메시 수와 같다(켜진 채 들어온다). 빌더를 거친 프리팹에서는 0 |
| `meshColliderComponents` | FBX 에셋에서는 0. 빌더를 거친 프리팹에서는 `COL_` 메시 수 |
| `materialShaders` | 프로젝트의 렌더 파이프라인 셰이더만(예: `Universal Render Pipeline/Lit`) |

빌더를 건드리지 않는 작업이면 숫자만 보고한다. 표식 empty의 개수·이름·커스텀 속성이 임포트에서 살아남았는지도 같은 방식으로 센다.

## 확인한 수치(예시, 한 세대 FBX)

| 출처 | 수치 |
|---|---|
| Unity 임포트(2026-10-02) | `uv2Ready` 18, MeshRenderer 29, 켜진 `COL_` 렌더러 11, MeshCollider 0, 셰이더 `Universal Render Pipeline/Lit` |
| `checks/`(2026-10-08, 같은 FBX) | 메시 29(렌더 18·충돌 11), 표식 `DOOR_` 9·`ROOM_` 12, 스케일 1, 렌더 삼각형 5,050 / 충돌 메시 비매니폴드 0·면적 0은 0, 렌더 메시별 열린 가장자리 수가 2026-10-02 기록과 같음(8개 메시), 용접 합본 열린 가장자리 0 / 충돌 메시 교차 부피 최대 3.1e-8 m³(기록 3.8e-8) / z-fighting 0쌍(그때와 같은 기준 25cm²) / 천장고 12/12 |
| `checks/`로 확인하지 못한 것 | 이 FBX의 `DOOR_` empty에는 폭·경첩 속성이 없어 문 통과와 문 최대 열림은 돌지 않았다. 보행 격자는 임의의 캡슐 값으로 실행만 확인했다(그 게임의 값으로 판정한 적 없음). 떠 있음·접합 틈·단면 형상은 돌리지 않았다 |
| `checks/`가 새로 찾은 것 | 정적 충돌 메시에 두께 0 조각 13개(고체 합치기가 남긴 등 맞댄 면. 2026-10-02 검사는 이것을 세지 않았다) |
