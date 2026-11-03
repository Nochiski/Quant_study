"""S27 `fin_prelim` — 잠정실적 공정공시 판본(N-64 PR ②-1). 합성 stage 2표 위 실제 `build_table` 왕복.

stage `stg_doc_prelim`(파서가 원 단위로 바꾼 서식 값) ⋈ `stg_disclosure`(종목 · 접수일). 행은 버리지 않고
`prelim_status` 로 가른다 — fi 가 확정 > 잠정 · 정정 접기 · 공개일 자르기를 하고, 거른 꼴을 센다(PG-10).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s27
from equity.baseline import Baseline
from equity.gates import GateStatus
from equity.model import RULES

SNAP = "snap_20261110T000000Z"
_NUM = ("revenue_krw", "op_profit_krw", "pretax_krw", "net_income_krw", "ni_owner_krw",
        "revenue_py_krw", "op_profit_py_krw", "net_income_py_krw")
_TYPES = {"period_from": "DATE", "period_to": "DATE", "py_from": "DATE", "py_to": "DATE",
          "available_date": "DATE", **{c: "DECIMAL(20,0)" for c in _NUM}}


def _prelim(rno: str, *, frm: str = "2026-07-01", to: str = "2026-09-30", status: str = "ok",
            member: str = "m.xml", fs_div: str = "CFS", rev: int | None = 1_237_395_000_000,
            avail: str | None = None) -> dict[str, object]:
    vals: dict[str, object] = {c: None for c in _NUM}
    if status == "ok":
        vals |= {"revenue_krw": rev, "op_profit_krw": 130_521_000_000, "revenue_py_krw": 1_113_746_000_000}
    return {"rcept_no": rno, "member_name": member, "fs_div": fs_div, "period_from": frm, "period_to": to,
            "py_from": "2025-07-01", "py_to": "2025-09-30", "unit_raw": "백만원", **vals,
            "status": status, "error": None if status == "ok" else "unit:조원",
            "available_date": avail or f"{rno[:4]}-{rno[4:6]}-{rno[6:8]}"}


def _disc(rno: str, *, ticker: str = "073240", corr: bool = False, observed: str = "2026-10-30",
          avail: str | None = None) -> dict[str, object]:
    d = f"{rno[:4]}-{rno[4:6]}-{rno[6:8]}"
    return {"rcept_no": rno, "rcept_dt": d, "available_date": avail or d, "corp_code": "00106463",
            "ticker": ticker, "is_correction": corr, "observed_date": observed}


PRELIM = [
    _prelim("20261029800635"),                                            # 3Q 연결 — ok
    _prelim("20261031800100", rev=1_240_000_000_000),                      # 같은 분기 정정 — 판본 행 둘
    _prelim("20260901800200", frm="2026-08-01", to="2026-08-31"),          # 월 실적
    _prelim("20260905800300", frm="2026-07-01", to="2026-09-30"),          # 기말(09-30) > 접수일(09-05)
    _prelim("20261102800417", status="failed"),                            # 조원 실패(p1.6 꼴)
    _prelim("20261020800500"),                                             # 접수 목록에 없음
    _prelim("20261028800600", member="a.xml", status="failed"),            # 멤버 둘 — ok 멤버가 이긴다
    _prelim("20261028800600", member="b.xml"),
    _prelim("20261027800700", avail="2026-10-28"),                         # 서식 쪽 공개일이 늦다
]
DISC = [
    _disc("20261029800635"), _disc("20261029800635", observed="2026-10-31"),   # append_only 재수집 판본
    _disc("20261031800100", corr=True), _disc("20260901800200"), _disc("20260905800300"),
    _disc("20261102800417", ticker="005490"), _disc("20261028800600", ticker="000660"),
    _disc("20261027800700", ticker="005380"),
]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, ) -> tuple[Path, build.BuildResult]:
    from conftest import _make_stage_tree   # 모듈 스코프에서 쓰려고 함수를 직접 부른다
    tmp = tmp_path_factory.mktemp("s27")
    _make_stage_tree(tmp, "stg_doc_prelim", PRELIM, partition_class="receipt_axis", snapshot_id=SNAP,
                     types=_TYPES)
    _make_stage_tree(tmp, "stg_disclosure", DISC, partition_class="receipt_axis", snapshot_id=SNAP,
                     types={"rcept_dt": "DATE", "available_date": "DATE", "observed_date": "DATE"})
    fx = tmp / "fin_prelim.json"
    fx.write_text(json.dumps([{"id": "FX-27-001", "key": {"rcept_no": "20261029800635"},
                               "column": "revenue_krw", "expect": "1237395000000",
                               "source": "합성 — 금호타이어 3Q26 1,237,395백만원 꼴"}]), encoding="utf-8")
    r = build.build_table(rules_s27.FIN_PRELIM, tmp / "stage", tmp / "equity", Baseline({}),
                          build_id="b_s27", fixtures_path=fx)
    return tmp / "equity", r


def _rows(equity_root: Path) -> dict[str, dict[str, object]]:
    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{equity_root}/fin_prelim/**/*.parquet', "
                      "hive_partitioning=false)")
    cols = [d[0] for d in rel.description]
    return {str(r[0]): dict(zip(cols, r, strict=True)) for r in rel.fetchall()}


def test_등록_순서는_fin_std_뒤_dataset_profile_앞() -> None:
    order = [ln.strip() for ln in (Path(__file__).parent.parent / "scripts" / "equity_order.txt")
             .read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert RULES["fin_prelim"] is rules_s27.FIN_PRELIM
    assert order.index("fin_std") < order.index("fin_prelim") < order.index("dataset_profile")


def test_게이트_전부_통과_접수번호당_한_행(built: tuple[Path, build.BuildResult]) -> None:
    equity_root, r = built
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert {g.name: g.status for g in r.gates}["EG3_fin_prelim"] is GateStatus.PASS
    rows = _rows(equity_root)
    assert len(rows) == len({p["rcept_no"] for p in PRELIM}) == 8       # 멤버 둘 · 재수집 판본이 접힌다


def test_상태로_가르고_버리지_않는다(built: tuple[Path, build.BuildResult]) -> None:
    rows = _rows(built[0])
    assert {k: v["prelim_status"] for k, v in rows.items()} == {
        "20261029800635": "ok", "20261031800100": "ok", "20260901800200": "not_quarter",
        "20260905800300": "future_period", "20261102800417": "parse_failed",
        "20261020800500": "no_disclosure", "20261028800600": "ok", "20261027800700": "ok"}


def test_값은_원_단위_그대로_정정은_판본_행이_따로(built: tuple[Path, build.BuildResult]) -> None:
    rows = _rows(built[0])
    a, c = rows["20261029800635"], rows["20261031800100"]
    assert (a["ticker"], a["rcept_dt"], a["is_correction"], a["fs_div"]) == ("073240", date(2026, 10, 29), False, "CFS")
    assert (a["period_from"], a["period_to"]) == (date(2026, 7, 1), date(2026, 9, 30))
    assert (a["revenue_krw"], a["revenue_py_krw"]) == (Decimal(1_237_395_000_000), Decimal(1_113_746_000_000))
    assert (c["is_correction"], c["revenue_krw"]) == (True, Decimal(1_240_000_000_000))
    f = rows["20261102800417"]
    assert f["revenue_krw"] is None and f["parse_error"] == "unit:조원"


def test_공개일은_두_입력_중_늦은_쪽(built: tuple[Path, build.BuildResult]) -> None:
    rows = _rows(built[0])
    assert rows["20261027800700"]["available_date"] == date(2026, 10, 28)
    assert rows["20261029800635"]["available_date"] == date(2026, 10, 29)
    assert rows["20261020800500"]["available_date"] == date(2026, 10, 20)   # 목록 없음 — 서식 쪽 공개일
