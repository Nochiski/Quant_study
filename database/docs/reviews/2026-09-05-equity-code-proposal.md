# `src/equity/` 구현 골격 설계 (제안, 2026-09-05)

> 대상: `workspace/dongmin/src/equity/` 신규 패키지 + `backend/src/backtest_engine/adapters/` 어댑터 1개.
> 근거는 전부 저장소 실물이다. 인용한 함수·클래스·필드는 읽고 확인한 것만 적었고, **확인하지 못한 것은 §8 에 미확인으로 분리**했다. 추측으로 채운 칸은 없다.
> 읽은 파일: `src/stage/{model,build,gates,manifest,baseline,snapshot,rules,rules_krx,rules_dart,__main__}.py`, `tests/{conftest,test_stage_builder_opts}.py`, `src/stage/fixtures/stg_price_daily.json`, `origin/stage/doc-p1` 의 `doc_prepass.py`·빌더 diff 4건, `docs/{EQUITY_DESIGN,EQUITY_WORKFLOW,EQUITY_KICKOFF,STAGE_HANDOFF}.md`, `backend/src/backtest_engine/{ports/*,adapters/krx_parquet.py,types/*}.py`, `backend/tests/test_bar_source_contract.py`, `backend/src/strategy_workbench/bootstrap/_container.py`, `.claude/rules/*.md`, `backend/pyproject.toml`, `scripts/run_stage*.sh`.

---

## 0. 재사용 판정의 사실 근거 (먼저 확정)

| 사실 | 근거 (파일:심볼) |
|---|---|
| `BuildRecord` 에 `inputs: dict[str,str]` 이 이미 있다 (기본 `{}`) | `stage/manifest.py:BuildRecord.inputs` — 주석 "equity 층 전용: 입력 stage 테이블 → 고정한 build_id" |
| `snapshot_id` 는 `BuildRecord` 의 **필수 위치 필드**다 | `stage/manifest.py:BuildRecord.snapshot_id` (기본값 없음) → equity 는 `""` 를 명시 전달 (WORKFLOW §1) |
| `manifest.commit()` 은 `table_root` 하위의 `v=<id>` 만 `shutil.rmtree` 한다 | `stage/manifest.py:commit()` 마지막 루프 — `_pinned/` 를 다른 루트에 두면 GC 대상 밖 |
| `_write_atomic` 은 private, `baseline.write()` 는 public 원자 교체다 | `manifest.py:_write_atomic` / `baseline.py:write()` |
| stage 게이트 결과 타입은 원장 의존이 없다 | `gates.py:GateStatus`(Enum)·`GateResult`(frozen dataclass, `as_dict()`) — import 는 `duckdb` 와 `.model` 뿐 |
| `GateContext` 는 원장 축에 묶여 있다 | `gates.py:GateContext` 필드 `src_view`(원장 UNION 뷰)·`n_dedup`·`cross_alias`·`parse_metrics`·`rule: TableRule` |
| `TableRule` 은 "원장 VARCHAR → stage 타입" 캐스팅 축이다 | `model.py:ColumnRule(src, kind, precision, scale, sign, unit_scale, zero_is_missing)`, `TableRule.partition_expr` 이 **원장 컬럼 실명** 기준(`substr(BAS_DD,1,4)`) |
| stage 빌더 본체는 원장 ATTACH·fanout 전제다 | `build.py:_attach()`(`ATTACH … (TYPE sqlite, READ_ONLY)`), `_union_sql()`, `_stage_sql()`(payload_hash·`rn`·`observed_n` 접기), `gates.py:g1_row_equation` = `n_src * fanout - n_dedup - n_reject` |
| `content_hash` 산출식 | `build.py:_content_hash()` = `f"{count(*)}:{hex(bit_xor(hash(CAST(t AS VARCHAR))))}"`, `read_parquet(glob, hive_partitioning=true)` |
| stage 는 해시를 **tmp 경로**에서 뜬다 (`v=` 하이브 컬럼이 안 붙는다) | `build.py` `glob = str(tmp_table / "year=*" / "*.parquet")`, `tmp_table = stage_root/_tmp/<bid>/<table>` |
| 파티션 레코드 형태 | `build.py` `partitions.append({"path": f"v={bid}" + (f"/{label}" if label else ""), "n_rows": n})` — date_axis 는 `v=b/year=2010`, whole 은 `v=b` |
| stage 픽스처 JSON 실형식 | `src/stage/fixtures/stg_price_daily.json` = `{key, column, expect, measured_sql, measured_at, note}`. `g4_fixtures` 가 **읽는 키는 `key`·`column`·`expect` 뿐** |
| 픽스처는 코드와 함께 산다 | `stage/__main__.py` `fx = Path(__file__).parent / "fixtures" / f"{a.table}.json"` + 주석 |
| `stg_disclosure.report_nm` 은 이미 정규화되어 저장된다 | `rules_dart.py:464` `ColumnRule("report_nm","report_nm",KIND_TEXT, normalize_text=True)` → equity 는 **대괄호 접두어 제거만** 하면 된다 |
| 1단계 입력 stage 컬럼 실명 (RULES 덤프로 확인) | `stg_listing_daily`(date·ticker·isin·name·list_date·market·secugrp·sect_tp·stkcert_tp·par_value_krw·list_shrs + extra `par_value_kind`) · `stg_index_daily`(index_class·index_name·date·**`close_idx`**…) · `stg_corp_map`(corp_code·ticker·corp_name_current) · `stg_company`(acc_mt·`induty_code_current`) · `stg_delisted_master`(ticker·`lstg_abol_dt`…) · `stg_master_daily`(extra `is_admin_issue`·`is_trade_halt`·`is_liquidation`, coverage_from 2026-09-01) · `stg_disclosure`(rcept_no·rcept_dt·ticker·report_nm + extra `is_correction`) |
| `origin/stage/doc-p1` 의 빌더 확장 방식 | `model.py:FileSource` 신설 + `build.py:_load_file_source()` 분기 + `gates.py:g0_declaration` 분기 + `rules.py:_MODULES` 등록 — **네 군데만 손대는 "새 소스 종류" 확장 패턴** |
| backend 의존성에 **duckdb 가 없다** | `backend/pyproject.toml` `dependencies = [fastapi, numpy, ruamel-yaml, uvicorn]`, optional `parquet = ["pyarrow>=15"]` |
| 워크벤치는 이미 `equity_duckdb` 이름을 예약해 뒀다 | `.claude/rules/backend-package-boundary.md` "새 `adapters/outbound/equity_duckdb` 가 port 에 맞춘다" + `bootstrap/_container.py:59` 가 `equity_adapter != "mock"` 을 거절 |

**판정 요약**

| stage 모듈 | 판정 | 이유 |
|---|---|---|
| `manifest.py` | **그대로 import** (`BuildRecord`·`Manifest`·`load`·`commit`) | 파일 규약만. `inputs` 필드가 이미 있다. 단 `_pinned/` 에는 `commit()` 을 **호출하지 않는다**(keep GC 가 하드링크 디렉토리를 지운다) → `write_pinned()` public 함수 1개 추가 제안(§8-4) |
| `baseline.py` | **함수 1개만 import** (`write`) | `Metric`·`measure()` 는 `ATTACH … TYPE sqlite` 전제(`baseline.py:measure` 루프). equity 는 parquet 위에서 측정 → `equity/baseline.py` 신규, JSON 스키마·원자 교체는 동일 |
| `gates.py` | `GateStatus`·`GateResult` **그대로 import**, 나머지 **복제 후 재정의** | `GateContext`·`g0~g9` 는 원장·fanout 축 |
| `build.py` | **복제 후 수정** — 살릴 것은 `_content_hash`·`_write_json`·`_count`·`_q`·골격 흐름 | `_attach`·`_union_sql`·`_stage_sql`·`_load_norm_maps`·`_available_sql` 전량 폐기 |
| `model.py` | **복제 후 재정의** | 캐스팅 축(`ColumnRule.src/kind/precision`)이 equity 엔 없다. `partition_expr` 이 원장 컬럼 기준 |
| `snapshot.py` | **사용 안 함** | VACUUM INTO 는 SQLite 전용. equity 의 입력 동결은 `_pinned/` 하드링크가 대체(WORKFLOW §1 명시) |
| `parsers.py`·`doc_prepass.py` | 사용 안 함 | blob/ZIP 파서 |
| `rules.py` | **패턴만 계승** | `_MODULES` → `RULES: dict[str, TableRule]` 레지스트리 한 줄 구조를 그대로 |
| `src/build_bridge.py`·`finalize.py` | **개념만** | 원장 SQLite 직접 읽기 + `prefix5` 브리지. equity 는 `isin8`(WORKFLOW §3-1 이 이미 "재사용 불가" 로 판정) |

---

## 1. 모듈 배치

### 1-1. 파일 트리

```
workspace/dongmin/src/equity/
  __init__.py            # 한 줄 docstring (stage/__init__.py 규약)
  model.py               # EquityTable · InputRef · EquityColumn · Equation · Invariant
                         #   · RangeRule · EquityAvailable · RULES_VERSION
  rules.py               # 레지스트리 — _MODULES → RULES: dict[str, EquityTable]
  rules_master.py        # 1단계 8테이블
  rules_price.py         # 2단계 (다음 슬라이스)
  rules_grid.py          # 3단계
  rules_fin.py           # 4단계
  rules_dart_pit.py      # 4-B
  rules_consensus.py     # 5단계
  rules_profile.py       # 6단계 (dataset_profile · universe_policy 는 rules_master)
  inputs.py              # MANIFEST 해석 · _pinned/ 하드링크 · stage 뷰 생성 · EG0 원재료
  build.py               # SQL 실행 → 파티션 COPY → _reject → _meta → 게이트 → MANIFEST 교체
  gates.py               # EG0~EG9 (+ EG-C 중 SQL 로 판정 가능한 항목)
  catalog.py             # equity.duckdb 매크로 생성 (임시 파일 → os.replace)
  baseline.py            # data/equity/baseline.json 측정·기록
  __main__.py            # CLI: pin · build · gate · catalog · baseline
  sql/
    corp.sql  security.sql  security_span.sql  corp_ticker.sql
    trading_calendar.sql  index_daily.sql  universe_daily.sql  universe_policy.sql
  fixtures/
    corp.json  security.json  security_span.json  corp_ticker.json
    trading_calendar.json  index_daily.json  universe_daily.json
workspace/dongmin/tests/
  test_equity_model.py  test_equity_inputs.py  test_equity_build.py  test_equity_gates.py
  test_equity_calendar.py  test_equity_corp.py  test_equity_security.py
  test_equity_span.py  test_equity_corp_ticker.py  test_equity_index.py
  test_equity_universe.py  test_equity_policy.py  test_equity_catalog.py
workspace/dongmin/scripts/
  equity_slice_from_stage.py   # 서버 stage → 로컬 소형 절단본 (읽기 전용)
  run_equity.sh                # 서버 러너 (flock · 재현성 2회 빌드)
backend/src/backtest_engine/adapters/
  equity_duckdb.py             # §6 — 이름·의존성은 승인 사항(§8-1)
```

### 1-2. `equity/model.py`

`EquityTable` 은 stage `TableRule` 의 **역할**(선언이 곧 게이트 입력)만 계승하고 필드는 전부 새로 짠다. "코드가 아니라 목록이다"(`rules_krx.py` docstring) 원칙 유지.

```python
"""equity 테이블 선언의 타입 (EQUITY_DESIGN v1.1 §2·§3·§4). 선언은 rules_<단계>.py.

stage 와 달리 입력이 이미 타입 있는 parquet 이므로 캐스팅 축(ColumnRule.kind/precision)이 없다.
대신 조인층의 축 셋이 들어간다: inputs(무엇을 읽는가) · equations(EG1 등식) · available(EG2).
stage 컬럼명은 `stage.rules.RULES[<table>].columns[].name` 이 정본이며 EG0 가 실물 대조한다.
"""
from __future__ import annotations

from dataclasses import dataclass

RULES_VERSION = "e1.0.0"          # BuildRecord.rules_version 에 실린다

BASIS_VOCAB = ("measured", "derived", "convention", "default", "unknown")   # DESIGN §1
PARTITION_CLASSES = ("date_axis", "receipt_axis", "whole")                  # DESIGN §2


@dataclass(frozen=True)
class InputRef:
    """읽는 stage 테이블 1개. columns 는 EG0 가 parquet 스키마와 대조하는 계약이다."""

    table: str                       # stage 테이블 실명 (stage.rules.RULES 키)
    columns: tuple[str, ...]         # stage ColumnRule.name / ExtraColumn.name
    role: str = "source"             # source | axis | signal — _meta 기록용 라벨


@dataclass(frozen=True)
class EquityColumn:
    name: str
    type: str                        # duckdb 타입 문자열: DATE · VARCHAR · BOOLEAN
                                     #   · DECIMAL(38,4) · INTEGER · STRUCT(kind VARCHAR, …)
    role: str = "fact"               # key | fact | available | basis | flag | derived
    note: str = ""                   # 근거 한 줄 (DESIGN §4 인용)


@dataclass(frozen=True)
class Equation:
    """EG1 — WORKFLOW §2-1 표 한 줄. 좌변은 equity 산출, 우변은 입력 위 스칼라."""

    key: str
    lhs_sql: str                     # `out` 뷰 위 단일 값
    rhs_sql: str                     # stage 입력 뷰 위 단일 값
    tolerance: int = 0               # 0 = 완전 일치


@dataclass(frozen=True)
class Invariant:
    """EG3 — 위반 행 술어. stage model.Invariant 와 같은 모양(0 이어야 통과)."""

    key: str
    violation_sql: str


@dataclass(frozen=True)
class RangeRule:
    """EG7 격리형 — 이 술어가 참인 셀은 SQL 이 NULL + miss_kind 로 격리하고 행은 유지한다."""

    key: str
    column: str
    violation_sql: str


@dataclass(frozen=True)
class EquityAvailable:
    """DESIGN §3 공통 골격 — 전 팩트 행 필수(EG2)."""

    kind: str                        # column | max_of | none
    basis: str                       # BASIS_VOCAB
    column: str | None = None        # kind=column
    parts: tuple[str, ...] = ()      # kind=max_of: 구성 행의 available 컬럼들
    content_date_column: str | None = None   # EG2 `available_date >= 내용일` 축


AVAILABLE_NONE = EquityAvailable("none", "unknown")


@dataclass(frozen=True)
class EquityTable:
    name: str
    grain: tuple[str, ...]                   # PK — EG3 유일성
    inputs: tuple[InputRef, ...]
    columns: tuple[EquityColumn, ...]
    partition_class: str
    partition_column: str | None             # equity 산출 컬럼 (date → year(date), rcept_no
                                             #   → substr(rcept_no,1,4)). whole = None
    available: EquityAvailable
    equations: tuple[Equation, ...] = ()     # 비어 있으면 EG1 FAIL (WORKFLOW §2 "착수 금지")
    invariants: tuple[Invariant, ...] = ()
    ranges: tuple[RangeRule, ...] = ()
    reject_reasons: tuple[str, ...] = ()     # _reject 로 나갈 사유 어휘 (pre_calendar 등)
    required_fixtures: tuple[str, ...] = ()  # EG4 — fixtures[].case 커버 강제
    depends_on: tuple[str, ...] = ()         # 선행 equity 테이블
    build_by_year: bool = False              # 연도 파티션 단위 루프
    consts: tuple[str, ...] = ()             # baseline.json 에서 끌어올 상수 키 (§2 _const)
    version_rule: str = "none"               # EG6 — none | first_write_wins | min_observed | …

    def column(self, name: str) -> EquityColumn:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"no such equity column: table={self.name} column={name}")

    @property
    def input_tables(self) -> tuple[str, ...]:
        return tuple(i.table for i in self.inputs)
```

`equity/rules.py` 는 stage 와 같은 한 줄 구조:

```python
_MODULES = (rules_master,)          # 단계가 늘 때마다 여기만 한 줄
RULES: dict[str, EquityTable] = {t.name: t for m in _MODULES for t in m.TABLES}
```

**선언 예 (`rules_master.py` 일부)** — DESIGN §4-1 을 그대로 옮긴 것:

```python
TRADING_CALENDAR = EquityTable(
    name="trading_calendar",
    grain=("date",),
    inputs=(
        InputRef("stg_index_daily", ("date",), role="axis"),
        InputRef("stg_flow_split_daily", ("date",), role="axis"),   # gap 축
        InputRef("stg_credit_daily", ("date",), role="axis"),
    ),
    columns=(
        EquityColumn("date", "DATE", "key"),
        EquityColumn("prev_td", "DATE"),
        EquityColumn("next_td", "DATE"),
        EquityColumn("calendar_source", "VARCHAR",
                     note="krx_index | gap_axis — DESIGN §4-1"),
    ),
    partition_class="whole", partition_column=None,
    available=AVAILABLE_NONE,          # 차원 테이블 — 팩트 아님
    equations=(Equation("rows",
                        "SELECT count(*) FROM out",
                        "SELECT (SELECT count(DISTINCT date) FROM stg_index_daily) + "
                        "(SELECT count(*) FROM gap_dates)"),),
    invariants=(Invariant("prev_next_chain",
                          "prev_td IS NOT NULL AND prev_td >= date"),
                Invariant("monotone", "next_td IS NOT NULL AND next_td <= date")),
    required_fixtures=("first_td", "holiday_gap", "gap_axis_head", "last_backfill_td"),
)
```

### 1-3. `equity/inputs.py` — MANIFEST 경유·`_pinned/`·뷰 생성

이 모듈이 **"맨 glob 금지"**(HANDOFF §1) 를 유일하게 지키는 지점이다.

```python
"""stage 입력 해석·고정 (EQUITY_WORKFLOW §1 · DESIGN §2 [결정 2]).

읽기 계약: `<stage_root>/<table>/MANIFEST.json` 의 current_build → `partitions[].path` 만 쓴다.
구버전 `v=…` 디렉토리가 keep=3 으로 공존하므로 디렉토리 glob 은 금지다(HANDOFF §1).
고정: 고른 build 의 `v=` 디렉토리를 `<equity_root>/_pinned/<table>/v=<build>/` 에 하드링크로
복제하고 BuildRecord 1건을 그 아래 MANIFEST.json 에 복사한다 — stage 의 keep=3 GC
(`manifest.commit()` 의 rmtree)가 입력을 지워도 빌드가 재현된다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

from stage import manifest
from .model import EquityTable, InputRef


@dataclass(frozen=True)
class PinnedInput:
    table: str
    build_id: str
    root: Path                        # <equity_root>/_pinned/<table>/v=<build>
    partition_paths: tuple[Path, ...]  # partitions[].path 를 절대경로로 푼 것
    meta: dict[str, object]           # 첫 파티션 _meta.json (gates·lag_known·coverage_from)

    @property
    def globs(self) -> tuple[str, ...]:
        return tuple(str(p / "*.parquet") for p in self.partition_paths)


def resolve(stage_root: Path, table: str) -> tuple[str, tuple[Path, ...]]:
    """(current_build, 파티션 디렉토리 절대경로들). 커밋된 빌드가 없으면 예외."""
    m = manifest.load(stage_root / table / "MANIFEST.json")
    if m.current_build is None:
        raise FileNotFoundError(
            f"stage table has no committed build — build it first: table={table} "
            f"stage_root={stage_root}")
    rec = next(b for b in m.builds if b.build_id == m.current_build)
    paths = tuple(stage_root / table / str(p["path"]) for p in rec.partitions)
    return m.current_build, paths


def pin(stage_root: Path, equity_root: Path, tables: tuple[str, ...]) -> dict[str, str]:
    """stage current_build 를 `_pinned/` 에 하드링크로 고정하고 {table: build_id} 를 낸다.

    이미 같은 build 가 고정돼 있으면 no-op. 다른 파일시스템이면 OSError(EXDEV) 를 그대로
    올린다 — 조용한 복사로 대체하지 않는다(DESIGN §10 P9 가 동일 디바이스를 실측했다).
    """
    pinned: dict[str, str] = {}
    for t in sorted(tables):
        build_id, parts = resolve(stage_root, t)
        dst_root = equity_root / "_pinned" / t
        dst = dst_root / f"v={build_id}"
        if not dst.exists():
            for src_dir in parts:
                rel = src_dir.relative_to(stage_root / t / f"v={build_id}")
                out = dst / rel
                out.mkdir(parents=True, exist_ok=True)
                for f in sorted(src_dir.iterdir()):
                    if f.is_file():
                        os.link(f, out / f.name)        # EXDEV = 다른 FS → 예외
        _copy_build_record(stage_root / t, dst_root, build_id)
        pinned[t] = build_id
    return pinned
```

- `_copy_build_record()` 는 `manifest.load()` 로 원본을 읽고 해당 `BuildRecord` 1건만 담은 `Manifest` 를 `_pinned/<t>/MANIFEST.json` 에 원자 기록한다. **`manifest.commit()` 을 쓰면 안 된다** — `commit()` 은 keep 밖 `v=` 를 rmtree 한다(`manifest.py:commit` 마지막 루프). 현재 `_write_atomic` 이 private 이므로 §8-4 의 public 함수 추가를 제안한다.
- `_reject` 디렉토리는 하드링크 대상에서 뺀다(입력이 아니다).

```python
def create_views(con: duckdb.DuckDBPyConnection, equity_root: Path,
                 rule: EquityTable, pinned: dict[str, str]) -> dict[str, PinnedInput]:
    """선언한 stage 테이블마다 선언한 컬럼만 투영한 TEMP VIEW 를 만든다.

    hive_partitioning=true 로 읽으면 파일에 없는 `year` 하이브 컬럼이 붙는다(HANDOFF §1).
    date_axis 입력에는 그 컬럼을 그대로 노출해 연도 프루닝 술어로 쓴다.
    """
    out: dict[str, PinnedInput] = {}
    for ref in rule.inputs:
        pi = load_pinned(equity_root, ref.table, pinned[ref.table])
        cols = ", ".join(_q(c) for c in ref.columns)
        globs = ", ".join(f"'{g}'" for g in pi.globs)
        has_year = any(p.name.startswith("year=") for p in pi.partition_paths)
        year_sel = ", year" if has_year else ""
        con.execute(f"CREATE OR REPLACE TEMP VIEW {_q(ref.table)} AS "
                    f"SELECT {cols}{year_sel} FROM read_parquet([{globs}], "
                    f"hive_partitioning=true)")
        out[ref.table] = pi
    return out


def declared_columns_missing(con, rule, views) -> list[str]:
    """EG0 — 선언 컬럼이 parquet 스키마에 없으면 그 목록. stage 컬럼명 기준."""
```

`input_gates_ok()` 는 `PinnedInput.meta["gates"]` 에서 `status == "fail"` 을 센다. `build.py` 가 **모든 파티션 `_meta.json` 에 같은 `gate_dicts` 를 쓰므로**(build.py 의 `for label, n, pdir in part_dirs:` 루프) 첫 파티션 하나만 읽어도 충분하다.

### 1-4. `equity/build.py`

stage `build_table()` 의 **흐름은 그대로**, 원장 축은 전부 들어낸다.

```python
def build_table(rule: EquityTable, equity_root: Path, stage_root: Path,
                pinned: dict[str, str], build_id: str | None = None,
                fixtures_path: Path | None = None, baseline_path: Path | None = None,
                gate_thresholds: dict[str, float] | None = None,
                memory_limit: str = "6GB", threads: int = 3,
                years: tuple[int, int] | None = None) -> BuildResult:
```

| stage `build_table` 단계 | equity 대응 |
|---|---|
| `_attach(snapshot)` | **삭제** → `inputs.create_views(pinned)` |
| `_available_sql` | **삭제** → `available_date`/`available_basis` 는 `.sql` 안에서 선언대로 계산, EG2 가 검사 |
| `_load_norm_maps` | **삭제** — `report_nm` 은 stage 가 이미 정규화(`rules_dart.py:464`), equity 는 접두어 제거만 (`regexp_replace(report_nm, '^\[[^\]]*\]', '')`) |
| `_stage_sql` (캐스팅·miss_kind·payload_hash·dedup) | **삭제** → `sql/<table>.sql` 1개 |
| `stage_ok` / `stage_rej` 뷰 | `out` / `out_rej` 뷰 — `.sql` 이 `reject_reason` 컬럼을 내면 그 값으로 갈린다 |
| `COPY … PARTITION_BY (year)` | 동일. equity 는 `year` 를 `year(date)` 또는 `substr(rcept_no,1,4)` 로 SELECT 절에서 만든다 |
| `_content_hash` | **그대로 복제**. 단 파티션마다 1회 + 테이블 전체 1회 (§2 참조) |
| `gates.run_all` | `equity.gates.run_all` |
| `_write_json(pdir/"_meta.json", meta)` | 필드 재정의 (§2) |
| `manifest.commit(...)` | **그대로**, `inputs=pinned`·`snapshot_id=""` 추가 |

`BuildResult` 는 stage 와 같은 모양을 쓰되 stage 전용 계상(`n_src`·`n_dedup`·`fanout`)을 뺀다:

```python
@dataclass(frozen=True)
class BuildResult:
    status: BuildStatus                 # OK | GATE_FAILED  (stage 와 같은 Enum 이름)
    table: str
    build_id: str
    inputs: dict[str, str]
    n_rows: int
    n_reject: int
    content_hash: str
    partitions: list[dict[str, object]]   # {path, n_rows, content_hash}
    gates: list[GateResult]
    elapsed_s: float
    peak_rss_mb: float | None
    out_dir: Path | None
    failed_report: Path | None

    @property
    def ok(self) -> bool:
        return self.status is BuildStatus.OK
```

### 1-5. `equity/catalog.py`

```python
"""equity.duckdb — 데이터 없이 테이블 매크로만. DESIGN §2 [결정 1] · §10 P1a~d.

P1c 실측: CREATE 시 상대경로는 `No files found` → 절대경로 필수.
P1b: 파일 DB 를 read_only=True 로 재오픈해 매크로 호출 OK.
P1d: 기본값 인자 `name := expr` + 본문 스칼라 서브쿼리 호출 OK.
쓰기는 임시 파일에 만든 뒤 os.replace — 이미 파일을 연 read_only 리더는 옛 inode 를 계속 본다.
"""

def macro_sql(table_globs: dict[str, tuple[str, ...]]) -> list[str]:
    """테이블별 절대경로 glob → CREATE OR REPLACE MACRO DDL 목록. 순수 함수 = 테스트 대상."""

def rebuild(equity_root: Path, tables: tuple[str, ...]) -> Path:
    """MANIFEST.current_build 로 경로를 확정해 equity.duckdb 를 통째로 다시 만든다."""
    tmp = equity_root / f".equity.duckdb.{os.getpid()}.tmp"
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        for ddl in macro_sql(_globs(equity_root, tables)):
            con.execute(ddl)
    finally:
        con.close()
    dst = equity_root / "equity.duckdb"
    os.replace(tmp, dst)            # 파일 원자 교체 — 유일한 전환 지점(manifest.py 규약과 동일)
    return dst
```

**`rebuild()` 는 빌드 커밋 뒤에 반드시 다시 불러야 한다** — 이유는 §8-6 (keep=3 GC 가 카탈로그의 절대경로를 죽인다).

### 1-6. `equity/baseline.py`

```python
"""data/equity/baseline.json — 게이트 상수·회귀 고정값 (DESIGN §2 · WORKFLOW §2).

형식은 stage 와 같다: {table: {metric: value, thresholds: {...}}} + _measured[]
(항목마다 SQL 을 실어 재현 가능하게). 원자 교체는 stage.baseline.write 를 그대로 쓴다.
차이: 측정면이 원장 SQLite 가 아니라 `_pinned/` stage parquet + 커밋된 equity 산출이다.
"""
from stage.baseline import write        # 원자 교체 규약 재사용 (SoT 중복 금지)

@dataclass(frozen=True)
class EquityMetric:
    table: str
    metric: str
    scope: str        # stage | equity — 어느 면 위에서 재는가
    sql: str
    growing: bool = False
```

1단계 초기 항목(값은 서버 실측 후 채움): `security.delist_conflict_rows`, `security_span.respan_count`(=2, P8), `corp_ticker.map_rate`, `trading_calendar.gap_trading_days`, `universe_daily.no_trade_run_k`, `universe_daily.liquidation_recall`, `security.backfill_end`(=2026-08-20), 그리고 `thresholds: {"EG7": 0.001}`.

### 1-7. `equity/__main__.py`

stage 는 `--table` 단일이지만 equity 는 동사가 5개라 서브커맨드로 간다.

```
PYTHONPATH=src python -m equity pin    --tables stg_listing_daily,stg_index_daily,…
                                       [--out data/equity/_pinned/inputs.json]
PYTHONPATH=src python -m equity build  <table> [--inputs data/equity/_pinned/inputs.json]
                                       [--years 2010:2015] [--memory-limit 6GB] [--threads 3]
                                       [--build-id ID] [--eg7 0.001]
PYTHONPATH=src python -m equity gate   <table> [--build ID]      # 재판정만, 폐기 없음
PYTHONPATH=src python -m equity catalog [--tables …]
PYTHONPATH=src python -m equity baseline --out data/equity/baseline.json
```

- `QL_HOME` 기본값 규약은 stage `__main__.py`·`baseline.py` 와 동일: `Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])`.
- 출력 한 줄 형식도 stage 와 맞춘다(`run_stage_all.sh` 가 `^(ok|gate_failed)` 와 `rows=`·`reject=`·`\d+s$` 를 파싱하므로 러너 스크립트를 그대로 본뜰 수 있다).
- `--table` 이 아니라 위치 인자로 두면 `choices=sorted(rules.RULES)` 로 오타를 CLI 에서 막는다(stage 와 동일).

---

## 2. SQL 빌드 방식

### 2-1. `.sql` 파일 채택 (문자열 템플릿 아님)

- **선택**: 테이블당 `src/equity/sql/<table>.sql` 1개.
- **대안**: stage 처럼 파이썬 문자열 조립(`build._stage_sql`).
- **이유**: stage 가 문자열을 조립한 이유는 컬럼 선언에서 캐스팅식·`miss_kind` CASE·`payload_hash` 를 **생성**해야 했기 때문이다(`build.py:_stage_sql` 은 `rule.columns` 루프로 SELECT 절을 만든다). equity 는 테이블마다 사람이 쓴 조인·윈도 SQL 이고 생성 요소가 사실상 없다. 100컬럼 룰 아래 300줄짜리 윈도 함수를 파이썬 문자열로 두면 diff 가 읽히지 않고, `.sql` 이면 서버에서 duckdb CLI 로 그대로 손검증할 수 있다(WORKFLOW §1 "읽기 전용 스크립트 scp"). 파일 위치 규약은 stage 픽스처와 같다 — `Path(__file__).parent / "sql" / f"{name}.sql"`.

### 2-2. 상수는 `_const` 임시 테이블로만 들어간다

WORKFLOW §1 금지: "임계 상수 본문·코드 하드코딩(전부 `baseline.json`·`universe_policy`)".

```python
# build.py
def _make_consts(con, rule: EquityTable, baseline: dict[str, object]) -> None:
    """rule.consts 가 요구한 키를 baseline.json 에서 꺼내 1행 wide 임시 테이블로 올린다."""
    missing = [k for k in rule.consts if k not in baseline]
    if missing:
        raise KeyError(f"baseline constant not found — table={rule.name} keys={missing} "
                       f"(등재는 사람 승인: WORKFLOW §2 첫 빌드 skip(no_baseline) 절차)")
    sel = ", ".join(f"{_lit(baseline[k])} AS {_q(k)}" for k in rule.consts)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _const AS SELECT {sel or 'NULL AS _none'}")
```

모든 `.sql` 은 `CROSS JOIN _const c` 로만 상수를 본다. 그러면 **"`.sql` 본문에 숫자 리터럴 금지"** 를 기계적으로 검사할 수 있다:

```python
# tests/test_equity_build.py
def test_sql_파일에_상수_하드코딩이_없다() -> None:
    allowed = {"0", "1", "2", "-1"}          # 인덱스·부호·span_seq 초기값만
    for p in (SQL_DIR).glob("*.sql"):
        nums = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?", _strip_comments(p.read_text())))
        assert nums <= allowed, f"{p.name}: 하드코딩 상수 {sorted(nums - allowed)}"
```

(named parameter `$x` 를 `CREATE TABLE … AS SELECT` 에 쓸 수 있는지는 로컬에 duckdb 가 없어 확인하지 못했다 — §8-8. `_const` 방식은 순수 SQL 이라 확인 없이도 성립하므로 이걸 기본으로 둔다.)

### 2-3. 파티션 프루닝과 연도 루프

- stage 산출을 `hive_partitioning=true` 로 읽으면 `year` 컬럼이 붙는다(HANDOFF §1). `inputs.create_views` 가 date_axis 입력에 그 컬럼을 노출하므로 `.sql` 은 `WHERE l.year BETWEEN c.year_lo AND c.year_hi` 한 줄로 파일 단위 프루닝을 받는다.
- `rule.build_by_year=True` 인 테이블(`universe_daily`, 이후 `fin_std`·격자)은 연도마다:
  1. `_const` 의 `year_lo`/`year_hi` 를 그 연도로 바꿔 `CREATE OR REPLACE TEMP TABLE _const`
  2. `.sql` 실행 → 그 연도분만 `COPY … TO '<tmp>/<table>/year=YYYY/part.parquet'`
  3. `DROP TABLE out` 로 메모리 회수
  → `PARTITION_BY` COPY 를 한 번에 하지 않으므로 duckdb 가 전 구간을 한 번에 물지 않는다. WORKFLOW §1 이 요구한 "연도 파티션 단위로 첫 슬라이스 실빌드 → RSS·스필·초 기록 후 전 구간" 을 CLI `--years 2010:2010` 로 그대로 수행한다.
- 세션 설정은 stage 기본값과 같은 값을 명시적으로 건다:
  `SET memory_limit='6GB'` · `SET threads=3` · `SET temp_directory='<equity_root>/_tmp/spill'`
  (stage `build.py` 의 `spill = stage_root/"_tmp"/"spill"` 과 같은 자리, DESIGN §2 는 `data/equity/_tmp/spill`, 여유 282GB — P9).

### 2-4. `_reject/<reason>/`

- `.sql` 은 마지막 SELECT 에 `reject_reason` 컬럼(NULL = 채택)을 낸다.
- `out` = `reject_reason IS NULL`, `out_rej` = 그 반대.
- 기록:
  ```sql
  COPY (SELECT * FROM out_rej) TO '<tmp>/<table>/_reject'
       (FORMAT PARQUET, PARTITION_BY (reject_reason), OVERWRITE_OR_IGNORE,
        FILENAME_PATTERN 'part');
  ```
  → 실제 경로는 `_reject/reject_reason=pre_calendar/part_0.parquet` 이 된다. DESIGN §2 표기(`_reject/<reason>/`)와 한 글자 다르지만 하이브 표기라 **읽을 때 사유가 컬럼으로 복원된다**. 승인 사항 §8-2.
- 사유 어휘는 `rule.reject_reasons` 에 선언하고, 게이트가 선언 밖 사유가 나오면 FAIL(어휘 폐쇄).
- 1단계 사유: `off_grid`(유니버스 밖 티커), `pre_calendar`(캘린더 하한 이전 — 3단계에서 본격 사용), `unmapped_ticker`.

### 2-5. `content_hash` — stage 와 같은 규약, 함정 2개

식은 `build.py:_content_hash` 를 **글자 그대로** 복제한다:
`f"{count(*)}:{format(bit_xor(hash(CAST(t AS VARCHAR))), 'x')}"`.

1. **반드시 tmp 경로에서 뜬다.** `hive_partitioning=true` 는 경로에서 `v=<build_id>` 도 컬럼으로 뽑는다. 최종 경로(`<table>/v=<bid>/…`)에서 해시를 뜨면 build_id 가 해시에 섞여 **재현성 EG5a 가 영원히 실패**한다. stage 는 `tmp_table` 아래(`_tmp/<bid>/<table>/year=*/`)에서 떠서 이 문제를 피하고 있다 — 같은 자리를 지킨다.
2. `CAST(t AS VARCHAR)` 는 행 struct 문자열화라 **컬럼 순서에 의존**한다. `.sql` 의 SELECT 순서가 바뀌면 해시가 바뀐다. 이건 결함이 아니라 스키마 드리프트 검출기로 삼고, `EquityTable.columns` 순서 = SELECT 순서를 EG0 에서 강제한다(`DESCRIBE out` 결과와 선언 순서 대조).

`_meta.json`(파티션당) 필드 — stage 필드 중 원장 축을 빼고 equity 축을 넣는다:

```json
{"table": "...", "build_id": "...", "partition": "year=2018" | "whole",
 "n_rows": 0, "n_reject": 0, "reject_by_reason": {"off_grid": 0},
 "inputs": {"stg_listing_daily": "b_..."},
 "rules_version": "e1.0.0", "content_hash": "N:hex",
 "partition_content_hash": "N:hex",
 "lag_known_inputs": {"stg_listing_daily": false},
 "coverage_from": null, "coverage_to": "2026-08-20",
 "memory_limit": "6GB", "threads": 3, "elapsed_s": 0.0, "peak_rss_mb": null,
 "gates": [ … GateResult.as_dict() … ]}
```

`BuildRecord.partitions[]` 원소에 `content_hash` 를 넣는다(EG5a 가 파티션 단위 비교를 요구). `partitions: list[dict[str, object]]` 라 **스키마 변경 없이** 가능하다.

---

## 3. 게이트 실행 프레임

모양은 stage `gates.py` 와 최대한 같게 — 결과 타입은 아예 같은 것을 import 한다.

```python
"""게이트 EG0~EG9 (EQUITY_WORKFLOW v1.1 §2).

폐기형(FAIL = 버전 폐기): EG0·EG1·EG2·EG3·EG4·EG5·EG6·EG8·EG9. 행 격리형: EG7.
실행 조건이 안 되는 게이트는 SKIP(사유)으로 남기고 폐기하지 않는다 — stage 규약 그대로.
결과 타입은 stage.gates 의 것을 그대로 쓴다(원장 의존 없음).
"""
from stage.gates import GateResult, GateStatus

DEFAULT_THRESHOLDS: dict[str, float] = {
    "EG7": 0.001,     # 격리 비율 상한 — stage DEFAULT_THRESHOLDS["G7"] 초기값 계승(WORKFLOW §2)
}
SKIP_REASONS = ("no_baseline", "no_previous_build", "no_cross_source", "not_grid",
                "no_profile_table", "dimension_table")


@dataclass
class EquityGateContext:
    con: duckdb.DuckDBPyConnection
    rule: EquityTable
    out_view: str                              # 채택 행 (tmp parquet 기준)
    reject_view: str
    input_views: dict[str, PinnedInput]        # stage 테이블 → 고정 입력(build_id·_meta)
    n_out: int
    n_reject: int
    reject_by_reason: dict[str, int]
    inputs: dict[str, str]                     # BuildRecord.inputs 와 같은 dict
    partition_hashes: dict[str, str]           # 이번 빌드 (파티션 라벨 → content_hash)
    previous: manifest.BuildRecord | None      # 직전 빌드 (EG5a·EG5b)
    baseline: dict[str, object] | None         # data/equity/baseline.json[table]
    thresholds: dict[str, float]
    fixtures: list[dict[str, object]] | None
    catalog_path: Path | None                  # EG-C 뷰 호출용 equity.duckdb (없으면 skip)
    current_year: int
```

게이트 함수 시그니처는 stage 와 동일하게 `(ctx) -> GateResult` 하나.

```python
def eg0_inputs(ctx: EquityGateContext) -> GateResult: ...
def eg1_equations(ctx: EquityGateContext) -> GateResult: ...
def eg2_pit(ctx: EquityGateContext) -> GateResult: ...
def eg3_keys(ctx: EquityGateContext) -> GateResult: ...
def eg4_fixtures(ctx: EquityGateContext) -> GateResult: ...
def eg5_reproducibility(ctx: EquityGateContext) -> GateResult: ...
def eg6_version_choice(ctx: EquityGateContext) -> GateResult: ...
def eg7_range(ctx: EquityGateContext) -> GateResult: ...
def eg8_cross_source(ctx: EquityGateContext) -> GateResult: ...
def eg9_regime_coverage(ctx: EquityGateContext) -> GateResult: ...

def run_all(ctx: EquityGateContext) -> list[GateResult]:
    return [eg0_inputs(ctx), eg1_equations(ctx), eg2_pit(ctx), eg3_keys(ctx),
            eg4_fixtures(ctx), eg5_reproducibility(ctx), eg6_version_choice(ctx),
            eg7_range(ctx), eg8_cross_source(ctx), eg9_regime_coverage(ctx)]
```

결과 레코드는 stage 와 같은 `{"name","status","detail","metrics"}`(`GateResult.as_dict()`).

**stage 와 다르게 가는 두 곳** — 명시해 둔다:

| 게이트 | stage | equity | 근거 |
|---|---|---|---|
| EG1 | `g1_row_equation` 은 `n_src*fanout - n_dedup - n_reject` 단일식, 항상 실행 | `rule.equations` 가 **비어 있으면 FAIL** | WORKFLOW §2 "표에 등식이 없는 테이블은 착수 금지" |
| EG4 | `g4_fixtures` 는 픽스처 없으면 `SKIP("no_fixtures")` | 픽스처 없으면 **FAIL**, 그리고 `rule.required_fixtures` 중 빠진 `case` 가 있으면 FAIL | WORKFLOW §2 EG4 "강제". stage 의 `unit_scale` 무커버 FAIL 규칙이 이 자리에 대응 |

**픽스처 매칭 로직은 stage `g4_fixtures` 를 그대로 복제한다** — `key` dict → `CAST("k" AS VARCHAR) = 'v'` AND 결합, `expect` 는 JSON null = SQL NULL, 그 외 문자열 비교. 이 규약이 stage 픽스처 9개와 이미 호환된다.

**skip 규약**: `GateStatus.SKIP` + `detail` = `SKIP_REASONS` 의 한 단어. `metrics` 는 **skip 이어도 항상 채운다** — WORKFLOW §2 "첫 빌드에서 skip(no_baseline) 인 게이트는 측정치를 `_meta.gates[].metrics` 에 남기고, 사람이 승인해 baseline 에 넣은 뒤 2회차 빌드가 정식 통과". 이걸 코드로 강제하려면:

```python
def test_skip_게이트도_metrics를_남긴다() -> None:
    for g in run_all(ctx_without_baseline):
        if g.status is GateStatus.SKIP and g.detail == "no_baseline":
            assert g.metrics, f"{g.name}: skip(no_baseline) 인데 측정치가 없다"
```

**baseline 조회 헬퍼** — 코드에 숫자를 못 쓰게 만드는 지점:

```python
def _base(ctx: EquityGateContext, key: str) -> float:
    """baseline.json 의 상수. 없으면 SkipGate 를 올려 호출부가 skip(no_baseline) 로 처리한다."""
    if ctx.baseline is None or key not in ctx.baseline:
        raise SkipGate("no_baseline")
    return float(str(ctx.baseline[key]))
```

`SkipGate` 는 게이트 함수 안에서만 잡는 내부 예외다(도메인 실패를 값으로 나른다는 `python.md` 원칙과 충돌하지 않게, 경계는 게이트 함수 하나로 좁힌다).

**EG-C 의 분할** — WORKFLOW §2 는 EG-C 를 "폐기형(테스트)" 로 적었다. 실행 위치가 저장소 두 곳으로 갈린다:

| EG-C 항목 | 실행 위치 |
|---|---|
| ② `v_universe(:d,'all')` 티커 집합 = `stg_listing_daily`(d − lag) · ③ 재상장 2구간·`coverage_gap` 절단 · ⑦ asof 과거 · ⑧ 랙 변경 · ⑨ `obs_month` 재현 불가 | `equity/gates.py:egc_sql(ctx)` — SQL 로 판정, `catalog_path` 없으면 `skip` |
| ① `BUILDERS` 등록 후 계약 전체 통과 · ④ 분할 픽스처 누적수익률 · ⑤ 정지 종목 섞인 BarQuery · ⑩ 폐지 909 중 20종목 | `backend/tests/` (§6) — 별도 PR |
| ⑥ 샘플 팩터 4개 손계산 | 팩터층 (범위 밖) |

---

## 4. 테스트 규약

### 4-1. 손계산 픽스처 JSON

**형식** — stage 규약을 유지하고 `case` 와 `source` 두 키를 더한다. `g4_fixtures` 가 읽는 필수 키(`key`·`column`·`expect`)를 그대로 두므로 stage 픽스처 9개와 같은 검증기를 쓴다.

```json
[
 {"case": "respan_036220_second",
  "key": {"ticker": "036220", "span_seq": "2"},
  "column": "first_date", "expect": "2024-03-13",
  "source": "재상장 실측 2종 — EQUITY_DESIGN §10 P8",
  "measured_sql": "SELECT min(date) FROM stg_listing_daily WHERE ticker='036220' AND date>='2020-01-01'",
  "measured_at": "2026-09-05"},
 {"case": "etf_list_date_unknown",
  "key": {"ticker": "069500"}, "column": "list_date", "expect": null,
  "source": "ETF 는 listing 에 없다(P8: ETF 1,416 티커, listing 에 0) → NULL basis=unknown",
  "measured_sql": "SELECT count(*) FROM stg_listing_daily WHERE ticker='069500'",
  "measured_at": "2026-09-05"}
]
```

- `expect` 는 **문자열 비교**다(`g4_fixtures` 가 `CAST(col AS VARCHAR)` 로 읽는다). 날짜는 `"2024-03-13"`, 불린은 `"true"`, NULL 은 JSON `null`.
- **위치**: 정본 `src/equity/fixtures/<table>.json`("골든 픽스처는 코드와 함께 산다" — `stage/__main__.py` 주석), CLI 는 `fixtures_path or (equity_root / "fixtures" / f"{name}.json")` 로 서버 `data/equity/fixtures/`(WORKFLOW §2 표기)도 받는다. stage `build_table` 이 이미 같은 fallback 을 쓴다. 승인 사항 §8-3.
- **금지**: WORKFLOW §2 EG4 — 기대값의 출처가 look-ahead 테이블(`stg_v3_revision_compare`, `stg_consensus_matrix` lookback 컬럼)이면 무효. `source` 문자열에 그 테이블 이름이 들어가면 테스트가 실패하도록 lint 를 건다.

### 4-2. stage 절단본 생성 스크립트

`workspace/dongmin/scripts/equity_slice_from_stage.py` (서버 실행, 읽기 전용, 콜 0).

```
PYTHONPATH=src .venv/bin/python scripts/equity_slice_from_stage.py \
  --stage-root ~/quant-ledger/data/stage \
  --out /tmp/stage_slice \
  --tables stg_listing_daily,stg_etf_price_daily,stg_index_daily,stg_corp_map,\
stg_company,stg_delisted_master,stg_master_daily,stg_price_daily,stg_disclosure,\
stg_flow_split_daily,stg_credit_daily \
  --tickers 005930,036220,101970,003540,900050,900060,0001A0,207940,069500,004200 \
  --years 2015,2016,2024,2025,2026
```

동작:
1. 테이블마다 `inputs.resolve()` 로 `current_build` + `partitions[].path` (맨 glob 금지 — 같은 함수를 쓴다).
2. duckdb 로 `COPY (SELECT * FROM read_parquet([…]) WHERE <필터>) TO out/<table>/v=<build>/<part>/part0.parquet`.
   - 티커 컬럼이 있는 테이블은 `ticker IN (…)`, 없는 테이블(`stg_index_daily`·`stg_corp_map`·`stg_company`)은 연도/전량.
   - **파티션 디렉토리 이름과 `build_id` 를 원본 그대로 유지**한다 → 로컬에서 EG0(`inputs` 대조)이 성립한다.
3. 원본 `MANIFEST.json` 과 파티션 `_meta.json` 을 그대로 복사하고, `partitions[].n_rows` 만 절단 후 값으로 고쳐 쓴다(EG1 등식이 절단본 위에서도 성립하려면 필요).
4. `slice.json` 을 남긴다 — `backend/tests/fixtures/krx_parquet/manifest.json` 과 같은 형식(`sliced_at`, `tickers`: {코드: 왜 골랐는가}, `files`: {파일: 행수}, `source_manifest`: {build_id, 원본 행수}).
5. 로컬로 scp → `workspace/dongmin/tests/data/stage_slice/`.

**커밋 여부**: `backend/tests/fixtures/krx_parquet/*.parquet` 은 저장소에 커밋돼 있다(선례). 다만 종목 10 × 4,094 거래일이면 `stg_price_daily`·`stg_listing_daily` 만으로도 수 MB 다. 테스트는 절단본이 없으면 `pytest.skip("stage slice 없음 — scripts/equity_slice_from_stage.py")` 로 건너뛰게 만들고, 커밋 여부는 첫 절단 후 실제 크기를 재서 결정한다(작으면 커밋, 크면 서버 전용). `.claude/rules/testing.md` 는 "산출물 파일의 존재/내용을 단언하는 테스트" 를 금지하지 절단본 픽스처를 금지하지 않는다 — 절단본 위 테스트는 전부 **함수 동작**(SQL 결과) 검증이다.

### 4-3. 게이트 부정 픽스처 (결함 주입)

`tests/test_equity_gates.py` — 게이트마다 정/부 한 쌍. 모두 tmp 에 만든 소형 parquet 위에서 돈다(서버 데이터 불필요).

| 테스트 | 주입하는 결함 | 기대 |
|---|---|---|
| `test_eg0_고정_안된_입력은_fail` | `inputs` 의 build_id 를 `_pinned/` 에 없는 값으로 | `EG0 fail`, `metrics["unpinned"] == ["stg_x"]` |
| `test_eg0_stage_컬럼_오타는_fail` | `InputRef.columns` 에 `stck_prpr`(원장 실명) 선언 | `EG0 fail`, `missing_columns` |
| `test_eg0_입력_meta_gates_fail_이면_fail` | 입력 `_meta.json` 의 `gates[0].status="fail"` | `EG0 fail` |
| `test_eg1_등식_없으면_fail` | `equations=()` 인 테이블 | `EG1 fail` (skip 아님) |
| `test_eg1_행수_한개_모자라면_fail` | out 에서 1행 제거 | `EG1 fail`, `metrics` 에 lhs/rhs |
| `test_eg2_available_null인데_basis_measured면_fail` | `available_date=NULL, basis='measured'` 1행 | `EG2 fail` |
| `test_eg2_available이_내용일보다_이르면_fail` | `available_date < date` | `EG2 fail` |
| `test_eg2_basis_어휘_밖이면_fail` | `basis='guessed'` | `EG2 fail` |
| `test_eg3_pk_중복은_fail` | 같은 grain 2행 | `EG3 fail` |
| `test_eg3_span_중첩은_fail` | 겹치는 span 2행 | `EG3 fail` |
| `test_eg4_픽스처_없으면_fail` | 픽스처 파일 부재 | `EG4 fail` (stage 와 반대) |
| `test_eg4_required_case_누락은_fail` | `required_fixtures` 중 하나 삭제 | `EG4 fail` |
| `test_eg5a_같은_inputs면_해시_동일` | 같은 입력으로 2회 빌드 | 파티션 해시 전량 동일 |
| `test_eg5a_build_id가_해시에_안_섞인다` | build_id 를 바꿔 2회 빌드 | 해시 동일 (§2-5 함정 1의 회귀 테스트) |
| `test_eg7_격리비율_초과는_fail` | 범위 밖 셀을 임계 초과로 주입 | `EG7 fail`, 그 아래면 `pass` + `_reject` 기록 |
| `test_eg8_교차소스_없으면_skip` | — | `SKIP("no_cross_source")` |
| `test_eg9_격자_아니면_skip` | — | `SKIP("not_grid")` |

### 4-4. 뷰 매크로 테스트 (duckdb in-memory)

`tests/test_equity_catalog.py`:

```python
def test_macro_sql_은_절대경로만_만든다() -> None:      # P1c 회귀
    for ddl in catalog.macro_sql({"universe_daily": ("data/equity/…/*.parquet",)}):
        ...   # 상대경로 입력이면 ValueError

def test_in_memory_매크로_바인딩() -> None:              # P1a
    con = duckdb.connect()
    for ddl in catalog.macro_sql(_globs(tmp)):
        con.execute(ddl)
    assert con.execute("SELECT count(*) FROM v_universe(DATE '2024-03-13')").fetchone()[0] == 3

def test_파일DB_read_only_재오픈_호출() -> None:          # P1b
    path = catalog.rebuild(tmp, ("universe_daily",))
    ro = duckdb.connect(str(path), read_only=True)
    assert ro.execute("SELECT count(*) FROM v_universe(DATE '2024-03-13')").fetchone()[0] == 3

def test_기본값_인자와_본문_서브쿼리() -> None:            # P1d
    # v_universe(d, policy := 'all', lag_override := NULL) 를 인자 1개로 호출
    ...

def test_os_replace_교체_후에도_옛_리더는_살아있다() -> None:
    ro = duckdb.connect(str(catalog.rebuild(tmp, T)), read_only=True)
    catalog.rebuild(tmp, T)                     # 새 파일로 교체
    assert ro.execute("SELECT 1").fetchone() == (1,)   # 옛 inode 로 계속 동작
```

### 4-5. 테스트 이름 규약

stage 는 영문 스네이크(`test_zero_marker_matches_decimal_and_all_zero_date_literals`)를 쓰지만 과제문이 한글 이름(`test_prev_next_td_손계산`)을 예시했다. 둘 다 pytest 에서 유효하다. **한 파일 안에서 섞지 않는다**는 조건으로 equity 는 한글 서술을 채택한다(손계산 케이스가 무엇을 재는지가 이름에 들어가야 리뷰가 된다). 승인 사항 §8-7.

---

## 5. 1단계 구현 작업 목록

의존 순서: **T0 인프라 → T1 캘린더 → T2 corp → T3 security → T4 span → T5 corp_ticker → T6 index → T7 universe_daily → T8 policy → T9 카탈로그 → T10 어댑터 → T11 서버 실측**.
T2·T6 은 T1 뒤 병렬 가능. T3 은 T1 필요(span 계산에 캘린더), T4 는 T3, T5 는 T3, T7 은 T4·T5·T6 전부.

각 작업의 **완료 조건**은 (a) 지정한 테스트가 통과 (b) 변경 `.py` 에 `ruff check` + `pyright` 통과 (c) 절단본 위에서 해당 테이블 게이트가 `fail 0`.

### T0 — 인프라 (코드 6파일, 테스트 5파일)

| # | 파일·함수 | 테스트 | 완료 조건 |
|---|---|---|---|
| T0.1 | `equity/model.py` 전량 | `test_equity_model.py::test_grain_컬럼이_columns에_있다`<br>`::test_partition_class_어휘_폐쇄`<br>`::test_available_basis_어휘_폐쇄`<br>`::test_partition_column은_whole일때만_None` | RULES 없이도 dataclass 단위 통과 |
| T0.2 | `equity/inputs.py::resolve`·`load_pinned` | `test_equity_inputs.py::test_MANIFEST_partitions만_읽는다`<br>`::test_구버전_v디렉토리를_무시한다`<br>`::test_커밋된_빌드_없으면_예외` | 가짜 stage 트리(tmp) 위에서 통과 |
| T0.3 | `equity/inputs.py::pin`·`_copy_build_record` | `::test_pin은_하드링크다`(`os.stat().st_nlink == 2`)<br>`::test_pin_후_원본_v디렉토리_삭제해도_읽힌다`<br>`::test_다른_파일시스템이면_OSError`(monkeypatch `os.link` → EXDEV)<br>`::test_같은_build_재pin은_noop` | stage `manifest.commit()` 로 GC 를 실제로 돌려 생존 확인 |
| T0.4 | `equity/inputs.py::create_views`·`declared_columns_missing` | `::test_선언_컬럼만_투영한다`<br>`::test_year_하이브_컬럼이_붙는다`<br>`::test_원장_실명_선언은_missing으로_잡힌다` | |
| T0.5 | `equity/build.py::build_table` 골격 + `_content_hash`·`_write_json`·`_q` | `test_equity_build.py::test_whole은_part0_parquet`<br>`::test_date_axis는_year디렉토리`<br>`::test_content_hash가_build_id에_불변`<br>`::test_게이트_실패시_tmp폐기_MANIFEST불변`<br>`::test_reject는_사유별_디렉토리`<br>`::test_sql파일에_상수_하드코딩_없음` | 더미 `.sql`(`SELECT 1 AS k, NULL AS reject_reason`) 로 골격만 |
| T0.6 | `equity/gates.py` 전량 | `test_equity_gates.py` §4-3 표 전 항목 | 게이트 10개 × (정 1 + 부 1) |
| T0.7 | `equity/baseline.py` | `test_equity_baseline.py::test_JSON_스키마가_stage와_같다`<br>`::test_없는_상수는_skip_no_baseline` | `stage.baseline.write` import 확인 |
| T0.8 | `equity/__main__.py` | `test_equity_cli.py::test_서브커맨드_5개`<br>`::test_없는_테이블은_argparse_에러` | |

### T1 — `trading_calendar`

- 파일: `sql/trading_calendar.sql`, `rules_master.py::TRADING_CALENDAR`, `fixtures/trading_calendar.json`
- SQL: `stg_index_daily` distinct date ∪ (`stg_flow_split_daily`·`stg_credit_daily` 의 date > `backfill_end`) → `lag/lead` 윈도로 `prev_td`·`next_td`, `calendar_source ∈ {krx_index, gap_axis}`
- 테스트 `test_equity_calendar.py`
  - `::test_prev_next_td_손계산` — 연휴를 낀 5거래일 픽스처에서 `prev_td`/`next_td` 가 캘린더상 인접 거래일
  - `::test_첫날_prev_td는_NULL_마지막_next_td는_NULL`
  - `::test_gap_axis는_backfill_end_이후만`
  - `::test_거래일만_행이다_is_trading_day_컬럼_없음` (DESIGN §12 "항진명제라 삭제" 회귀)
  - `::test_EG1_행수_=_index_distinct_date_+_gap거래일`
- 완료: 절단본 EG0·EG1·EG3 pass, 픽스처 4 case(`first_td`·`holiday_gap`·`gap_axis_head`·`last_backfill_td`)

### T2 — `corp`

- 원천 `stg_corp_map`(corp_code·ticker·corp_name_current) + `stg_company`(acc_mt·induty_code_current)
- 테스트 `test_equity_corp.py`
  - `::test_fiscal_month_basis는_current_snapshot_고정` (P10: `stg_company` observed_date 1개)
  - `::test_induty_class_금융_64_65_66`
  - `::test_corp_cls_컬럼이_없다` — DESIGN §4-1 "현재값, 과거 필터 함정" 회귀 테스트
  - `::test_acc_mt_NULL_0` / `::test_EG1_=_distinct_corp_code`
- 완료: EG1 = 3,478(서버), 절단본에서는 절단 행수

### T3 — `security`

- 테스트 `test_equity_security.py`
  - `::test_sec_type_매핑_11어휘_손계산` — P11 의 `secugrp × stkcert_tp` 조합마다 1행씩(주권/보통주 → common, 구형·신형우선주·종류주권 → preferred, 부동산투자회사 → reit, 선박투자회사 → ship_fund, 투자회사·사회간접자본 → fund, 외국주권 → foreign, 주식예탁증권·증서 → dr)
  - `::test_spac이_common보다_우선` — `sect_tp='SPAC(소속부없음)'` ∨ `name LIKE '%기업인수목적%'`
  - `::test_etf는_list_date_NULL_basis_unknown`
  - `::test_delist_date_krx우선_kis대조` / `::test_delist_conflict_플래그`
  - `::test_마지막_존재일이_backfill_end면_delist_NULL_coverage_gap` (하한 조건 회귀)
  - `::test_sec_type_other는_EG7_격리`
  - `::test_ticker_0001A0는_TEXT` (정수 캐스팅 금지)
- 완료: 픽스처 `security.json` — 우선주 폐지 1 · ETF 1 · SPAC 1 · HK 1 · `0001A0` 1

### T4 — `security_span`

- 테스트 `test_equity_span.py`
  - `::test_재상장_036220_2구간_손계산`(~2016-05-04 / 2024-03-13~) · `::test_재상장_101970_2구간`
  - `::test_span_비중첩` · `::test_Σn_days_=_존재일수`
  - `::test_end_reason_delisted_vs_coverage_gap` · `::test_last_date는_backfill_end_이하`
  - `::test_ETF도_span을_갖는다` (DESIGN §12 "span 원천에 ETF 누락" 지적 반영 회귀)
- 완료: EG3 비중첩 0 · baseline `respan_count=2`

### T5 — `corp_ticker`

- 테스트 `test_equity_corp_ticker.py`
  - `::test_isin8은_KR7계열만_그룹핑` · `::test_비KR7_HK0_KYG_USU는_단독_corp`
  - `::test_003540_1대N_common_ticker` · `::test_link_basis_어휘_isin8_corp_map_none`
  - `::test_ETF는_corp_code_NULL` · `::test_EG3_KR7그룹당_보통주_정확히1`
  - `::test_EG1_=_security_행수`

### T6 — `index_daily`

- 테스트 `test_equity_index.py::test_1대1_행수` · `::test_available_date_=_date_basis_default` · `::test_grain_index_class_index_name_date`
- **문서 정정 필요**: WORKFLOW §3-1 산출 열은 `close_pt` 라고 적었지만 stage 실명은 `stg_index_daily.close_idx`(rules_krx.py). equity 컬럼명은 `close_idx` 로 간다 — §8-5.

### T7 — `universe_daily` (가장 큼, `build_by_year=True`)

- SQL 구조: `security_span × trading_calendar` 격자 CTE → `stg_listing_daily` 조인 → `stg_price_daily`/`stg_etf_price_daily` 조인 → 신호 CTE(`stg_disclosure`) → 상태 윈도
- 테스트 `test_equity_universe.py`
  - `::test_signal_술어는_접두어_제거_후_매칭` — `regexp_replace(report_nm,'^\[[^\]]*\]','')` (stage 가 이미 정규화했으므로 여기서 정규화 재적용 금지)
  - `::test_halt_state_지정후_첫거래일에_해제` / `::test_해제공시로_해제` / `::test_지정_해제_동일일_1214건_케이스`
  - `::test_status_우선순위_coverage_gap_delisted_suspended_listed`
  - `::test_no_trade_run_연속_거래일_손계산`
  - `::test_adv20창_D_19_D_거래일_손계산` (랙 미적용 원값 저장)
  - `::test_admin_state_KOSDAQ은_sect_tp_PIT_basis_measured`
  - `::test_admin_state_KOSPI는_365거래일창_basis_derived`
  - `::test_liquidation_window_개시부터_delist_date`
  - `::test_admin_flag는_2026_09_01_이전_NULL`
  - `::test_ETF_행_포함_sec_type_etf` · `::test_available_basis_컬럼군별`
  - `::test_EG3_halt_state_열린채_끝난_구간_0`
- 완료: EG1 등식 성립 · 픽스처 12 case(DESIGN §8 1단계 EG4 열 그대로)

### T8 — `universe_policy`

- 테스트 `test_equity_policy.py::test_predicate가_v_universe에서_평가된다` · `::test_threshold_kind_어휘_quantile_absolute_flag` · `::test_초기표는_비어있어도_빌드_성공`
- 초기 분위수 값은 서버 실측 후 등재(WORKFLOW §5 "정책 분위수 초기값과 근거" 기록 항목)

### T9 — `catalog.py` + `v_universe`

- 테스트: §4-4 전량 + `::test_v_universe_lag_override가_profile을_이긴다`
- 완료: `equity.duckdb` 가 read_only 로 열려 `v_universe` 호출됨

### T10 — 엔진 어댑터 (§6) — **backend 저장소 별도 PR**

### T11 — 서버 실측 1회전

1. `pin` → `inputs.json`
2. 테이블 순서대로 `build` (T1→T7), 각각 `_meta.gates` 확인
3. `baseline` 측정 → 사람 승인 → `baseline.json` 등재 → **2회차 빌드**로 `skip(no_baseline)` 게이트 정식 통과
4. 같은 `inputs` 로 3회차 → 파티션 `content_hash` 전량 일치 확인(EG5a)
5. `catalog` 재생성
6. `EQUITY_DESIGN.md §기록` 에 WORKFLOW §5 항목(입력 build_id·산출 행수·게이트 판정·baseline diff·빌드 시간·RSS + 1단계 전용 3항목) 기재

---

## 6. 엔진 어댑터 골격

### 6-1. 배치와 의존성 (먼저 결정해야 한다)

- 파일: `backend/src/backtest_engine/adapters/equity_duckdb.py`. `backtest_engine/adapters/` 는 평면 파일 구조다(`csv_bars.py`·`sqlite_bars.py`·`krx_parquet.py`) — `.claude/rules/backend-package-boundary.md` 의 `facade/` 노드 규약은 `strategy_workbench` 쪽에만 걸린다. 배치 자체는 문제없다.
- **이름 충돌 주의**: 같은 규칙 문서가 `adapters/outbound/equity_duckdb`(워크벤치 `EquityDataPort` 구현)를 따로 예약해 뒀고, `bootstrap/_container.py:59` 가 `equity_adapter="duckdb"` 를 아직 거절한다. 둘은 다른 것이다 — 엔진 3포트 어댑터(이 문서) vs 워크벤치 조회 포트. PR body 에 구분을 명시한다.
- **의존성**: backend `dependencies` 에 duckdb 가 없다(`fastapi`·`numpy`·`ruamel-yaml`·`uvicorn`). `code-style.md` "새 라이브러리 추가 금지 → 기존 의존성 대안 먼저 제시".

| 안 | 내용 | 장 | 단 |
|---|---|---|---|
| **A (권장)** | **pyarrow** 로 equity parquet 을 직접 읽는다. `krx_parquet.py` 와 동일 패턴 — `import pyarrow.parquet as pq` 를 함수 안에서, `pq.read_table(path, columns=…, filters=[…])`. MANIFEST 는 stdlib `json` | 새 의존성 0. optional extra `parquet` 이 이미 있고 계약 테스트가 `pytest.importorskip("pyarrow")` 선례를 갖는다. 필요한 질의(종목별 구간·날짜 범위·이벤트)는 전부 predicate pushdown 으로 충분 | 뷰 매크로(`v_universe`)를 못 쓴다 — 어댑터가 `security_span`·`price_daily`·`adj_factor`·`corp_event` **테이블**을 직접 읽는다(DESIGN §5 도 어댑터는 뷰가 아니라 이 4개만 읽는다고 적었다). 파일명이 `equity_duckdb.py` 면 거짓말 → `equity_parquet.py` 권장 |
| B | `[project.optional-dependencies] equity = ["duckdb>=1.5.5"]` 추가, 함수 안 import, 계약 테스트 `pytest.importorskip("duckdb")` | 카탈로그 매크로를 그대로 호출 | 새 의존성 = 사용자 승인 필요 |
| C | 어댑터를 `workspace/dongmin` 에 둔다 | backend 무변경 | EG-C ① 이 `backend/tests/test_bar_source_contract.py::BUILDERS` 등록을 요구 → **기각** |

아래 골격은 A·B 어느 쪽이든 같은 클래스 시그니처다. 읽기 함수 `_read(table, columns, filters)` 한 곳만 갈린다.

### 6-2. 클래스 시그니처

```python
"""equity 층 parquet 어댑터 — BarSource · UniverseSource · CorporateActionSource (DESIGN §7).

읽는 것은 4테이블뿐이다: security_span · price_daily · corp_event · adj_factor.
MANIFEST.json 의 current_build → partitions[].path 만 읽는다(맨 glob 금지 — STAGE_HANDOFF §1).
PIT: 생성자 asof·lag 로 `available_date <= asof - lag` 를 건다. lag 를 모르면 0 을 쓰지 않고
예외를 낸다 — lag_known=false 원천에 lag 0 을 적용하면 look-ahead 다(STAGE_HANDOFF §2).
"""

@dataclass(frozen=True)
class EquityRoot:
    """data/equity 루트. 테이블별 current_build 파티션 경로를 푼다."""

    root: Path

    def partition_files(self, table: str) -> tuple[Path, ...]: ...
    def build_id(self, table: str) -> str: ...


class EquityBarSource:
    def __init__(self, root: Path, asof: date,
                 lag_overrides: Mapping[str, int] | None = None) -> None: ...
    def load_bars(self, query: BarQuery) -> LoadResult: ...


class EquityUniverseSource:
    def __init__(self, root: Path, asof: date,
                 lag_overrides: Mapping[str, int] | None = None,
                 venue: str = "XKRX") -> None: ...
    def load_universe(self, query: UniverseQuery) -> UniverseResult: ...


class EquityCorporateActionSource:
    def __init__(self, root: Path, asof: date,
                 lag_overrides: Mapping[str, int] | None = None) -> None: ...
    def load_actions(self, query: CorporateActionQuery) -> CorporateActionResult: ...
```

생성자 `(root, asof, lag_overrides)` 는 DESIGN §7 마지막 줄이 지정한 것. `LoadResult.detail` / `CorporateActionResult.detail` 에 `build_id`·`asof`·적용 랙을 적어 "결과 메타에 기록" 요구를 만족한다.

### 6-3. `security_span` 기반 종목별 질의 구간

```python
def _spans(self, instrument: InstrumentId) -> tuple[tuple[date, date], ...]:
    """그 종목의 (first_date, last_date) 구간들. 재상장은 2구간(036220·101970)."""

def load_bars(self, query: BarQuery) -> LoadResult:
    return merge_results(self._load_one(i, query) for i in query.instruments)

def _load_one(self, instrument, query) -> LoadResult:
    spans = self._spans(instrument)
    if not spans:
        return LoadResult((), LoadStatus.NO_DATA,
                          detail=f"no security_span — symbol={instrument.symbol} "
                                 f"root={self._root} build={self._build_id('security_span')}")
    windows = [(max(f, query.start or f), min(l, query.end or l)) for f, l in spans]
    windows = [(a, b) for a, b in windows if a <= b]
    if not windows:
        return LoadResult((), LoadStatus.NO_DATA, detail=…)   # 구간과 안 겹침 = 전체 실패
    rows = [r for a, b in windows for r in self._read_price(instrument.symbol, a, b)]
    ...
```

- **사전 제외 금지**(DESIGN §7): 구간 전체가 무거래인 종목도 질의는 한다. 결과가 0행이면 `NO_DATA` → `merge_results` 가 전체 실패(계약 `test_missing_instrument_fails_whole_query`).
- `price_kind='reference'` ∨ `volume_shr = 0` 행은 Bar 로 안 내보내고 `dropped_rows` 로 보고. `krx_parquet.py` 가 `clean_raw_bars(..., drop_zero_volume=True)` 로 하는 것과 같은 자리 — **정제 로직은 `data.cleaning.clean_raw_bars` 를 그대로 재사용**한다(SoT 중복 금지).
- `open` NULL: `Bar.__post_init__` 이 `min(o,h,l,c) > 0` 을 강제하므로 close 로 채우지 않고 그 행을 drop + `dropped_rows`. DESIGN §7 의 "`open` NULL 을 close 로 채우지 않음" 을 만족한다.
- 중복 세션 검사: equity `price_daily` PK 가 (ticker,date)라 원리적으로 없지만, 계약 `test_duplicate_session_is_format_error` 를 통과하려면 어댑터가 읽은 뒤 검사해 `FORMAT_ERROR` 를 내야 한다 — `krx_parquet._first_duplicate_session` 과 같은 헬퍼를 둔다.

### 6-4. `UniverseSource`

```python
def load_universe(self, query: UniverseQuery) -> UniverseResult:
    if query.end is not None and query.end > self._backfill_end:
        return UniverseResult((), LoadStatus.NO_DATA,
                              detail=f"UniverseQuery.end beyond coverage — end={query.end} "
                                     f"backfill_end={self._backfill_end} (coverage_gap)")
    ...
```

- span 1행 → `Membership(InstrumentId("XKRX", ticker, AssetClass.EQUITY, "KRW"), first_date, last_date)` 1:1. 재상장 2구간 → Membership 2개(EG-C ③).
- `suspended` 는 구간을 끊지 않는다(Bar 부재는 span 안이므로 `NO_DATA` 가 아니라 그냥 세션 없음).
- `coverage_gap` 은 구간을 끊지 않고 **질의를 거절**한다(위 코드). `backfill_end` 는 `baseline.json` 에서 읽는다 — 코드 하드코딩 금지.
- `market` 은 필터 컬럼이지 구간 축이 아니다 → 생성자 옵션 `markets: frozenset[str] | None`(krx_parquet 의 `security_groups` 와 같은 자리).

### 6-5. `CorporateActionSource` — `ts = effective_date`

```python
_ACTION_MAP: dict[str, CorporateActionType] = {
    "split": CorporateActionType.SPLIT,
    "bonus": CorporateActionType.SPLIT,
    "stock_dividend": CorporateActionType.SPLIT,
    "reverse_split": CorporateActionType.REVERSE_SPLIT,
    "capred": CorporateActionType.REVERSE_SPLIT,
    "rights": CorporateActionType.SHARE_COUNT_CHANGE,
    "spinoff": CorporateActionType.SHARE_COUNT_CHANGE,
    "merger": CorporateActionType.SHARE_COUNT_CHANGE,
}

def _to_event(self, row) -> CorporateActionEvent | None:
    ratio = row["share_factor"]                      # ratio = 구주 1주당 신주 수 (types/events.py)
    if ratio is None or ratio <= 0:
        return None                                  # __post_init__ 가 ratio>0 을 강제 — 조용한
                                                     #   대체 금지, detail 에 사유를 남긴다
    kind = (CorporateActionType.SHARE_COUNT_CHANGE if not row["factor_ok"]
            else _ACTION_MAP.get(row["event_type"], CorporateActionType.SHARE_COUNT_CHANGE))
    return CorporateActionEvent(
        ts=datetime.combine(row["effective_date"], time.min),   # DESIGN §7: ts = 효력일 00:00
        instrument=…, action_type=kind, ratio=Decimal(str(ratio)),
        detail=(f"equity build={self._build_id('adj_factor')} event_id={row['event_id']} "
                f"type={row['event_type']} factor_source={row['factor_source']} "
                f"factor_ok={row['factor_ok']} available={row['available_date']}"),
    )
```

- PIT 필터: `available_date <= asof - lag`. `available_date > asof - lag` 인 계수는 **방출하지 않는다**(DESIGN §7).
- 임계 1.5 미만도 전달한다 — 엔진의 `data.corporate_actions.detect_share_count_events` 검출기를 equity 어댑터가 대체하므로 이중조정이 없다(DESIGN §7).
- `factor_ok=false` 는 종류를 SHARE_COUNT_CHANGE 로 강등(알림 전용, 포지션 미조정).
- 정렬: `actions.sort(key=lambda e: (e.ts, e.instrument.symbol))` — krx_parquet 과 같은 규약.

### 6-6. `BUILDERS` 등록 (EG-C ①)

`backend/tests/test_bar_source_contract.py` 는 `Builder = Callable[[Path, Rows], BarSource]` 를 요구한다(`Rows = dict[symbol, list[(session,o,h,l,c,v)]]`).

```python
def build_equity(root: Path, rows: Rows) -> BarSource:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from backtest_engine.adapters.equity_duckdb import EquityBarSource

    # 1) price_daily — 연도 파티션 + available_date = date
    # 2) security_span — 종목별 min/max 세션 1구간
    # 3) 테이블마다 MANIFEST.json (BuildRecord 1건, partitions[].path)
    _write_equity_table(root, "price_daily", _price_rows(rows), partition_by_year=True)
    _write_equity_table(root, "security_span", _span_rows(rows), partition_by_year=False)
    return EquityBarSource(root, asof=date(2099, 1, 1), lag_overrides={"price_daily.ohlcv": 0})


BUILDERS["equity"] = build_equity
ZERO_VOLUME_DROPPED["equity"] = True     # 정지행(volume=0) 은 Bar 로 안 나간다 — DESIGN §7
```

두 딕셔너리 모두 수정해야 한다 — `test_zero_volume_row_policy_is_explicit_per_adapter` 가 `ZERO_VOLUME_DROPPED[name]` 을 참조하므로 등록만 하고 정책을 안 적으면 `KeyError` 로 깨진다.

계약 8케이스 중 주의할 것:
- `test_clamp_drops_zero_price_rows_and_reports` — 가격 0 행. equity `price_daily` 는 O/H/L 이 NULL(stage `zero_is_missing`)이라 픽스처가 0을 넣으면 어댑터가 drop + `dropped_rows=1` 로 보고해야 한다. `clean_raw_bars` 가 처리.
- `test_strict_rejects_ohlc_violation` — `OhlcPolicy.STRICT` 를 `clean_raw_bars` 에 그대로 넘긴다.

---

## 7. 서버 실행 절차

### 7-1. 배포

```bash
# 로컬 (workspace/dongmin)
rsync -av --delete src/equity/     kael-server:~/quant-ledger/src/equity/
rsync -av          scripts/run_equity.sh scripts/equity_slice_from_stage.py \
                                   kael-server:~/quant-ledger/scripts/
```
stage 와 같은 배치다 — `run_stage.sh` 가 `PYTHONPATH=/home/kael/quant-ledger/src` 를 쓰고 `stage/` 가 그 아래 있다.

### 7-2. 실행 (`scripts/run_equity.sh`)

`run_stage.sh` 를 본뜨되 **flock 을 더한다**(WORKFLOW §1 "서버 테이블 직렬(`flock`)" — stage 스크립트에는 없다).

```bash
#!/bin/bash
# equity 빌드 러너 — 테이블 직렬(flock) · 같은 inputs 로 2회 빌드해 파티션 해시 재현성 확인.
#   사용: scripts/run_equity.sh <table> [--years 2010:2010] [--memory-limit 6GB]
#   로그: logs/equity_<table>.log · 요약 logs/equity_all/summary.tsv
#   실행 창: 06:30~익일 05:30 KST (daily_wise 크론 무동작 구간 — run_stage.sh 와 같은 규약)
cd /home/kael/quant-ledger
export QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src
TABLE="$1"; shift
LOG="logs/equity_${TABLE}.log"
mkdir -p logs data/equity/_tmp/spill
exec 9>data/equity/.build.lock
flock -w 7200 9 || { echo "lock timeout: 다른 equity 빌드가 도는 중" >> "$LOG"; exit 2; }
{
echo "════ equity build ${TABLE} 시작 $(TZ=Asia/Seoul date '+%m-%d %H:%M:%S KST') ════"
/usr/bin/time -v .venv/bin/python -m equity build "$TABLE" \
  --inputs data/equity/_pinned/inputs.json "$@" 2>&1 | tee /tmp/eq_${TABLE}_1.out
echo "──── 재현성: 같은 inputs 로 재빌드 ────"
.venv/bin/python -m equity build "$TABLE" --inputs data/equity/_pinned/inputs.json "$@" \
  2>&1 | tee /tmp/eq_${TABLE}_2.out | grep -E "^(ok|gate_failed)"
H1=$(grep -oE 'hash=[0-9]+:[0-9a-f]+' /tmp/eq_${TABLE}_1.out | head -1)
H2=$(grep -oE 'hash=[0-9]+:[0-9a-f]+' /tmp/eq_${TABLE}_2.out | head -1)
[ "$H1" = "$H2" ] && echo "재현성 OK: $H1" || echo "재현성 FAIL: run1 $H1 / run2 $H2"
echo "════ 종료 $(TZ=Asia/Seoul date '+%H:%M:%S KST') ════"
} >> "$LOG" 2>&1
```

전수 러너 `run_equity_all.sh` 는 `run_stage_all.sh` 를 그대로 본뜬다(`ORDER=(trading_calendar corp security security_span corp_ticker index_daily universe_daily universe_policy)`, `summary.tsv` 파싱 동일). **마지막에 `python -m equity catalog` 를 반드시 넣는다** — §8-6.

### 7-3. 1회전 순서

```bash
# 0) 입력 고정 (한 번, 모든 1단계 테이블이 같은 inputs 를 쓴다)
.venv/bin/python -m equity pin --tables stg_index_daily,stg_listing_daily,stg_etf_price_daily,\
stg_corp_map,stg_company,stg_delisted_master,stg_master_daily,stg_price_daily,stg_disclosure,\
stg_flow_split_daily,stg_credit_daily
# → data/equity/_pinned/<t>/v=<build>/ (하드링크) + inputs.json

# 1) 첫 슬라이스 실측 (RSS·스필·초 기록 — WORKFLOW §1)
scripts/run_equity.sh universe_daily --years 2010:2010

# 2) 전 구간
for T in trading_calendar corp security security_span corp_ticker index_daily \
         universe_daily universe_policy; do scripts/run_equity.sh "$T"; done

# 3) baseline 측정 → 사람 승인 → 등재 → 2회차 빌드로 skip(no_baseline) 정식 통과
.venv/bin/python -m equity baseline --out /tmp/baseline_new.json
diff data/equity/baseline.json /tmp/baseline_new.json     # diff 를 커밋 메시지에 (WORKFLOW §5)

# 4) 카탈로그
.venv/bin/python -m equity catalog
```

### 7-4. 로그·기록

- `logs/equity_<table>.log` — `/usr/bin/time -v` 로 최대 RSS 포함(stage 와 같은 방식).
- `logs/equity_all/summary.tsv` — `table status rows reject elapsed_s failed_gates` (run_stage_all.sh 형식).
- `data/equity/_failed/<build_id>.json` — 게이트 실패 보고서(테이블·build_id·inputs·행수·게이트 전량).
- 문서 기록은 WORKFLOW §5 표 그대로 `EQUITY_DESIGN.md §기록` 에.

### 7-5. 실패 시 롤백 — **롤백 동작이 없는 것이 설계다**

| 상황 | 결과 |
|---|---|
| 게이트 FAIL | `_failed/<build>.json` 기록 + `shutil.rmtree(tmp_root)`. `MANIFEST.json` 은 **손대지 않았다** → `current_build` 불변, 리더는 구 버전을 계속 본다 |
| 빌드 중 예외/OOM/kill | tmp 만 남는다. `MANIFEST.json` 불변. 다음 실행이 `if tmp_root.exists(): shutil.rmtree(tmp_root)` 로 청소(stage `build_table` 첫머리와 같은 자리) |
| 커밋 직후 오판 발견 | `manifest.py` 의 `builds[]` 에 직전 빌드가 keep=3 안에 남아 있다 → `MANIFEST.json` 의 `current_build` 를 직전 build_id 로 되돌리는 `equity rollback <table> <build_id>` 를 CLI 에 둘 수 있다. **1단계 범위 밖으로 두고 수동 편집 + `catalog` 재생성으로 처리**(파일 1개 원자 교체라 위험이 낮다) |
| 카탈로그 재생성 실패 | `os.replace` 전이면 `equity.duckdb` 불변, 후면 새 파일. 임시 파일만 남는다 |

**포인터 불변 원리**: `manifest.commit()` 은 `build_table` 의 **맨 마지막**에서만 불린다(stage 도 같다). 그 앞의 어떤 실패도 `MANIFEST.json` 을 건드리지 못한다.

---

## 8. 승인 필요 · 미확인 (착수 전에 답이 있어야 하는 것)

### 8-1. 엔진 어댑터의 duckdb 의존성

- **상황**: `backend/pyproject.toml` `dependencies` = fastapi·numpy·ruamel-yaml·uvicorn. duckdb 없음. optional extra 는 `parquet = ["pyarrow>=15"]` 뿐.
- **인풋**: `backend/src/backtest_engine/adapters/equity_duckdb.py` 를 만들고 `import duckdb` 를 쓰면 CI(`uv sync --locked --extra parquet` → `uv run pytest -q`)가 ImportError.
- **에러 위치**: 아직 없는 파일. 결정 지점은 `backend/pyproject.toml:6-19`.
- **위험성**: `code-style.md` "새 라이브러리 추가 금지 → 기존 의존성 대안 먼저 제시" 위반. §6-1 의 A(pyarrow 재사용, 파일명 `equity_parquet.py`) / B(optional extra `equity` 추가) 중 택일 필요. **권장 A.**

### 8-2. `_reject/<reason>/` 경로 표기

- **상황**: DESIGN §2 는 `_reject/<reason>/`, duckdb `COPY … PARTITION_BY (reject_reason)` 는 `_reject/reject_reason=<r>/` 를 만든다.
- **위험성**: 문서와 실물 경로가 다르면 다음 사람이 맨 glob 으로 찾다가 못 찾는다. 하이브 표기를 쓰면 읽을 때 사유가 컬럼으로 복원되는 이득이 있다. **하이브 채택 + 문서 정정**을 제안한다. (대안: 사유마다 개별 `COPY` 로 정확히 `_reject/<reason>/part0.parquet` — 사유 수만큼 쿼리가 늘지만 문서 그대로.)

### 8-3. 픽스처 위치 이중 규약

- **상황**: WORKFLOW §2 EG4 는 `data/equity/fixtures/<table>.json`, stage 선례는 `src/stage/fixtures/`("코드와 함께 산다").
- **위험성**: 로컬 TDD 는 서버 `data/` 없이 돌아야 한다. `src/equity/fixtures/` 를 정본으로 하고 `data/equity/fixtures/` 를 fallback 으로 받는 stage 와 같은 이중 경로를 제안한다(`build_table(fixtures_path=…)` 인자가 이미 그 구조).

### 8-4. `manifest.py` 에 public 원자 쓰기 함수 추가

- **상황**: `_pinned/<t>/MANIFEST.json` 을 쓰려면 원자 교체가 필요한데 `manifest._write_atomic` 은 private 이고, `manifest.commit()` 은 keep 밖 `v=` 를 rmtree 한다(하드링크 디렉토리가 지워진다).
- **인풋**: `inputs.pin()` → `_copy_build_record()`.
- **에러 위치**: `stage/manifest.py:commit()` 마지막 `for b in stale: shutil.rmtree(...)`.
- **위험성**: `commit()` 을 그대로 쓰면 고정한 입력이 4번째 pin 에서 삭제되어 EG5a 재현성이 조용히 깨진다. **제안**: `manifest.py` 에 `def write_pinned(table_root: Path, record: BuildRecord) -> None`(GC 없음) 3줄 추가 — stage 동작 불변, 별도 `refactor` 커밋. (대안: equity 안에 3줄 복제 — `code-style.md` "기존 SoT 먼저 찾기" 와 충돌.)

### 8-5. `close_pt` vs `close_idx`

- **상황**: WORKFLOW §3-1 산출 열이 `index_daily(index_key × date · close_pt)`. stage 실명은 `stg_index_daily.close_idx`(rules_krx.py), grain 은 `(index_class, index_name, date)`(DESIGN §4-1 과 일치, WORKFLOW 의 `index_key` 와 불일치).
- **위험성**: EG0 가 "stage 컬럼명 기준" 으로 실물 대조하므로 문서를 그대로 옮기면 첫 빌드가 `missing_columns` 로 실패한다. equity 컬럼명은 `close_idx`, grain 은 `(index_class,index_name,date)` 로 가고 **WORKFLOW §3-1 정정**을 제안한다.

### 8-6. 카탈로그가 GC 로 죽는 경로를 가리킬 수 있다

- **상황**: `catalog.rebuild()` 가 `MANIFEST.current_build` 의 **절대경로**를 매크로 본문에 굽는다(P1c: 상대경로 불가).
- **인풋**: (1) `equity catalog` 실행 (2) 같은 테이블을 3회 더 빌드·커밋 (3) 카탈로그 재생성 없이 `v_universe` 호출.
- **에러 위치**: `stage/manifest.py:commit()` 의 `shutil.rmtree(table_root / f"v={b.build_id}")` — keep=3 밖으로 밀린 `v=` 디렉토리를 지운다. 카탈로그는 그 경로를 계속 가리킨다.
- **위험성**: `read_parquet` 이 "No files found" 로 죽거나(에러면 다행) **일부 파티션만 남아 조용히 행이 줄어든다**. 방어: (a) 러너가 빌드 후 항상 `catalog` 를 부른다 (b) `catalog.rebuild()` 가 굽기 전에 모든 glob 의 파일 존재를 확인하고 없으면 예외 (c) EG-C ② 를 카탈로그 재생성 직후 1회 돌린다. 셋 다 넣는다.

### 8-7. 테스트 이름 언어

한글 테스트 이름(`test_prev_next_td_손계산`)은 과제문 예시이자 손계산 케이스를 이름에 담기 좋다. stage 는 전부 영문이다. equity 파일 안에서 통일한다는 조건으로 한글을 제안한다 — 승인 필요.

### 8-8. 확인하지 못한 것 (로컬에 duckdb 없음)

- **상황**: 이 워크트리 `.venv` 에 duckdb 가 없다(`ModuleNotFoundError`). `workspace/dongmin` 은 CI 대상도 아니다(`.github/workflows/ci.yml` 은 `backend`·`frontend` 잡뿐).
- **미확인 항목**: (1) `CREATE TABLE … AS SELECT` 에 named parameter `$x` 가 먹는지 (→ §2-2 는 `_const` 임시 테이블로 우회해 확인 없이 성립) (2) `COPY … PARTITION_BY (reject_reason)` 의 정확한 파일명 패턴 (3) 매크로 안에서 `read_parquet([…])` 리스트 인자 바인딩.
- **위험성**: T0 착수 전에 **로컬 개발 환경에 duckdb 를 설치**하지 않으면 TDD 자체가 불가능하다. stage 테스트도 `import duckdb` 를 하므로 stage 시절엔 있었던 환경이다 — equity 착수 전 첫 작업으로 확인/복구해야 한다. (테스트 함수 수는 현재 156개이고 KICKOFF §0 의 "194" 는 parametrize 포함 수집 건수로 보인다 — 이것도 실행 환경이 없어 확인하지 못했다.)

### 8-9. EG-C ① 은 backend 저장소 파일 2곳을 고쳐야 한다

`backend/tests/test_bar_source_contract.py` 의 `BUILDERS` 와 `ZERO_VOLUME_DROPPED` 딕셔너리. equity 빌드 PR 과 분리한 별도 PR 로 간다(`code-style.md` 기능/cleanup 분리 + 저장소 경계).
