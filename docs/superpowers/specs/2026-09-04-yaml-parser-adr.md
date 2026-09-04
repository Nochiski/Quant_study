# ADR: Backend YAML parser와 cross-runtime YAML 1.2 계약 (P0-03)

> 작성: 2026-09-04
>
> 상태: Accepted
>
> 상위 ADR: [Strategy Authoring Contract](./2026-09-04-strategy-authoring-contract-adr.md)
> · Initiative tracker: [PLAN.md](../../planning/strategy-workbench-yaml-ui/PLAN.md)

## 1. 맥락

같은 YAML source를 frontend(`yaml` npm)와 backend(Python)가 각각 parse한다. 두 parser가 같은
scalar를 다른 타입으로 읽으면 frontend는 valid로 표시하고 backend는 다른 값을 compile하는
silent corrupt 경로가 생긴다. P0-03은 backend parser를 정하고, 양쪽이 동일한 typed tree를 만들도록
허용 문법과 fail-closed 규칙을 고정한다.

## 2. 실측

PyYAML 6.0.3(YAML 1.1), ruamel.yaml 0.19.1(`typ="safe", pure=True`, `version=(1,2)`), `yaml` 2.9.0
(`version: "1.2", schema: "core"`)으로 같은 probe를 읽은 결과. 실제 fixture는 3절의 manifest에 있다.

| plain scalar / 구조 | PyYAML (1.1) | ruamel.yaml safe (1.2) | `yaml` npm core (1.2) | 판정 |
|---|---|---|---|---|
| `yes` / `no` / `on` / `off` | bool | str | str | 1.1 금지, 문자열로 허용 |
| `010` / `0o10` / `0x1F` | int 8 / 8 / 31 | int 10 / 8 / 31 | number 10 / 8 / 31 | 1.2 core 그대로 허용 |
| `1_000` | int 1000 | int 1000 | **string "1_000"** | 양쪽 불일치 → `ambiguous_number_underscore` 거부 |
| `1e-2` | **str** | float 0.01 | number 0.01 | 1.1 금지, 허용 |
| `2021-01-01` (plain) | date | **date** | string | backend timestamp constructor를 문자열로 교체 → 양쪽 string |
| `-0.0` | float -0.0 | float -0.0 | number -0 | 허용. canonical `-0.0`→`0.0` 정규화는 P1-01 |
| `.nan` / `.inf` | float | float nan/inf | NaN / Infinity | `non_finite_number` 거부 |
| duplicate key | 마지막 값 (무음) | DuplicateKeyError | `DUPLICATE_KEY` error | `duplicate_key` 거부 |
| `&anchor` / `*alias` | 해석 | 해석 | 해석 | 정책 거부 `anchor_or_alias` (P1-02 node/alias 폭발 제한과 별개) |
| `<<: *base` merge key | 병합 | **병합** | **`"<<"` 문자열 key** | 양쪽 불일치 → alias 정책으로 거부 |
| `!custom 1` | ConstructorError | ConstructorError | **warning `TAG_RESOLVE_FAILED` + string** | `tag` 거부. frontend는 warning을 error로 취급 |
| `!!binary aGk=` | bytes | bytes | Buffer | `tag` 거부 (JSON 불가) |
| `%YAML 1.1` directive | 1.1 해석 | 1.1 해석 | 1.1 해석 | `directive` 거부 |
| 다중 document | 오류 | ComposerError | 2 documents | `multiple_documents` 거부 |
| `1: v` / `? [1,2]` key | int / tuple key | int / tuple key | `"1"` / `"[ 1, 2 ]"` 문자열화 | 양쪽 불일치 → `non_string_key` 거부 |
| 빈 문서 / scalar / sequence root | None / str / list | None / str / list | no doc / string / array | `not_a_mapping` 거부 |
| `~`, `null`, 빈 값 | None | None | null | 허용 |

## 3. 결정

### D1. Backend parser는 ruamel.yaml, YAML 1.2, pure Python safe loader다

- `ruamel.yaml>=0.19.1`을 backend runtime 의존성에 추가한다. PyYAML은 uvicorn transitive 의존성과 P0-01
  임시 테스트 loader(dev group)로만 남고 `strategy_workbench` 코드는 import하지 않는다. P1-02가 codec을
  추가할 때 `import yaml`을 architecture test로 금지한다.
- 값 tree는 `typ="safe"` loader로 만들고, source map(JSON Pointer ↔ line/column)은 P1-02가 같은 pure
  scanner의 token/mark 또는 `typ="rt"` loader의 `lc` 정보로 만든다. `rt` wrapper 타입(`ScalarInt` 등)은
  값 tree로 쓰지 않는다.
- `tag:yaml.org,2002:timestamp` constructor를 원문 문자열 반환으로 교체한다. 날짜 변환은 typed hydrate가
  한다.
- pure Python loader만 사용한다. C loader는 position 정보와 resolver 커스터마이징이 다르다.

### D2. 허용 문법은 YAML 1.2 core schema에서 다음을 뺀 부분집합이다

거부(diagnostic `syntax` 또는 `structural`, 위치 포함):

- 다중 document, directive, custom tag(`!`, `!!python/...`), anchor/alias(`&`, `*`), merge key(`<<`)
- duplicate key, 비문자열 key, 비어 있지 않은 complex key
- plain scalar timestamp: backend는 timestamp constructor를 문자열 반환으로 교체해 frontend와 같은
  string tree를 만든다. quoted/plain 어느 쪽이든 typed hydrate가 `date`로 변환한다.
- 숫자 안의 `_`: ruamel(1000)과 `yaml`("1_000")이 다르게 읽으므로 plain scalar 토큰이
  `^[+-]?[0-9][0-9_]*[0-9]$`이면서 `_`를 포함하면 `ambiguous_number_underscore`로 거부한다.
- 정책 거부 reason code: `syntax`, `directive`, `anchor_or_alias`, `tag`, `ambiguous_number_underscore`,
  `duplicate_key`, `multiple_documents`, `non_string_key`, `non_finite_number`, `not_a_mapping`. P1-02
  diagnostic `code`는 이 reason을 `yaml.<reason>`으로 노출한다.
- `.nan`, `.inf`, `-.inf` 와 non-finite float
- 크기 제한: 512 KiB bytes, depth 32, node 20,000 초과 시 거부 (P1-02에서 값 확정)

### D3. Cross-runtime fixture는 양쪽 테스트가 같은 파일을 읽는다

- `backend/tests/fixtures/strategy_documents/yaml12/manifest.json`이 case 목록의 SoT다. 각 case는
  `accepted/*.yaml` + 기대 JSON 또는 `rejected/*.yaml` + 기대 reason code를 가진다 (12 accepted, 16
  rejected). manifest에 없는 fixture 파일은 backend 테스트가 실패시킨다.
- backend: `backend/tests/contract/test_yaml12_cross_runtime.py`가 ruamel 기반 임시 loader로 manifest를
  검증한다. P1-02 codec이 이 loader를 대체한다.
- frontend: `frontend/src/shared/lib/yaml12/__tests__/cross-runtime.test.ts`가 같은 manifest를 `yaml`
  2.9.0으로 검증한다. `yaml`은 이 PR에서 frontend dependency로 추가된다 (P3-01이 CST/source map에 그대로
  사용). fixture는 backend 디렉터리 한 곳에만 둔다.

### D4. Typed hydrate 이후 canonical bytes가 같아야 한다

accepted fixture마다 backend codec → typed `StrategySpec` → `canonical_strategy_json` 결과가 기대
JSON에서 만든 결과와 byte 단위로 같아야 한다. `-0.0` 정규화는 P1-01 canonicalizer가 맡는다.

## 4. 대안

- **PyYAML 유지**: `yes`→bool, `1e-2`→str, duplicate key 무음 허용. 1.2 core와 맞추려면 resolver를
  전부 교체해야 하고 position은 Mark로만 제한적으로 나온다. 기각.
- **strictyaml**: 타입 추론을 아예 없애지만 schema를 별도로 선언해야 해 StrategySpec과 owner가
  둘이 된다. 기각.
- **frontend parse 결과를 backend로 전송**: source가 아니라 파생 트리를 보내므로 exact source 보존과
  server-side compile 원칙(ADR P0-01 D1)에 어긋난다. 기각.

## 5. Rollback

ruamel.yaml 의존성과 codec 모듈을 제거하면 JSON 경로만 남는다. YAML source가 저장된 revision은
`source`가 남아 있어도 JSON canonical로 조회할 수 있다.
