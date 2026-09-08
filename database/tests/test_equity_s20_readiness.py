"""S20 `factor_readiness` — 절단본 전 체인 + `dataset_profile` 위 실제 `build_table` 왕복
(DESIGN v1.2 §4-8 · GATES v1.0 §6 EG10 · §9 「EG10 이름」 · WORKFLOW §4 DoD 6단계).

절단본 실측(2026-09-06 S19-2): **35 ready / 19 blocked** (ready 비율 0.648). 막힌 사유 분포는
`field_unavailable` 5(F02·F04·F05·F08 + GAP-09 R04) · `partial_support` 12(컨센서스·의견 6 ·
금융업 매출액 3 · EPS 분할 미조정 1 · KOSPI 관리종목 비대칭 1 + **GAP-03 기관 순매수 1**) ·
`no_observations` 2(E05·E06 — `corp_event` MVP 4유형 밖이라 행이 0).

S19-2 직전은 31 ready / 23 blocked 였고 `field_unavailable` 이 10 이었다 — 격자 3테이블
(`flow_daily`·`short_daily`·`credit_daily`)이 커밋돼 있는데 `field_profiles` 선언이 비어
`dataset_profile` 에 행이 없었기 때문이다. 선언을 채우자 F01·F06·F07·F09 넷이 ready 로 열리고
F03 이 `field_unavailable` → `partial_support`(GAP-03) 로 옮겨 갔다.

선언 54가 `FACTORS.md` 정본과 어긋나지 않는지는 **문서를 파싱해** 대조한다(`check_field_map.py`
규약) — 표를 손으로 옮긴 곳이라 드리프트가 가장 쉽게 생기는 지점이다.

골든 픽스처는 **모집단 비의존**(선언 + 선언 조인으로만 정해지는 판정)만 담고, 커버 실측에 걸린
판정(`status='ready'`·`no_observations`·`first_usable_date`)은 절단본 손계산 테스트가 맡는다
(§9 S19 2차 — 서버 EG4 폐기의 교훈).
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest
from equity import build, gates, rules_s19, rules_s20
from equity.model import EquityTable
from test_equity_s19_profile import SEED, STAGE_SLICE, build_chain

DOCS = Path(__file__).parents[1] / "docs"
REGISTRY_DOC = Path(__file__).parents[2] / "backend" / "FACTORS.md"

# 2026-09-07: corp_event 가 자사주 취득·CB 발행을 싣기 시작해 E05·E06 이 열렸다(E05·E06 / 결정 1).
# `no_observations` 는 「필드는 선언했는데 값이 한 줄도 없다」였고, 값이 생기며 사유가 사라졌다.
# 2026-09-07 S08-2: `stg_foreign_daily` 를 flow_daily 에 이어 F02·F08 이 열렸다.
# 2026-09-07 F05: 요구 재료를 실재하는 `short.short_sale_volume` 로 정정해 열렸다.
# 2026-09-07 GAP-03 종결: 「기관」 = 원장 합계로 확정해 F03 이 열렸다.
# 2026-09-07 S02-2: `benchmark.close` 선언으로 R04(시장 베타)가 열렸다.
N_READY = 42
N_BLOCKED = 12
BLOCKED_REASON_COUNTS = {"field_unavailable": 1, "partial_support": 11}
READINESS_GATES = ["EG0", "EG7", "EG1", "EG2", "EG3", "EG10", "EG4", "EG5a"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, build.BuildResult]:
    """체인 25 + `dataset_profile` + `factor_readiness`. 느려서 모듈에서 한 번만 짓는다."""
    eq = tmp_path_factory.mktemp("s20") / "equity"
    build_chain(eq)
    p = build.build_table(rules_s19.DATASET_PROFILE, STAGE_SLICE, eq, SEED,
                          build_id="b_dataset_profile")
    assert p.ok, [(g.name, g.status.value, g.detail) for g in p.gates]
    r = build.build_table(rules_s20.FACTOR_READINESS, STAGE_SLICE, eq, SEED,
                          build_id="b_factor_readiness")
    return eq, r


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


def _rows(out_dir: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect()
    try:
        con.execute("CREATE OR REPLACE TEMP VIEW fr AS SELECT * FROM read_parquet("
                    f"'{out_dir / '*.parquet'}')")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 선언이 문서와 어긋나지 않는다 ────────────────────────────────────────────

def _factors_md_ids() -> list[str]:
    """`FACTORS.md` §1~§7 표의 팩터 ID. §12 구 F## 대응표는 축이 달라 제외한다."""
    text = (DOCS / "FACTORS.md").read_text(encoding="utf-8")
    body = text[text.index("## 1. 밸류"):text.index("## 8. 업종별")]
    return re.findall(r"^\| *([VQGIFMRE]\d{2}) *\|", body, re.M)


def _registry_ids() -> set[str]:
    """`backend/FACTORS.md` 레지스트리 50 ID (`check_field_map.FACTOR_ID_RE` 와 같은 축)."""
    return {m.group(1) for line in REGISTRY_DOC.read_text(encoding="utf-8").splitlines()
            if (m := re.match(r"^\| *\d+ *\| *`([a-z]+\.[a-z_0-9]+)` *\|", line))}


def test_선언_54는_FACTORS_문서의_정본_ID_와_같다() -> None:
    doc_ids = _factors_md_ids()
    assert len(doc_ids) == 54
    assert list(rules_s20.FACTOR_IDS) == doc_ids


def test_그룹별_개수가_문서의_V7_Q8_G10_I5_F9_M3_R4_E8_과_같다() -> None:
    counts: dict[str, int] = {}
    for fid in rules_s20.FACTOR_IDS:
        counts[fid[0]] = counts.get(fid[0], 0) + 1
    assert counts == {"V": 7, "Q": 8, "G": 10, "I": 5, "F": 9, "M": 3, "R": 4, "E": 8}


def test_registry_대응은_레지스트리_50_안에서만_고른다() -> None:
    registry = _registry_ids()
    assert len(registry) == 50
    mapped = {f.registry_factor_id for f in rules_s20.FACTORS if f.registry_factor_id}
    assert mapped <= registry
    assert len(mapped) == len({f.registry_factor_id for f in rules_s20.FACTORS
                               if f.registry_factor_id})       # 1:1 (중복 대응 금지)


def test_G04는_원장_EPS_가_아니라_순이익과_주식수를_요구한다() -> None:
    """원장 `eps_basic` 은 **주식분할 미조정**이라 시계열 비율이 분할 구간에서 가짜 점프를 낸다
    (삼성전자 2018 1분기 85,435 vs 사업보고서 6,461 — 50:1 분할).

    그래서 요구 재료를 순이익 ÷ 주식수로 바꿨다(BLOCKED_FACTORS §5-3 (가)) — 분할은 주식수
    변화에 그대로 반영되므로 가짜 점프가 원리적으로 생기지 않는다. 나눗셈은 팩터층 몫이라는
    F05·V02 와 같은 규약이다.
    """
    spec = next(f for f in rules_s20.FACTORS if f.factor_id == "G04")
    assert spec.required_field_ids == ("financial.net_income", "price.shares_outstanding")
    assert "financial.eps_basic" not in spec.required_field_ids


def test_요구_필드는_전부_field_id_문법이다() -> None:
    for f in rules_s20.FACTORS:
        for field_id in f.required_field_ids:
            assert re.fullmatch(r"[a-z]+\.[a-z_0-9]+", field_id), (f.factor_id, field_id)


# ── 빌드 왕복 ────────────────────────────────────────────────────────────────

def test_54행이_지어지고_게이트가_전부_통과한다(built) -> None:
    _, r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == READINESS_GATES
    assert r.n_rows == rules_s20.N_FACTORS == 54
    assert r.n_reject == 0
    assert _gate(r, "EG1").detail == "declaration_table"


def test_절단본_준비도는_31_대_23_이다(built) -> None:
    _, r = built
    got = dict(_rows(r.out_dir, "SELECT status, count(*) FROM fr GROUP BY 1 ORDER BY 1"))
    assert got == {"ready": N_READY, "blocked": N_BLOCKED}
    m = _gate(r, "EG10").metrics
    assert (m["n_ready"], m["n_blocked"]) == (N_READY, N_BLOCKED)
    assert m["blocked_reason_counts"] == BLOCKED_REASON_COUNTS


def test_수급_공매도_신용_9팩터의_판정은_선언한_필드가_가른다(built) -> None:
    """격자 3표의 필드 선언이 수급 9팩터의 판정을 가른다.

    S19-2 가 6필드를 선언해 F01·F06·F07·F09 를 열었고, **2026-09-07 S08-2** 가
    `stg_foreign_daily`(키움 ka10008)를 `flow_daily` 에 이어 F02·F08 을 더 열었다.

    남는 둘은 **선언을 빠뜨린 것이 아니라 재료 판단이 남은 것**이다:
      F04 `flow.pension_net_buy` — `penfnd_etc_krw` 는 실재하지만 키움과 KIS 가 같은 칸을 채운
          적이 0건이라(완전 배타) KIS 의 「기금」이 키움의 「연기금등」과 같은 주체인지 확인할
          축이 없다. 사람 결정이 먼저다(BLOCKED_FACTORS F04)
    (F05 는 2026-09-07 에 열렸다 — 요구 재료가 만들지 않기로 한 필드를 가리키고 있었고,
    실재하는 `short.short_sale_volume` 로 정정했다. 나눗셈은 여전히 팩터층 몫이다.)
    (F03 도 2026-09-07 에 열렸다 — 「기관」을 원장 합계로 확정해 GAP-03 이 닫혔다.)
    """
    _, r = built
    got = dict(_rows(r.out_dir, "SELECT factor_id, coalesce(blocked_reason, 'ready') FROM fr "
                                "WHERE factor_id LIKE 'F0%' ORDER BY 1"))
    assert sorted(got) == [f"F0{i}" for i in range(1, 10)]
    assert {f for f, v in got.items() if v == "ready"} == {"F01", "F02", "F03", "F05", "F06",
                                                           "F07", "F08", "F09"}
    assert got["F04"] == "field_unavailable: flow.pension_net_buy"



def test_막힌_행은_사유와_소유자를_반드시_갖는다(built) -> None:
    _, r = built
    assert _rows(r.out_dir, "SELECT factor_id FROM fr WHERE status = 'blocked' "
                            "AND (blocked_reason IS NULL OR owner IS NULL)") == []
    reasons = {str(x) for (x,) in _rows(
        r.out_dir, "SELECT DISTINCT split_part(blocked_reason, ':', 1) FROM fr "
                   "WHERE blocked_reason IS NOT NULL")}
    assert reasons <= set(rules_s20.BLOCKED_REASONS)
    owners = {str(x) for (x,) in _rows(r.out_dir, "SELECT DISTINCT owner FROM fr")}
    assert owners <= set(rules_s20.OWNER_VOCAB)


def test_ready_행은_시작일을_갖고_막힌_행은_사유가_없을_때만_갖는다(built) -> None:
    _, r = built
    assert _rows(r.out_dir, "SELECT factor_id FROM fr WHERE status = 'ready' "
                            "AND (first_usable_date IS NULL OR blocked_reason IS NOT NULL)") == []
    # 재료가 없거나(field_unavailable) 관측이 0 이면(no_observations) 시작일을 지어내지 않는다
    assert _rows(r.out_dir,
                 "SELECT factor_id FROM fr WHERE first_usable_date IS NOT NULL AND "
                 "split_part(blocked_reason, ':', 1) IN ('field_unavailable', "
                 "'no_observations')") == []


def test_시작일은_요구_재료_중_가장_늦게_열린_것이다(built) -> None:
    """V05 는 GAP-02 계정(2023-11-14)이 가장 늦어 그날이 팩터 시작일이다."""
    eq, r = built
    (first,), = _rows(r.out_dir, "SELECT first_usable_date FROM fr WHERE factor_id = 'V05'")
    profile = eq / "dataset_profile" / "v=b_dataset_profile"
    con = duckdb.connect()
    try:
        con.execute("CREATE TEMP VIEW dp AS SELECT * FROM read_parquet("
                    f"'{profile / '*.parquet'}')")
        spec = next(f for f in rules_s20.FACTORS if f.factor_id == "V05")
        ids = ", ".join(f"'{x}'" for x in spec.required_field_ids)
        (expect,) = con.execute(
            f"SELECT max(coverage_from) FROM dp WHERE field_id IN ({ids})").fetchone()
    finally:
        con.close()
    assert first == expect
    assert str(first) == "2023-11-14"


def test_required_columns_는_프로파일에서_유도된다(built) -> None:
    """선언은 field_id 만 말한다 — 컬럼은 `dataset_profile` 조인이 채운다."""
    _, r = built
    (cols,), = _rows(r.out_dir, "SELECT required_columns FROM fr WHERE factor_id = 'M01'")
    assert list(cols) == ["price_adj_daily.adj_close"]
    (cols,), = _rows(r.out_dir, "SELECT required_columns FROM fr WHERE factor_id = 'Q01'")
    assert list(cols) == ["fin_std.net_income", "fin_std.total_equity"]
    (cols,), = _rows(r.out_dir, "SELECT required_columns FROM fr WHERE factor_id = 'F01'")
    assert list(cols) == ["flow_daily.frgnr_invsr_krw", "price_daily.value_krw"]
    # S08-2 로 F02 의 재료가 격자에 붙었다 — 컬럼이 조인으로 채워진다
    (cols,), = _rows(r.out_dir, "SELECT required_columns FROM fr WHERE factor_id = 'F02'")
    assert list(cols) == ["flow_daily.foreign_wght_pct"]
    # 프로파일 행이 없는 필드는 컬럼도 없다 — F04 는 요구 재료가 그 하나뿐이라 빈 목록이다
    (cols,), = _rows(r.out_dir, "SELECT required_columns FROM fr WHERE factor_id = 'F04'")
    assert list(cols) == []


def test_커버_실측에_걸린_판정은_손계산으로_잰다(built) -> None:
    """골든 픽스처는 모집단 비의존 선언값만 담는다(§9 S19 1차) — 커버율에서 나온 판정은 여기서.

    M01 은 재료(`price.adj_close`) 커버가 100% 라 ready 이고 시작일이 캘린더 하한이다.
    E05·E06 은 `corp_event` 가 MVP 4유형만 적재해 재료 커버가 0 → `no_observations`,
    시작일 NULL(재료가 없는데 날짜를 적으면 표가 거짓말을 한다).
    """
    _, r = built
    got = {f: (s, str(d) if d is not None else None, b) for f, s, d, b in _rows(
        r.out_dir, "SELECT factor_id, status, first_usable_date, blocked_reason FROM fr "
                   "WHERE factor_id IN ('M01', 'E05', 'E06', 'V05', 'F07', 'F09') ORDER BY 1")}
    assert got["M01"] == ("ready", "2010-01-04", None)
    # 대차잔고는 KIS 유닛이 덮는 구간만 있다 — 가장 늦게 열린 재료가 팩터의 시작일이다
    assert got["F07"] == ("ready", "2014-01-02", None)
    assert got["F09"] == ("ready", "2010-01-04", None)
    # 자사주·CB 를 싣기 시작하며 둘 다 열렸다 — 시작일은 각 원천의 첫 공시일이다
    assert got["E05"] == ("ready", "2015-01-23", None)
    assert got["E06"] == ("ready", "2023-07-18", None)
    # GAP-02 계정이 가장 늦게 열려 V05 의 시작일이 된다
    assert got["V05"] == ("ready", "2023-11-14", None)


def test_같은_입력_재빌드는_파티션_해시가_같다(built) -> None:
    eq, first = built
    again = build.build_table(rules_s20.FACTOR_READINESS, STAGE_SLICE, eq, SEED,
                              build_id="b_factor_readiness_2")
    assert again.ok
    assert again.content_hash == first.content_hash
    assert _gate(again, "EG5a").status is gates.GateStatus.PASS


N_DETERMINISM_BUILDS = 5


def test_같은_입력으로_다섯_번_지어도_해시가_같다(built) -> None:
    """리스트 컬럼(`required_columns`·`required_field_ids`)과 `first_usable_date` 가 집계 순서에
    새면 여기서 드러난다 — `list_sort` 와 `max` 로 총순서를 못박은 것의 회귀(§9 S19 2차)."""
    eq, first = built
    hashes = {first.content_hash}
    keep = N_DETERMINISM_BUILDS + 2      # manifest GC 가 모듈 픽스처 원본을 지우지 않게
    for i in range(N_DETERMINISM_BUILDS - 1):
        r = build.build_table(rules_s20.FACTOR_READINESS, STAGE_SLICE, eq, SEED,
                              build_id=f"b_fr_det_{i}", threads=3, keep=keep)
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
        hashes.add(r.content_hash)
    assert len(hashes) == 1, hashes


def test_리스트_컬럼은_정렬돼_있다(built) -> None:
    """`list()` 는 정렬이 없으면 스캔 순서를 그대로 담는다 — `required_columns` 는 list_sort 다."""
    _, r = built
    for (cols,) in _rows(r.out_dir, "SELECT required_columns FROM fr"):
        assert list(cols) == sorted(cols)
    body = rules_s20.FACTOR_READINESS.sql_path.read_text(encoding="utf-8")
    assert body.count("list_sort(list(DISTINCT") == 6      # 사유 5축 + required_columns


def test_ready_하한은_미등재라_술어만_빠지고_측정치가_남는다(built) -> None:
    """`ready_min` 은 사람 승인 대기 — 나머지 폐기형 술어는 첫 빌드에도 그대로 돈다."""
    _, r = built
    m = _gate(r, "EG10").metrics
    assert m["ready_min"] is None
    assert "n_ready_below_baseline" not in m
    assert m["n_ready"] == N_READY


# ── EG10 폐기형 (부정 픽스처) ────────────────────────────────────────────────

def _variant(tmp_path: Path, rule: EquityTable, name: str, sql: str) -> EquityTable:
    """산출 SQL 만 바꾼 변종. 이름을 바꿔야 정본 MANIFEST 를 건드리지 않는다."""
    path = tmp_path / f"{name}.sql"
    path.write_text(sql, encoding="utf-8")
    return EquityTable(**{**rule.__dict__, "name": name, "sql_path": path})


def _body() -> str:
    return rules_s20.FACTOR_READINESS.sql_path.read_text(encoding="utf-8").strip().rstrip(";")


def test_ready_인데_재료_컬럼이_없으면_EG10_이_폐기한다(built, tmp_path: Path) -> None:
    """표가 'ready' 라고 적었는데 그 컬럼이 실물에 없으면 표가 거짓말을 하는 것이다."""
    eq, _ = built
    sql = (f"SELECT * EXCLUDE (required_columns), "
           "list_value('price_daily.no_such_column') AS required_columns "
           f"FROM ({_body()}) t")
    # 컬럼 순서를 선언과 맞추기 위해 다시 투영한다
    cols = ", ".join(f'"{c}"' for c in rules_s20.FACTOR_READINESS.columns)
    rule = _variant(tmp_path, rules_s20.FACTOR_READINESS, "factor_readiness_bad_column",
                    f"SELECT {cols}, reject_reason FROM ({sql}) v")
    r = build.build_table(rule, STAGE_SLICE, eq, SEED, build_id="b_bad_column",
                          fixtures_path=tmp_path / "none.json")
    assert not r.ok
    g = _gate(r, "EG10")
    assert g.status is gates.GateStatus.FAIL
    assert g.metrics["n_ready_required_column_absent"] > 0
    assert "price_daily.no_such_column" in g.metrics["missing_required_columns"]


def test_사유_없이_blocked_로_적으면_EG10_이_폐기한다(built, tmp_path: Path) -> None:
    sql = (f"SELECT * EXCLUDE (status, blocked_reason), 'blocked' AS status, "
           f"NULL::VARCHAR AS blocked_reason FROM ({_body()}) t")
    cols = ", ".join(f'"{c}"' for c in rules_s20.FACTOR_READINESS.columns)
    eq, _ = built
    rule = _variant(tmp_path, rules_s20.FACTOR_READINESS, "factor_readiness_no_reason",
                    f"SELECT {cols}, reject_reason FROM ({sql}) v")
    r = build.build_table(rule, STAGE_SLICE, eq, SEED, build_id="b_no_reason",
                          fixtures_path=tmp_path / "none.json")
    assert not r.ok
    assert _gate(r, "EG10").metrics["n_blocked_without_reason"] == 54


def test_행이_모자라면_EG10_이_폐기한다(built, tmp_path: Path) -> None:
    eq, _ = built
    cols = ", ".join(f'"{c}"' for c in rules_s20.FACTOR_READINESS.columns)
    rule = _variant(tmp_path, rules_s20.FACTOR_READINESS, "factor_readiness_short",
                    f"SELECT {cols}, reject_reason FROM ({_body()}) t WHERE factor_id <> 'M01'")
    r = build.build_table(rule, STAGE_SLICE, eq, SEED, build_id="b_short",
                          fixtures_path=tmp_path / "none.json")
    assert not r.ok
    g = _gate(r, "EG10")
    assert g.metrics["n_rows_off_expected"] == 1
    assert g.metrics["n_factor_id_absent"] == 1
