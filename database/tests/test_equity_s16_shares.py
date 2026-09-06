"""S16 `shares_outstanding`·`treasury_stock`·`dividend_event` — 절단본 e2e 왕복 + 손 트리 부정.

체인은 `corp_ticker`(S01) → `trading_calendar`(S02) → `price_daily`(S04) → S16 3테이블이다.
`shares_outstanding` 만 앞 셋을 읽는다(산출식이 아니라 KRX 대조 기록형이 읽는다 — DESIGN §4-2
"DART 주식수는 검산·보조"), `treasury_stock`·`dividend_event` 는 stage 1테이블씩만 읽는다.

절단본 실측(법인 10개):
  `stg_shares` 319 → 집계행(`se='합계'`) 82 제외 237 = 산출 237(접힌 판본 0).
  `stg_tesstk` 1,162 → 집계행('총계','총계','총계') 127 제외 1,035 → grain 중복 58 접어 977.
  `stg_dividend` 1,227(`row_kind` 열 없음) → grain distinct 221 = 산출 221,
      (grain, `se`) 축에서 60행이 접힌다(전부 `stock_knd='-'` 이고 한쪽만 값이 있다).
손검산 두 자리: 삼성전자 2018 배당(보통주 DPS 1,416 · 우선주 1,417 · 총액 9,619,243백만원 ·
연결 배당성향 21.9%)과 삼성전자 2025 자사주(기초 29,700,000 + 취득 118,314,495 − 처분
6,040,880 − 소각 50,144,628 = 기말 91,828,987).
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s04, rules_s05, rules_s16
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from equity.model import EquityTable

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SO = rules_s16.SHARES_OUTSTANDING
TS = rules_s16.TREASURY_STOCK
DE = rules_s16.DIVIDEND_EVENT

N_SHARES = 237
N_TREASURY = 977
N_DIVIDEND = 221
GATE_ORDER = {
    "shares_outstanding": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_shares_outstanding",
                           "EG4", "EG5a"],
    "treasury_stock": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_treasury_stock", "EG4", "EG5a"],
    "dividend_event": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_dividend_event", "EG4", "EG5a"],
}


def _seed() -> Baseline:
    """S01·S02·S04·S16 seed 를 합친다 — 체인 전체가 한 baseline 을 본다."""
    d = Path(rules_s16.__file__).parent
    merged: dict[str, object] = {}
    for name in ("s01", "s02", "s03", "s04", "s16"):
        p = d / f"baseline_seed_{name}.json"
        if p.exists():
            merged.update(load(p).data)
    return Baseline({k: v for k, v in merged.items()
                     if not k.startswith("_") and k != "measured_at"})


SEED = _seed()


class Chain:
    """절단본 위 e2e 체인 1회 — 읽기 전용 검사가 여럿이라 모듈에서 한 번만 짓는다."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for rule, bid in ((rules_s01.CORP_TICKER, "b_s16_ct"),
                          (rules_s02.TRADING_CALENDAR, "b_s16_cal"),
                          (rules_s04.PRICE_DAILY, "b_s16_pd")):
            r = build.build_table(rule, STAGE_SLICE, root, SEED, build_id=bid)
            assert r.ok, (rule.name, [(g.name, g.status.value, g.detail) for g in r.gates])
        self.results = {rule.name: build.build_table(rule, STAGE_SLICE, root, SEED,
                                                     build_id=f"b_s16_{bid}")
                        for rule, bid in ((SO, "so"), (TS, "ts"), (DE, "de"))}

    def __getitem__(self, table: str) -> build.BuildResult:
        return self.results[table]


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Chain:
    return Chain(tmp_path_factory.mktemp("s16") / "equity")


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _metrics(r: build.BuildResult, name: str) -> dict[str, object]:
    return dict(_gate(r, name).metrics)


def _rows(out_dir: Path, where: str = "TRUE") -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * EXCLUDE (v, year) FROM read_parquet("
                          f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true) "
                          f"WHERE {where}")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _rejects(out_dir: Path, key: str) -> list[tuple[object, object]]:
    con = duckdb.connect()
    try:
        return [(r[0], r[1]) for r in con.execute(
            f"SELECT {key}, reject_reason FROM read_parquet("
            f"'{out_dir / '_reject' / '*' / '*.parquet'}', hive_partitioning=true) "
            "ORDER BY 1").fetchall()]
    finally:
        con.close()


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("table", "n_rows"), [("shares_outstanding", N_SHARES),
                                               ("treasury_stock", N_TREASURY),
                                               ("dividend_event", N_DIVIDEND)])
def test_절단본_빌드가_전_게이트를_통과한다(chain: Chain, table: str, n_rows: int) -> None:
    r = chain[table]
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert [g.name for g in r.gates] == GATE_ORDER[table]
    assert {g.name: g.status.value for g in r.gates if g.name != "EG5a"} == {
        name: "pass" for name in GATE_ORDER[table] if name != "EG5a"}
    assert (r.n_rows, r.n_reject) == (n_rows, 0)


def test_EG1_은_stage_비집계_grain_행수와_같다(chain: Chain) -> None:
    """세 등식 다 `.sql` 의 모집단 CTE 를 재사용한다 — 정의를 두 벌 베끼지 않는다."""
    assert [(_metrics(chain[t], "EG1")["lhs"], _metrics(chain[t], "EG1")["rhs"],
             _metrics(chain[t], "EG1")["delta"])
            for t in ("shares_outstanding", "treasury_stock", "dividend_event")] == [
        (N_SHARES, N_SHARES, 0), (N_TREASURY, N_TREASURY, 0), (N_DIVIDEND, N_DIVIDEND, 0)]


def test_집계행은_모집단_밖이고_판본_중복은_접힌다(chain: Chain) -> None:
    so = _metrics(chain["shares_outstanding"], "EG3_shares_outstanding")
    assert (so["n_pool_rows"], so["n_pool_grain"], so["n_collapsed_src_rows"]) == (237, 237, 0)
    ts = _metrics(chain["treasury_stock"], "EG3_treasury_stock")
    assert (ts["n_pool_rows"], ts["n_pool_grain"], ts["n_collapsed_src_rows"]) == (1035, 977, 58)
    assert ts["n_total_label_in_out"] == 0        # 총계 라벨은 산출에 없다
    assert ts["n_subtotal_rows"] == 246           # '소계' 는 남는다(모집단 정의)
    de = _metrics(chain["dividend_event"], "EG3_dividend_event")
    assert (de["n_pool_rows"], de["n_pool_grain"]) == (1227, 221)
    assert (de["n_pool_grain_se"], de["n_collapsed_grain_se"]) == (1167, 60)
    assert (de["n_value_conflict"], de["n_grain_multi_rcept"]) == (0, 0)


def test_주식수_항등과_KRX_대조는_기록형이다(chain: Chain) -> None:
    """발행 = 자기 + 유통 · DART ↔ KRX 는 폐기형이 아니다 — 정본은 KRX 다(DESIGN §4-2)."""
    m = _metrics(chain["shares_outstanding"], "EG3_shares_outstanding")
    assert (m["n_identity_measurable"], m["n_share_identity_mismatch"]) == (76, 0)
    # KRX 대조: 종류 대응 103행 중 티커 다중 11 · 가격 없음 2 · 폐지·티커 재사용 구간 16 →
    # 비교 73, 어긋남 6(발행주식총수 ≠ 상장주식수인 구간). 어긋나도 게이트는 통과한다.
    assert (m["n_krx_class_rows"], m["n_krx_ambiguous"], m["n_krx_no_price"]) == (103, 11, 2)
    assert (m["n_krx_stale"], m["n_krx_compared"], m["n_krx_mismatch"]) == (16, 73, 6)
    assert _gate(chain["shares_outstanding"], "EG3_shares_outstanding").status is GateStatus.PASS


def test_원장_총계는_소계를_뺀_잎_합과_같다(chain: Chain) -> None:
    """GATES FX-4B-001 의 전수판. '소계' 를 빼지 않으면 이중계상이다."""
    m = _metrics(chain["treasury_stock"], "EG3_treasury_stock")
    assert (m["n_ledger_total_groups"], m["n_ledger_total_mismatch"]) == (125, 0)


def test_삼성전자_2018_배당_손검산(chain: Chain) -> None:
    out = chain["dividend_event"].out_dir
    assert out is not None
    rows = {str(r["stock_knd"]): r for r in _rows(
        out, "corp_code = '00126380' AND bsns_year = '2018'")}
    assert set(rows) == {"-", "보통주", "우선주"}
    # 종류 축 — 주당 현금배당금·현금배당수익률만 온다
    assert float(str(rows["보통주"]["dps_krw"])) == 1416.0
    assert float(str(rows["우선주"]["dps_krw"])) == 1417.0
    assert float(str(rows["보통주"]["yield_pct"])) == 3.7
    assert rows["보통주"]["cash_total_krw"] is None and rows["보통주"]["payout_pct"] is None
    # 법인 축 — 총액(백만원 → 원)·배당성향은 '-' 행에만
    assert float(str(rows["-"]["cash_total_krw"])) == 9619243 * 1_000_000
    assert float(str(rows["-"]["payout_pct"])) == 21.9
    assert rows["-"]["payout_basis"] == "consolidated"
    assert rows["-"]["dps_krw"] is None
    # PIT 축은 접수일이다 — 배당 기준일·락일이 아니다(DESIGN §4-5 · §11 TR 불가)
    for r in rows.values():
        assert (r["available_date"], r["available_basis"]) == (date(2019, 4, 1), "derived")
        assert r["stlm_dt"] == date(2018, 12, 31)
        assert r["rcept_no"] == "20190401004781"


def test_삼성전자_2025_자사주_손검산(chain: Chain) -> None:
    out = chain["treasury_stock"].out_dir
    assert out is not None
    rows = _rows(out, "corp_code = '00126380' AND bsns_year = '2025' "
                      "AND stock_knd = '보통주' AND acqs_mth3 = '장내직접취득'")
    assert len(rows) == 1
    r = rows[0]
    begin, acq, dsps, retire, end = (int(str(r[c])) for c in (
        "begin_shr", "acquired_shr", "disposed_shr", "retired_shr", "end_shr"))
    assert (begin, acq, dsps, retire, end) == (
        29_700_000, 118_314_495, 6_040_880, 50_144_628, 91_828_987)
    assert begin + acq - dsps - retire == end
    assert (r["available_date"], r["stlm_dt"]) == (date(2026, 3, 10), date(2025, 12, 31))


def test_삼성전자_2018_주식수는_KRX_상장주식수와_같다(chain: Chain) -> None:
    out = chain["shares_outstanding"].out_dir
    assert out is not None
    rows = {str(r["se"]): r for r in _rows(
        out, "corp_code = '00126380' AND bsns_year = '2018'")}
    assert int(str(rows["보통주"]["issued_shr"])) == 5_969_782_550     # 005930 list_shrs 12-28
    assert int(str(rows["우선주"]["issued_shr"])) == 822_886_700       # 005935
    assert rows["보통주"]["treasury_shr"] is None                      # 원장 공란 = 결측
    assert int(str(rows["보통주"]["distributed_shr"])) == 5_969_782_550
    # `se='비고'` 는 값이 전부 결측인 주석 행이지만 모집단에 남는다
    assert rows["비고"]["issued_shr"] is None


def test_파티션은_접수연도_축이다(chain: Chain) -> None:
    for table in ("shares_outstanding", "treasury_stock", "dividend_event"):
        out = chain[table].out_dir
        assert out is not None
        years = sorted(p.name for p in out.iterdir() if p.name.startswith("year="))
        assert (years[0], years[-1]) == ("year=2016", "year=2026"), table
        con = duckdb.connect()
        try:
            n = con.execute(
                "SELECT count(*) FROM read_parquet("
                f"'{out / 'year=*' / '*.parquet'}', hive_partitioning=true) "
                "WHERE year <> CAST(substr(rcept_no, 1, 4) AS INTEGER)").fetchone()
            assert n is not None and int(str(n[0])) == 0, table
        finally:
            con.close()


def test_재빌드는_같은_파티션_해시를_낸다(chain: Chain, tmp_path: Path) -> None:
    """EG5a — 같은 inputs·같은 규칙 판본이면 파티션 content_hash 가 전량 같아야 한다."""
    for table, rule in (("shares_outstanding", SO), ("treasury_stock", TS),
                        ("dividend_event", DE)):
        again = build.build_table(rule, STAGE_SLICE, chain.root, SEED,
                                  build_id=f"b_s16_{table}_again")
        assert again.ok, [(g.name, g.status.value, g.detail) for g in again.gates]
        g = _gate(again, "EG5a")
        assert g.status is GateStatus.PASS, (table, g.detail)
        assert g.metrics["n_changed_partitions"] == 0, table
        assert again.content_hash == chain[table].content_hash, table


# ── 선언 ↔ SQL 대조 ──────────────────────────────────────────────────────────

def test_배당_라벨_대응표가_sql_리터럴과_같다() -> None:
    body = rules_s16.DIVIDEND_SQL.read_text(encoding="utf-8").split("WITH src AS", 1)[1]
    for label in (rules_s16.DPS_LABELS + rules_s16.CASH_TOTAL_LABELS
                  + rules_s16.YIELD_LABELS):
        assert f"p.se = '{label}'" in body, label
    for label, basis in rules_s16.PAYOUT_LABELS:
        assert f"p.se = '{label}'" in body, label
        assert f"'{basis}'" in body, basis
    assert rules_s16.MAPPED_SE_LABELS == (
        rules_s16.DPS_LABELS + rules_s16.CASH_TOTAL_LABELS + rules_s16.YIELD_LABELS
        + tuple(s for s, _ in rules_s16.PAYOUT_LABELS))


def test_EG1_우변은_sql_의_모집단_CTE_를_재사용한다() -> None:
    for rule, path, keys in ((SO, rules_s16.SHARES_SQL, rules_s16.SHARES_GRAIN),
                             (TS, rules_s16.TREASURY_SQL, rules_s16.TREASURY_GRAIN),
                             (DE, rules_s16.DIVIDEND_SQL, rules_s16.DIVIDEND_GRAIN)):
        text = path.read_text(encoding="utf-8")
        assert text.count("-- ==== eg1:") == 1, rule.name
        rhs = rule.eg1_rhs_sql
        assert rhs.startswith(text[:40]), rule.name
        assert rhs.endswith(
            f"SELECT count(*) FROM (SELECT DISTINCT {', '.join(keys)} FROM src)"), rule.name
        assert rule.eg1_lhs_sql == 'SELECT count(*) FROM "out_pq"'


def test_집계행_제외는_shares_와_tesstk_뿐이다() -> None:
    """`stg_dividend` 에는 `row_kind` 열이 없다 — GATES §3-⑱ 우변의 술어는 실행 불가다."""
    assert "row_kind <> 'aggregate'" in rules_s16.SHARES_SQL.read_text(encoding="utf-8")
    assert "row_kind <> 'aggregate'" in rules_s16.TREASURY_SQL.read_text(encoding="utf-8")
    assert "row_kind" not in rules_s16.DIVIDEND_SQL.read_text(
        encoding="utf-8").split("WITH src AS", 1)[1]
    assert "row_kind" not in DE.input_columns["stg_dividend"]


def test_종류_어휘는_S05_대응표를_재사용한다() -> None:
    assert rules_s16.COMMON_KINDS == rules_s05.COMMON_KINDS + rules_s16.SHARES_COMMON_EXTRA
    assert rules_s16.PREFERRED_KINDS == rules_s05.PREFERRED_KINDS
    assert "보통주" in rules_s16.COMMON_KINDS and "보통부" in rules_s16.COMMON_KINDS
    assert "우선주" in rules_s16.PREFERRED_KINDS
    # 4B 원장에만 나오는 개행 포함 표기는 여기서 더한다(stage 가 이 3테이블은 정규화하지 않았다)
    assert rules_s16.SHARES_COMMON_EXTRA == ("의결권 있는 주식\n(보통주)",
                                             "의결권 \n있는 주식\n(보통주)")


def test_격리_어휘와_상수_파티션_선언() -> None:
    for rule in (SO, TS, DE):
        assert rule.reject_reasons == ("rcept_dt_missing",)
        assert rule.partition_class == "receipt_axis"
        assert rule.partition_key_expr == "CAST(substr(rcept_no, 1, 4) AS INTEGER)"
        assert rule.available_basis == ("derived",)
        assert rule.content_date_column == "stlm_dt"
    assert SO.inputs == ("stg_shares", "corp_ticker", "price_daily")
    assert TS.inputs == ("stg_tesstk",)
    assert DE.inputs == ("stg_dividend",)
    assert (SO.consts, TS.consts, DE.consts) == ((), (), ("cash_total_unit_krw",))
    assert SO.grain == ("corp_code", "bsns_year", "reprt_code", "se")
    assert TS.grain == ("corp_code", "bsns_year", "reprt_code", "acqs_mth1", "acqs_mth2",
                        "acqs_mth3", "stock_knd")
    assert DE.grain == ("corp_code", "bsns_year", "reprt_code", "stock_knd")


def test_배당_테이블에는_기준일_락일_축이_없다(chain: Chain) -> None:
    """DESIGN §4-5 '기준일·락일 없음' · §11 'TR 불가(배당락 원천 없음)'."""
    assert not [c for c in DE.columns if "ex_" in c or "record_date" in c]
    assert _metrics(chain["dividend_event"], "EG3_dividend_event")["has_ex_date_axis"] is False
    # S05 는 cash_dividend 어휘만 선언하고 행을 만들지 않으며, S16 도 만들지 않는다
    assert "cash_dividend" in rules_s05.EVENT_TYPE_VOCAB
    assert "cash_dividend" not in rules_s05.MVP_EVENT_TYPES


@pytest.mark.parametrize("table", ["shares_outstanding", "treasury_stock", "dividend_event"])
def test_픽스처는_전부_positive_이고_키가_grain_이다(chain: Chain, table: str) -> None:
    rule = {"shares_outstanding": SO, "treasury_stock": TS, "dividend_event": DE}[table]
    fx = json.loads((Path(rules_s16.__file__).parent / "fixtures"
                     / f"{table}.json").read_text(encoding="utf-8"))
    assert len(fx) >= 10
    assert {f["fixture_class"] for f in fx} == {"positive"}
    assert all(set(f["key"]) == set(rule.grain) for f in fx)
    assert _metrics(chain[table], "EG4")["n_fixtures"] == len(fx)


# ── 손 트리 부정 픽스처 ───────────────────────────────────────────────────────

OBS = date(2026, 9, 1)


def _shares_row(rcept_no: str, se: str, issued: int | None, treasury: int | None,
                distributed: int | None, available: date | None = date(2019, 4, 1),
                row_kind: str = "detail") -> dict[str, object]:
    return {"corp_code": "00000001", "bsns_year": "2018", "reprt_code": "11011", "se": se,
            "rcept_no": rcept_no, "stlm_dt": date(2018, 12, 31), "istc_totqy_shr": issued,
            "tesstk_co_shr": treasury, "distb_stock_co_shr": distributed, "row_kind": row_kind,
            "available_date": available, "available_basis": "derived"}


def _tesstk_row(mth1: str, mth2: str, mth3: str, knd: str, acq: int | None, end: int | None,
                row_kind: str = "detail", rcept_no: str = "20190401000001") -> dict[str, object]:
    return {"corp_code": "00000001", "bsns_year": "2018", "reprt_code": "11011",
            "acqs_mth1": mth1, "acqs_mth2": mth2, "acqs_mth3": mth3, "stock_knd": knd,
            "rcept_no": rcept_no, "stlm_dt": date(2018, 12, 31), "bsis_qy_shr": None,
            "change_qy_acqs_shr": acq, "change_qy_dsps_shr": None,
            "change_qy_incnr_shr": None, "trmend_qy_shr": end, "row_kind": row_kind,
            "available_date": date(2019, 4, 1), "available_basis": "derived"}


def _div_row(se: str, knd: str, thstrm: float | None, rcept_no: str = "20190401000001",
             observed: date = OBS,
             available: date = date(2019, 4, 1)) -> dict[str, object]:
    return {"corp_code": "00000001", "bsns_year": "2018", "reprt_code": "11011", "se": se,
            "stock_knd": knd, "rcept_no": rcept_no, "stlm_dt": date(2018, 12, 31),
            "thstrm": thstrm, "observed_date": observed, "available_date": available,
            "available_basis": "derived"}


def _hand_fixture(tmp_path: Path, key: dict[str, str], column: str, expect: object) -> Path:
    """손 트리에는 절단본 골든 픽스처를 쓸 수 없다 — EG4 는 부재가 실패이므로 1건을 깐다."""
    path = tmp_path / f"hand_{column}.json"
    path.write_text(json.dumps(
        [{"case": "hand", "key": key, "column": column, "expect": expect,
          "source": "hand — 손 트리 부정 픽스처의 EG4 자리를 채운다"}], ensure_ascii=False),
        encoding="utf-8")
    return path


def _build_hand(rule: EquityTable, make_stage_tree, tmp_path: Path, stg_table: str,
                rows: list[dict[str, object]], fixture: Path,
                equity_root: Path | None = None) -> build.BuildResult:
    tree = make_stage_tree(tmp_path, stg_table, rows, partition_class="receipt_axis")
    return build.build_table(rule, tree.stage_root, equity_root or (tmp_path / "equity"),
                             SEED, build_id="b_hand", fixtures_path=fixture,
                             gate_thresholds={"EG7": 1.0})


def _equity_copy(chain: Chain, tmp_path: Path) -> Path:
    """`shares_outstanding` 은 equity 입력 둘을 읽는다 — 지어 둔 체인을 복사해서 쓴다.

    S16 산출은 지운다: 남겨 두면 EG5a 가 손 트리 산출을 절단본 산출과 비교해 폐기한다.
    """
    dst = tmp_path / "equity"
    shutil.copytree(chain.root, dst)
    for t in ("shares_outstanding", "treasury_stock", "dividend_event"):
        shutil.rmtree(dst / t, ignore_errors=True)
    return dst


def test_부정_같은_grain_에_접수가_둘이면_한_행만_낸다(chain: Chain, make_stage_tree,
                                                       tmp_path: Path) -> None:
    """정정 재제출로 grain 이 겹치면 first_write_wins 로 접고 접힌 수를 `n_src_rows` 에 남긴다."""
    rows = [_shares_row("20190401000001", "보통주", 100, 10, 90),
            _shares_row("20190901000001", "보통주", 120, 10, 110,
                        available=date(2019, 9, 1))]
    r = _build_hand(SO, make_stage_tree, tmp_path, "stg_shares", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "se": "보통주"},
                                  "n_src_rows", "2"),
                    equity_root=_equity_copy(chain, tmp_path))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 0)
    assert r.out_dir is not None
    out = _rows(r.out_dir)[0]
    assert (out["rcept_no"], int(str(out["issued_shr"])), out["n_src_rows"]) == (
        "20190401000001", 100, 2)      # 먼저 접수된 판본이 이긴다
    m = _metrics(r, "EG3_shares_outstanding")
    assert (m["n_pool_rows"], m["n_pool_grain"], m["n_collapsed_src_rows"]) == (2, 1, 1)
    assert _metrics(r, "EG1")["rhs"] == 1        # 우변도 grain distinct 라 등식이 선다


def test_부정_접수일_결측은_격리된다(chain: Chain, make_stage_tree, tmp_path: Path) -> None:
    rows = [_shares_row("20190401000001", "보통주", 100, 10, 90),
            _shares_row("20190401000002", "우선주", 50, None, 50, available=None)]
    r = _build_hand(SO, make_stage_tree, tmp_path, "stg_shares", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "se": "보통주"},
                                  "available_basis", "derived"),
                    equity_root=_equity_copy(chain, tmp_path))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert r.out_dir is not None
    assert _rejects(r.out_dir, "se") == [("우선주", "rcept_dt_missing")]
    m = _metrics(r, "EG1")
    assert (m["lhs"], m["rhs"], m["n_reject"], m["delta"]) == (1, 2, 1, 0)


def test_부정_집계행을_모집단에_넣으면_EG1이_깨진다(chain: Chain, make_stage_tree,
                                                    tmp_path: Path) -> None:
    """`se='합계'` 를 남기면 좌변만 늘고 우변(모집단 CTE)은 그대로라 등식이 어긋난다."""
    variant_sql = tmp_path / "shares_variant.sql"
    variant_sql.write_text(
        rules_s16.SHARES_SQL.read_text(encoding="utf-8").replace(
            "WHERE row_kind <> 'aggregate'", "WHERE TRUE"), encoding="utf-8")
    rule = EquityTable(**{**SO.__dict__, "name": "shares_variant", "sql_path": variant_sql})
    rows = [_shares_row("20190401000001", "보통주", 100, 10, 90),
            _shares_row("20190401000001", "합계", 100, 10, 90, row_kind="aggregate")]
    r = _build_hand(rule, make_stage_tree, tmp_path, "stg_shares", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "se": "보통주"},
                                  "n_src_rows", "1"),
                    equity_root=_equity_copy(chain, tmp_path))
    assert r.status is build.BuildStatus.GATE_FAILED
    eg1 = _gate(r, "EG1")
    assert eg1.status is GateStatus.FAIL
    assert (eg1.metrics["lhs"], eg1.metrics["rhs"], eg1.metrics["delta"]) == (2, 1, 1)


def test_부정_원장에_없는_값을_지어내면_EG3가_폐기한다(chain: Chain, make_stage_tree,
                                                       tmp_path: Path) -> None:
    """`n_row_not_in_stage` — 산출 행은 전부 stage 축으로 되짚어져야 한다."""
    variant_sql = tmp_path / "shares_fabricated.sql"
    variant_sql.write_text(
        rules_s16.SHARES_SQL.read_text(encoding="utf-8").replace(
            "s.istc_totqy_shr                                             AS issued_shr",
            "s.istc_totqy_shr + 1                                         AS issued_shr"),
        encoding="utf-8")
    rule = EquityTable(**{**SO.__dict__, "name": "shares_fabricated", "sql_path": variant_sql})
    r = _build_hand(rule, make_stage_tree, tmp_path, "stg_shares",
                    [_shares_row("20190401000001", "보통주", 100, 10, 90)],
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "se": "보통주"},
                                  "issued_shr", "101"),
                    equity_root=_equity_copy(chain, tmp_path))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_shares_outstanding")
    assert g.status is GateStatus.FAIL and g.metrics["n_row_not_in_stage"] == 1


def test_부정_소계를_잎으로_더하면_원장_총계와_어긋난다(make_stage_tree,
                                                       tmp_path: Path) -> None:
    """'소계' 를 합산 축에서 빼지 않았다면 이 원장에서 취득 합이 두 배가 된다."""
    rows = [_tesstk_row("배당가능이익범위 이내 취득", "직접취득", "장내직접취득", "보통주", 10, 10),
            _tesstk_row("배당가능이익범위 이내 취득", "직접취득", "소계", "보통주", 10, 10),
            _tesstk_row("총계", "총계", "총계", "보통주", 10, 10, row_kind="aggregate")]
    r = _build_hand(TS, make_stage_tree, tmp_path, "stg_tesstk", rows,
                    _hand_fixture(tmp_path,
                                  {"corp_code": "00000001", "bsns_year": "2018",
                                   "reprt_code": "11011",
                                   "acqs_mth1": "배당가능이익범위 이내 취득",
                                   "acqs_mth2": "직접취득", "acqs_mth3": "장내직접취득",
                                   "stock_knd": "보통주"}, "acquired_shr", "10"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)          # 총계 1행은 모집단 밖
    m = _metrics(r, "EG3_treasury_stock")
    assert (m["n_ledger_total_groups"], m["n_ledger_total_mismatch"]) == (1, 0)
    assert m["n_subtotal_rows"] == 1


def test_부정_원장_총계가_잎_합과_다르면_기록형이_잡는다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    rows = [_tesstk_row("배당가능이익범위 이내 취득", "직접취득", "장내직접취득", "보통주", 10, 10),
            _tesstk_row("총계", "총계", "총계", "보통주", 99, 10, row_kind="aggregate")]
    r = _build_hand(TS, make_stage_tree, tmp_path, "stg_tesstk", rows,
                    _hand_fixture(tmp_path,
                                  {"corp_code": "00000001", "bsns_year": "2018",
                                   "reprt_code": "11011",
                                   "acqs_mth1": "배당가능이익범위 이내 취득",
                                   "acqs_mth2": "직접취득", "acqs_mth3": "장내직접취득",
                                   "stock_knd": "보통주"}, "acquired_shr", "10"))
    # 원장 품질 신호이지 산출 버그가 아니다 — 폐기하지 않고 기록만 한다(GATES §0-1)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    m = _metrics(r, "EG3_treasury_stock")
    assert (m["n_ledger_total_groups"], m["n_ledger_total_mismatch"]) == (1, 1)


def test_부정_같은_라벨이_둘이면_값_있는_행을_집는다(make_stage_tree, tmp_path: Path) -> None:
    """절단본 60그룹의 모양 — `stock_knd='-'` 에 같은 `se` 가 두 번, 한쪽만 값이 있다."""
    rows = [_div_row("주당 현금배당금(원)", "-", None),
            _div_row("주당 현금배당금(원)", "-", 600.0),
            _div_row("현금배당금총액(백만원)", "-", 1.0)]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "-"},
                                  "n_src_rows", "3"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 0)
    assert r.out_dir is not None
    out = _rows(r.out_dir)[0]
    assert float(str(out["dps_krw"])) == 600.0        # NULL 을 먼저 집으면 값이 사라진다
    assert float(str(out["cash_total_krw"])) == 1_000_000.0
    m = _metrics(r, "EG3_dividend_event")
    assert (m["n_collapsed_grain_se"], m["n_value_conflict"]) == (1, 0)


def test_부정_같은_라벨에_다른_값이_둘이면_기록형이_잡는다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    rows = [_div_row("주당 현금배당금(원)", "-", 500.0),
            _div_row("주당 현금배당금(원)", "-", 600.0)]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "-"},
                                  "dps_krw", "500.0"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    m = _metrics(r, "EG3_dividend_event")
    assert m["n_value_conflict"] == 1
    # 정렬이 결정적이라(값 오름차순) 산출은 항상 500 이다 — EG5a 재현성의 근거
    assert r.out_dir is not None
    assert float(str(_rows(r.out_dir)[0]["dps_krw"])) == 500.0


def test_부정_한_grain_에_접수가_둘이면_식별축은_나중_접수를_싣는다(make_stage_tree,
                                                                     tmp_path: Path) -> None:
    """정정 재제출로 라벨마다 판본이 갈리면 PIT 축은 기여 행 중 가장 나중 것이어야 한다."""
    rows = [_div_row("주당 현금배당금(원)", "-", 600.0, rcept_no="20190401000001",
                     observed=date(2026, 8, 1), available=date(2019, 4, 1)),
            _div_row("현금배당금총액(백만원)", "-", 1.0, rcept_no="20190901000001",
                     observed=date(2026, 9, 1), available=date(2019, 9, 1))]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "-"},
                                  "rcept_no", "20190901000001"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 0)
    assert r.out_dir is not None
    out = _rows(r.out_dir)[0]
    assert out["available_date"] == date(2019, 9, 1)   # 어느 기여 행보다도 이르지 않다
    assert float(str(out["dps_krw"])) == 600.0         # 값 축은 라벨마다 따로 고른다
    assert float(str(out["cash_total_krw"])) == 1_000_000.0
    assert _metrics(r, "EG3_dividend_event")["n_grain_multi_rcept"] == 1


def test_부정_배당성향_라벨_우선순위와_basis(make_stage_tree, tmp_path: Path) -> None:
    rows = [_div_row("(연결)현금배당성향(%)", "-", 21.9),
            _div_row("(별도)현금배당성향(%)", "-", 30.0)]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "-"},
                                  "payout_basis", "consolidated"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    out = _rows(r.out_dir)[0]
    assert (float(str(out["payout_pct"])), out["payout_basis"]) == (21.9, "consolidated")


def test_부정_법인_축_값이_종류_행에_실리면_기록형이_잡는다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    """원장이 총액을 '보통주' 로 적어 오면 종류 행에 법인 축 값이 실린다 — 서식 신호."""
    rows = [_div_row("현금배당금총액(백만원)", "보통주", 1.0),
            _div_row("주당 현금배당금(원)", "보통주", 600.0)]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "보통주"},
                                  "dps_krw", "600.0"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    m = _metrics(r, "EG3_dividend_event")
    assert m["n_firm_value_on_class_row"] == 1


def test_부정_미대응_se_라벨은_기록만_되고_행을_지우지_않는다(make_stage_tree,
                                                             tmp_path: Path) -> None:
    """'주당 주식배당(주)'·'주당액면가액(원)' 등은 아직 컬럼이 없다 — 모집단에는 남는다."""
    rows = [_div_row("주당 주식배당(주)", "보통주", 0.1),
            _div_row("주당액면가액(원)", "-", 100.0)]
    r = _build_hand(DE, make_stage_tree, tmp_path, "stg_dividend", rows,
                    _hand_fixture(tmp_path, {"corp_code": "00000001", "bsns_year": "2018",
                                             "reprt_code": "11011", "stock_knd": "보통주"},
                                  "dps_krw", None))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)
    m = _metrics(r, "EG3_dividend_event")
    assert m["n_unmapped_se_rows"] == 2
    assert m["unmapped_se_labels"] == ["주당 주식배당(주)", "주당액면가액(원)"]
