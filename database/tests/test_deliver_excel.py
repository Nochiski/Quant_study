"""전달층 엑셀(`src/deliver/`, 플랜 M2 W2 T2.5b · MODEL_EXCEL_SPEC) — 합성 판으로 검증.

합성 세계: ISO 2026-W39(월 09-21 ~ 금 09-25, 수 09-23 휴장 = 판 없음) + 지난주 목·금(09-17·18).
종목 45(대분류 G45 15 · G10 10 · G20 10 · G30 5 · G40 5). 금요일 순위는 G45 가 1~15위라
업종 상한 9 가 걸린다. model 판·factor_inputs 판은 고정 계약 경로에 parquet 로 쓴다.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import statistics
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from deliver import __main__ as cli
from deliver.common import cap_candidates, yoy
from deliver.excel_daily import build_daily, model_label
from deliver.excel_weekly import build_weekly, prev_week, week_days
from deliver.qpack import Col, fit_width
from deliver.reader import DeliverError, find_run
from deliver.stats import quantile
from deliver.view import load_day
from model import registry
from model.contracts import FI_TABLES, score_columns
from openpyxl import load_workbook
from openpyxl.formatting.rule import ColorScale
from stage import manifest

W39 = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
HOLIDAY = "2026-09-23"
W38 = ["2026-09-17", "2026-09-18"]
FRI, THU, MON, TUE = "2026-09-25", "2026-09-24", "2026-09-21", "2026-09-22"
PRIMARY = "v4_rank@0.1"
SPECS = ("v2_percentrank@1.0", "v3_zscore@1.0", "v4_rank@0.1", "v4_rank@0.2")
N = 45
SECTOR_NAMES = {"G45": "IT", "G10": "에너지", "G20": "산업재", "G30": "필수소비재", "G40": "금융"}
FORMULA_NAME = '=HYPERLINK("http://x")'
# fi_universe 추정기관수(최근 3개월 투자의견 증권사 수) — 비고 열 대조용. 나머지 종목은 5명.
ANALYSTS = {1: 0, 2: 2, 3: 3, 4: None}
TOKEN, CHAT = "123456:SECRET-TOKEN-abc", "-100987654321"
PA = {"VARCHAR": pa.string(), "DATE": pa.date32(), "BIGINT": pa.int64(),
      "INTEGER": pa.int32(), "DOUBLE": pa.float64(), "BOOLEAN": pa.bool_()}


def tick(i: int) -> str:
    return f"{i + 1:06d}"


def sector(i: int) -> str:
    return ("G45" if i < 15 else "G10" if i < 25 else "G20" if i < 35 else "G30" if i < 40
            else "G40")


BASE_ORDER = [*range(0, 40), 44]


def order_for(day: str) -> list[int]:
    o = list(BASE_ORDER)
    if day == MON:                      # 월: 38 이 1위(월만 ●)
        o.remove(38)
        o.insert(0, 38)
    if day == THU:                      # 목: 0·1 자리 바꿈 → 금 Δ순위 +1
        o[0], o[1] = o[1], o[0]
    if day in W38:                      # 지난주: 34·39 가 맨 앞
        o = [34, 39] + [x for x in o if x not in (34, 39)]
    return o


def excluded_for(day: str) -> dict[int, str]:
    ex = {40: "admin", 41: "adv20", 42: "pull_gate", 43: "insufficient_data"}
    if day == TUE:
        ex[44] = "pull_gate"            # 화: 44 제외 → ✕
    return ex


def ret_of(i: int) -> float:
    return (i % 7 - 3) / 100


# ── parquet 쓰기 ───────────────────────────────────────────────────────────────
def _write(path: Path, schema: pa.Schema, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    pq.write_table(pa.table(cols, schema=schema), path)


def _d(v: object) -> object:
    return date.fromisoformat(v) if isinstance(v, str) else v


def write_fi(root: Path, table: str, bid: str, rows: list[dict[str, object]]) -> None:
    c = FI_TABLES[table]
    schema = pa.schema([(col.name, PA[col.dtype]) for col in c.columns])
    fixed = [{k: (_d(v) if dict(zip(c.column_names, (x.dtype for x in c.columns),
                                    strict=True)).get(k) == "DATE" else v)
              for k, v in r.items()} for r in rows]
    _write(root / table / f"v={bid}" / "part0.parquet", schema, fixed)


def _score_schema(cols: tuple[str, ...]) -> pa.Schema:
    text = {"ticker", "stock_code", "score_date", "spec_id", "exclude_reason", "sector_l1",
            "sector_l2", "coverage_state"}
    fields = []
    for c in cols:
        if c in text or c.endswith("_flag"):
            fields.append((c, pa.string()))
        elif c in ("rank", "n_buckets_used"):
            fields.append((c, pa.int64()))
        elif c == "excluded":
            fields.append((c, pa.bool_()))
        else:
            fields.append((c, pa.float64()))
    return pa.schema(fields)


IND_SCHEMA = pa.schema([("ticker", pa.string()), ("score_date", pa.string()),
                        ("spec_id", pa.string()), ("key", pa.string()), ("bucket", pa.string()),
                        ("role", pa.string()), ("raw", pa.float64()), ("pct", pa.float64()),
                        ("flag", pa.string())])


# ── 합성 판 ────────────────────────────────────────────────────────────────────
def _raw(i: int, key: str) -> tuple[float | None, str | None]:
    """지표 원값·flag — 표식·결측 사례를 종목에 심는다."""
    if key.startswith("REV_") and i == 3:
        return None, "커버리지<3"
    if key == "REV_OP_1M" and i == 1:
        return 1.5, "흑전"
    if key == "REV_OP_1M" and i == 2:
        return -0.5, "적전"
    if key == "EP":
        return (i - 5) * 0.01, None
    if key == "DY0" and i % 4 == 0:
        return 0.0, "무배당"
    if key == "R1M" and i == 4:
        return 50.0, None               # 5000% — 윈저라이즈 대상
    if key == "OPM_TTM" and i == 6:
        return None, "원천없음"
    return 0.01 * ((i * 7 + len(key)) % 29) - 0.05, None


def write_model_day(model_root: Path, day: str, fi_bid: str, *, status: str = "ok") -> str:
    bid = f"m_{day.replace('-', '')}T000000Z"
    order, excl = order_for(day), excluded_for(day)
    ranked = [i for i in order if i not in excl]
    rank = {i: k + 1 for k, i in enumerate(ranked)}
    specs: dict[str, dict[str, object]] = {}
    for sid in SPECS:
        spec = registry.get(sid)
        cols = score_columns(spec)
        rows: list[dict[str, object]] = []
        inds: list[dict[str, object]] = []
        if spec.engine == "v4_rank":
            buckets = list(spec.buckets)
            local = dict(rank)
            if sid == "v4_rank@0.2":            # 동일가중판은 0·2 순위를 바꾼다
                local[0], local[2] = rank[2], rank[0]
            for i in range(N):
                reason = excl.get(i)
                d13 = reason in ("admin", "adv20")
                r = None if reason else local.get(i)
                comp = None if d13 else 100.0 - 2.0 * (local.get(i) or 44)
                bs: dict[str, float | None] = {}
                for k, b in enumerate(buckets):
                    bs[b] = None if d13 or comp is None else max(0.0, min(100.0, comp + k - 2.5))
                if i == 3:
                    bs["revision"] = None
                if i == 43:
                    bs = {b: (v if b in ("low_risk", "value") else None) for b, v in bs.items()}
                rows.append({"ticker": tick(i), "score_date": day, "spec_id": sid, "rank": r,
                             "composite": comp, "excluded": reason is not None,
                             "exclude_reason": reason, "sector_l1": sector(i),
                             "sector_l2": sector(i) + "10",
                             "coverage_state": "grace" if i == 5 else "fresh",
                             "n_buckets_used": sum(v is not None for v in bs.values()),
                             **{f"{b}_score": v for b, v in bs.items()}})
                if d13:
                    continue
                for ind in spec.indicators:
                    raw, flag = _raw(i, ind.key)
                    inds.append({"ticker": tick(i), "score_date": day, "spec_id": sid,
                                 "key": ind.key, "bucket": ind.bucket, "role": ind.role,
                                 "raw": raw, "pct": None, "flag": flag})
            for ind in spec.indicators:          # 점수 지표 백분위 = 원값 순위(합성)
                if ind.role != "score":
                    continue
                mine = sorted((r for r in inds if r["key"] == ind.key and r["raw"] is not None),
                              key=lambda r: (r["raw"], r["ticker"]))
                for k, r in enumerate(mine):
                    r["pct"] = 100.0 * k / (len(mine) - 1)
        else:
            key = "stock_code"
            comp_col = "composite_score" if spec.engine == "v3_zscore" else "total_score"
            universe = [*reversed(range(40)), 45] if spec.engine == "v3_zscore" else BASE_ORDER
            for k, i in enumerate(universe, start=1):
                rows.append({key: tick(i), "score_date": day, "rank": k,
                             comp_col: 100.0 - k})
        d = model_root / sid / f"v={bid}"
        _write(d / "scores.parquet", _score_schema(cols), rows)
        if inds:
            _write(d / "indicators.parquet", IND_SCHEMA, inds)
        n_ranked = sum(1 for r in rows if r.get("rank") is not None)
        specs[sid] = {"n_scores": len(rows), "n_ranked": n_ranked,
                      "n_excluded": len(rows) - n_ranked,
                      "gates": {"MG0": {"status": "pass"}, "MG1": "pass"}}
    meta = {"layer": "model", "status": status, "build_id": bid, "date": day,
            "basis": "morning", "fi_build_id": fi_bid, "generated_at": f"{day}T00:00:00Z",
            "specs": specs, "primary_spec": PRIMARY}
    runs = model_root / "_runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{day.replace('-', '')}_morning.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if status == "ok":
        (model_root / "latest_morning.json").write_text(json.dumps(meta), encoding="utf-8")
    return bid


def write_fi_day(fi_root: Path, day: str,
                 edit: Callable[[str, list[dict[str, object]]], None] | None = None) -> str:
    """합성 fi 판 하나. `edit(표 이름, 행 목록)` 으로 쓰기 직전에 표마다 행을 고칠 수 있다."""
    bid = f"m_{day.replace('-', '')}T010000Z"
    uni, prices, adj, flows, fins, cons, cons_a = [], [], [], [], [], [], []
    for i in range(N + 1):
        t = tick(i)
        uni.append({"ticker": t, "date": day, "name": FORMULA_NAME if i == 7 else f"종목{i}",
                    "market": "KOSPI" if i % 2 else "KOSDAQ", "sec_type": "common",
                    "shares": 1_000_000, "market_cap": 1000.0 + 100 * i, "mktcap_basis": "krx",
                    "sector_l1": sector(i), "sector_l1_name": SECTOR_NAMES[sector(i)],
                    "sector_l2": sector(i) + "10",
                    "sector_l2_name": SECTOR_NAMES[sector(i)] + " 중분류",
                    "has_estimates": i != N, "coverage_state": "grace" if i == 5 else "fresh",
                    "coverage_age_days": 2 if i == 5 else 0, "n_analysts": ANALYSTS.get(i, 5), "adv20": 50.0,
                    "is_admin": i == 40, "is_halted": False, "audit_adverse": False,
                    "filing_late": False, "eligible": i != N, "exclude_reason": None})
        for dd, px, ok in ((W38[1], 100.0, True),
                           (day, 100.0 * (1 + ret_of(i)), i != 35)):
            prices.append({"ticker": t, "date": dd, "close": int(10_000 + i * 100),
                           "price_source": "krx"})
            adj.append({"ticker": t, "date": dd, "adj_close": px, "adj_factor": 1.0,
                        "adj_ok": ok})
        if i != 10:
            flows.append({"ticker": t, "date": day, "foreign_investor": 1.0})
        op24, op25, ni24, ni25 = 100.0 + i, 120.0 + i, 50.0, 60.0
        if i == 1:
            op24, op25 = -10.0, 20.0
        if i == 2:
            ni24, ni25 = 30.0, -5.0
        if i == 3:
            op24, op25 = -10.0, -20.0
        for per, rev, op, ni in (("2024/12", 1000.0 + 10 * i, op24, ni24),
                                 ("2025/12", 1100.0 + 10 * i, op25, ni25)):
            fins.append({"ticker": t, "period": per, "period_type": "annual", "revenue": rev,
                         "op": op, "ni": ni, "roe": 5.0 + i, "roa": 2.0, "debt_ratio": 80.0,
                         "available_date": "2026-03-15" if per == "2025/12" else "2025-03-15"})
            # 컨센서스 연간 표 확정 행 — 합성 세계는 지배 순이익 = 연결 순이익(두 정의가 갈리는 경우 ·
            # 실제 창(Y−1~Y+1) 밖 Y−2 를 stage 에서 읽는 경우는 `_controlling_ni` 테스트)
            cons_a.append({"ticker": t, "period": per, "data_type": "A", "revenue": rev, "op": op,
                           "ni": ni, "fetched_date": "2026-09-24"})
        for k, per in enumerate(("2025/06", "2025/09", "2025/12", "2026/03", "2026/06")):
            op = 10.0 + k
            if i == 1:
                op = -5.0 if k == 0 else 8.0
            fins.append({"ticker": t, "period": per, "period_type": "quarter",
                         "revenue": 250.0, "op": op, "available_date": "2026-08-14"})
        for per, h, eps, ni in (("2026/12", "cur", 1200.0, 70.0), ("2026/12", "1m", 1000.0, 65.0),
                                ("2027/12", "cur", 1400.0, 80.0)):
            if i == 2 and h == "cur" and per == "2026/12":
                ni = -3.0
            if i == 4 and h == "1m":
                eps = -100.0
            cons.append({"ticker": t, "target_period": per, "horizon": h,
                         "revenue": 1200.0 + 10 * i, "op": 130.0 + i, "ni": ni, "eps": eps,
                         "per": 12.5, "pbr": 1.1, "n_analysts": 5, "obs_date": "2026-09-24",
                         "fetched_date": "2026-09-24"})
    for table, rows in (("fi_universe", uni), ("fi_prices", prices), ("fi_adj_prices", adj),
                        ("fi_flows", flows), ("fi_fin_summary", fins), ("fi_consensus", cons),
                        ("fi_credit", []), ("fi_consensus_annual", cons_a)):
        if edit is not None:
            edit(table, rows)
        write_fi(fi_root, table, bid, rows)
    return bid


@pytest.fixture(scope="module", autouse=True)
def _pin_ql_home(tmp_path_factory: pytest.TempPathFactory):
    """QL_HOME 을 빈 임시 폴더로 고정 — 개발 머신·서버의 실제 QL_HOME 이 테스트에 새지 않게."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("QL_HOME", str(tmp_path_factory.mktemp("ql_home")))
        yield


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("deliver")
    model_root, fi_root, out_root = root / "model", root / "fi", root / "deliver"
    for day in [*W38, *[d for d in W39 if d != HOLIDAY]]:
        write_model_day(model_root, day, write_fi_day(fi_root, day))
    return {"model": model_root, "fi": fi_root, "out": out_root}


@pytest.fixture(scope="module")
def daily(world: dict[str, Path]):
    res = build_daily(FRI, "morning", model_root=world["model"], fi_root=world["fi"],
                      out_root=world["out"])
    return res, load_workbook(res.path)


@pytest.fixture(scope="module")
def weekly(world: dict[str, Path]):
    res = build_weekly("2026-W39", model_root=world["model"], fi_root=world["fi"],
                       out_root=world["out"])
    return res, load_workbook(res.path)


def test_normal_style_font_is_arial_10_like_qpack(daily, weekly) -> None:
    """엑셀은 행 번호를 표준 스타일 글꼴로 그린다 — openpyxl 기본 Calibri 11 이면
    11.25pt 행에서 잘린다. 큐팩 원본처럼 글꼴 0번과 표준 스타일이 Arial 10 이어야
    한다(10-06 사용자 지적)."""
    for _, book in (daily, weekly):
        normal = book._named_styles["Normal"].font
        assert (normal.name, normal.sz) == ("Arial", 10.0)
        assert (book._fonts[0].name, book._fonts[0].sz) == ("Arial", 10.0)
        first = book.worksheets[0]
        # 데이터 칸은 그대로 맑은 고딕 8
        assert {c.font.name for c in first[8] if c.value is not None} == {"맑은 고딕"}


def header(ws) -> dict[str, int]:
    """7행 열 이름 → 열 번호(줄바꿈은 공백으로)."""
    return {str(c.value).replace("\n", " "): c.column for c in ws[7] if c.value is not None}


def rows_by_code(ws) -> dict[str, int]:
    """코드 → 행(빈 A 칸은 뺀다 — 확정 양식은 표 끝 선을 마지막 행 다음 행에 둔다)."""
    return {str(ws.cell(r, 1).value): r for r in range(8, ws.max_row + 1)
            if ws.cell(r, 1).value is not None}


def col_of(ws, label: str, occurrence: int = 0) -> int:
    hits = [c.column for c in ws[7] if str(c.value).replace("\n", " ") == label]
    return hits[occurrence]


def group_col(ws, group: str, label: str) -> int:
    """6행 그룹 이름이 group 으로 시작하는 블록 안에서 7행 머리가 label 인 열 — 확정 양식은 그룹에
    있는 말(버킷 이름 등)을 머리에서 되풀이하지 않아 같은 머리('유니버스 (Z)')가 블록마다 있다."""
    start = next(c.column for c in ws[6] if str(c.value or "").startswith(group))
    c = start
    while ws.cell(7, c).value is not None and (c == start or ws.cell(6, c).value is None):
        if str(ws.cell(7, c).value).replace("\n", " ") == label:
            return c
        c += 1
    raise KeyError((group, label))


# ── 순수 함수 ──────────────────────────────────────────────────────────────────
def test_yoy_sign_flags_replace_growth() -> None:
    assert yoy(20.0, -10.0) == (None, "흑자전환")
    assert yoy(-5.0, 30.0) == (None, "적자전환")
    assert yoy(-20.0, -10.0) == (None, "적자지속")
    assert yoy(5.0, 0.0) == (None, "흑자전환")              # 이전값 0 도 부호 전환
    assert yoy(-5.0, 0.0) == (None, "적자전환")
    assert yoy(110.0, 100.0) == (pytest.approx(10.0), None)  # 흑자지속은 숫자
    assert yoy(None, 1.0) == (None, None)


def test_cap_candidates_limits_per_sector() -> None:
    rows = [{"t": i, "s": "A" if i < 12 else f"S{i % 4}"} for i in range(60)]
    got = cap_candidates(rows, 30, 9, lambda r: r["s"])
    assert len(got) == 30
    assert sum(1 for r in got if r["s"] == "A") == 9
    assert [r["t"] for r in got[:10]] == [*range(9), 12]         # 10~11번째 A 는 건너뛴다
    assert len(cap_candidates(rows[:12], 30, 9, lambda r: r["s"])) == 9


def test_week_helpers() -> None:
    assert week_days("2026-W39") == W39
    assert prev_week("2026-W39") == "2026-W38"
    assert prev_week("2027-W01") == "2026-W53"
    with pytest.raises(DeliverError):
        week_days("2026-39")


# ── 매일 엑셀 ─────────────────────────────────────────────────────────────────
def test_daily_sheets_and_row_counts(daily) -> None:
    res, wb = daily
    assert res.path.name == "model_scores_20260925_morning.xlsx"
    assert res.path.parent.name == "daily"
    # 확정 양식 순서(샘플 22 · 30) — 지표(표시용) 시트는 뺐다(10-11 사용자). 새 시트 여섯은 이
    # 합성 세계에 equity · stage 원천이 없어 '데이터 없음' 한 줄로 선다(test_deliver_extra_sheets)
    assert wb.sheetnames == ["Score", "Sector", "Earnings", "Analyst", "Events", "Market", "ETF",
                             "Flow", "Short", "Compare", "Raw", "Meta", "trend"]
    assert wb["ETF"]["A6"].value.startswith("데이터 없음: ")
    assert wb["trend"].sheet_state == "hidden"                 # 그래프 원자료(숨김)
    assert res.sheets == wb.sheetnames[:-1]                         # 보이는 시트만 알린다
    for name in ("Score", "Raw", "Earnings", "Compare"):
        ws = wb[name]
        assert ws.cell(7 + N, 1).value == tick(43), name   # 주 모델 모집단 45행(8 ~ 7+N)
        assert ws.max_row == 8 + N and ws.cell(8 + N, 1).value is None, name   # 표 끝 선 행
    # Sector = 대분류 5(8~12) · 빈 줄 · 머리 2 · 중분류 5(16~20) · 표 끝 선 행
    sec = wb["Sector"]
    assert [sec.cell(r, 1).value for r in range(8, 22)] == (
        ["대분류"] * 5 + [None, "업종(WICS 중분류)", "구분"] + ["중분류"] * 5 + [None])
    assert res.n_rows == N and res.n_ranked == 41
    assert [t for _, t, _ in res.top] == [tick(0), tick(1), tick(2), tick(3), tick(4)]


def test_daily_scores_sheet_contents(daily) -> None:
    _, wb = daily
    ws = wb["Score"]
    h = header(ws)
    for label in ("코드", "이름", "시장", "대분류", "중분류", "시총 (억원)", "거래대금 20일(억원)",
                  "주가 (원)",
                  "순위", "점수", "전일 순위", "Δ순위 1M", "1M 흐름",
                  "제외 사유", "커버리지", "비고", "유니버스", "업종", "축", "최대 차이",
                  "후보", "변화"):
        assert label in h, label
    groups = [c.value for c in ws[6] if c.value is not None]
    assert groups[:3] == ["종목", "종합", "상태"] and "비교 순위" in groups
    assert groups[-1] == "후보(대분류당 최대 9)"
    rows = rows_by_code(ws)
    assert ws.cell(8, 1).value == tick(0) and ws.cell(8, h["순위"]).value == 1
    r0 = rows[tick(0)]
    assert ws.cell(r0, h["전일 순위"]).value == 2                      # 1일·1W Δ 없음(N-16)
    assert not {"Δ순위", "Δ순위 1W", "1W 흐름"} & set(h)
    assert ws.cell(r0, h["Δ순위 1M"]).value is None                 # 1M 비교 판이 없다
    assert ws.cell(r0, h["1M 흐름"]).value is None                  # 그래프 칸은 값이 없다
    assert ws.cell(rows[tick(40)], h["제외 사유"]).value == "관리종목(admin)"
    assert ws.cell(rows[tick(42)], h["제외 사유"]).value == "고점근접+반전 게이트(pull_gate)"
    assert ws.cell(rows[tick(40)], h["순위"]).value is None
    assert ws.cell(rows[tick(5)], h["커버리지"]).value == "유예 D+2"
    r3 = rows[tick(3)]
    assert ws.cell(r3, group_col(ws, "리비전", "유니버스")).value == "결측(커버리지<3)"
    assert ws.cell(r3, h["축"]).value == "리비전"
    assert isinstance(ws.cell(r3, group_col(ws, "저위험", "유니버스")).value, float)
    # 제외 순서: 순위 종목 뒤에 코드순
    tail = [ws.cell(r, 1).value for r in range(8 + 41, 8 + N)]
    assert tail == sorted(tail) == [tick(i) for i in (40, 41, 42, 43)]
    # 다른 모델 순위 — v3 는 역순 유니버스
    v3 = col_of(ws, "v3 원본")                       # 헤더는 모델 이름만(spec id 는 정의·메타)
    assert ws.cell(r0, v3).value == 40


def test_daily_scores_sheet_note_flags_thin_coverage(daily) -> None:
    _, wb = daily
    ws = wb["Score"]
    h = header(ws)
    assert h["비고"] == h["커버리지"] + 1
    rows = rows_by_code(ws)
    note = {i: ws.cell(rows[tick(i)], h["비고"]).value for i in range(5)}
    # 짧은 표지(샘플 22 · DECISIONS §7 #6 ⑪) — 뜻은 비고 머리 칸 메모 범례
    assert note == {0: None, 1: "의견 0(3M)", 2: "애널 ≤3", 3: "애널 ≤3", 4: "애널 미상"}
    legend = ws.cell(7, h["비고"]).comment.text
    assert all(f"■ {k}:" in legend for k in ("애널 ≤3", "의견 0(3M)", "애널 미상"))


def test_daily_raw_sheet_flags_and_definitions(daily) -> None:
    _, wb = daily
    ws = wb["Raw"]
    h = header(ws)
    rows = rows_by_code(ws)
    v, f = h["영업이익 추정 1M(%)"], h["REV_OP_1M 표식"]
    assert ws.cell(rows[tick(1)], v).value is None                      # 흑전 → 표식만(E)
    assert ws.cell(rows[tick(1)], f).value == "흑전"
    assert ws.cell(rows[tick(2)], v).value == pytest.approx(-50.0)      # 적전 → 값 + 표식
    assert ws.cell(rows[tick(2)], f).value == "적전"
    assert ws.cell(rows[tick(3)], v).value == "결측(커버리지<3)"
    assert ws.cell(rows[tick(0)], h["EP 표식"]).value == "적자"
    assert ws.cell(rows[tick(0)], h["DY0 표식"]).value == "무배당"
    assert ws.cell(rows[tick(0)], h["배당수익률(%)"]).value == 0          # 무배당은 0(결측 아님)
    assert isinstance(ws.cell(rows[tick(9)], h["VOL60 백분위"]).value, float)
    spec = registry.get(PRIMARY)
    vol_def = next(i.definition for i in spec.indicators if i.key == "VOL60")
    # 열 정의는 7행 머리 칸 메모(5행은 빈칸) — 메모는 300px 줄로 접혀 있다
    vol = ws.cell(7, h["60일 변동성(%)"])
    assert vol_def.replace(" — ", ": ") in vol.comment.text.replace("\n", " ")
    assert ws.cell(rows[tick(0)], h["연간 기준기"]).value == "2025/12"
    assert ws.cell(rows[tick(0)], h["재무 접수일"]).value == "2026-03-15"
    assert ws.cell(rows[tick(10)], h["수급 최신일"]).value is None
    assert ws.cell(rows[tick(0)], h["수급 최신일"]).value == FRI
    # 표시 지표는 이 시트에 없다(중복 표기 금지)
    assert "1M 수익률(%)" not in h


def test_daily_has_no_display_indicator_sheet(daily) -> None:
    """지표(표시용) 시트는 뺐다(10-11 사용자 '인디케이터 굳이 볼 필요가 있나' → '없애') — 시트도,
    Meta 열 사전 줄도 없다. ROA · 부채비율은 Raw 퀄리티, 나머지는 Raw · Earnings 로 본다."""
    _, wb = daily
    assert not {"지표(표시용)", "Indicator"} & set(wb.sheetnames)
    meta = wb["Meta"]
    assert not any(meta.cell(r, 1).value in ("Indicator", "지표(표시용)")
                   for r in range(8, meta.max_row + 1))


def test_daily_earnings_real_years_quarters_and_sign_words(daily) -> None:
    """N-19: 실적 시트 머리글은 실제 연도·분기다. 합성 판(09-25)은 10-02 판과 같은 구조다
    (Y = 2026, 확정 2025/12, 컨센서스 2026·2027, 최근 분기 2026/06).
    결산기·표식·최근 분기·Q0 공시일 열은 없고, 부호 전환은 y-y 칸 안의 글자다."""
    _, wb = daily
    ws = wb["Earnings"]
    labels = [str(c.value).replace("\n", " ") for c in ws[7] if c.value is not None]
    # YoY 머리는 비교하는 두 해(10-11 사용자: 2025 옆 YoY 를 2026 대비로 읽음)
    years = ["2025", "YoY (2024-2025)", "2026E", "YoY (2025-2026E)", "2027E", "YoY (2026E-2027E)"]
    assert labels == ["코드", "이름", "대분류", *years * 3,
                      "25.2Q", "25.3Q", "25.4Q", "26.1Q", "26.2Q", "YoY (최근 분기)"]
    groups = [str(c.value) for c in ws[6] if c.value is not None]
    assert groups == ["종목", *(f"{m}(억원 · YoY %)" for m in ("매출", "영업이익", "순이익")),
                      "분기 영업이익(억원 · YoY %)"]
    gone = ("표식", "공시일", "결산기", "FY-1", "FY0")
    assert not [s for s in labels + groups if any(w in s for w in gone)]
    rows = rows_by_code(ws)
    r10 = rows[tick(10)]
    rev = col_of(ws, "2025", 0)                       # 0 = 매출, 1 = 영업이익, 2 = 순이익
    assert ws.cell(r10, rev).value == 1200.0
    assert ws.cell(r10, rev).number_format == "#,##0"                   # 확정치 — 'E' 아님
    assert ws.cell(r10, rev + 1).value == pytest.approx((1200 / 1100 - 1) * 100)
    e0 = col_of(ws, "2026E", 0)
    assert ws.cell(r10, e0).value == 1300.0                              # 2026/12 컨센서스
    assert ws.cell(r10, e0 + 1).value == pytest.approx((1300 / 1200 - 1) * 100)
    op_y = col_of(ws, "YoY (2024-2025)", 1)
    assert ws.cell(rows[tick(1)], op_y).value == "흑자전환"               # −10 → 20
    assert ws.cell(rows[tick(3)], op_y).value == "적자지속"               # −10 → −20
    assert ws.cell(rows[tick(2)], col_of(ws, "YoY (2024-2025)", 2)).value == "적자전환"  # 순이익
    # 흑자 파랑 · 적자 빨강 글자(10-11 사용자)
    assert ws.cell(rows[tick(1)], op_y).font.color.rgb == "FF0070C0"
    assert ws.cell(rows[tick(3)], op_y).font.color.rgb == "FFC00000"
    h = header(ws)
    assert [ws.cell(r10, h[q]).value for q in ("25.2Q", "25.3Q", "25.4Q", "26.1Q", "26.2Q")] == [
        10, 11, 12, 13, 14]
    assert ws.cell(r10, h["YoY (최근 분기)"]).value == pytest.approx((14 / 10 - 1) * 100)
    assert ws.cell(rows[tick(1)], h["YoY (최근 분기)"]).value == "흑자전환"   # −5 → 8


def _roll_to_2027(table: str, rows: list[dict[str, object]]) -> None:
    """2027-01-04 판 합성 — 2026 사업보고서는 0번 종목만 나왔고 나머지는 공시 전이다.
    컨센서스는 2026·2027·2028 결산기다(11번 종목은 2026 추정치도 없다).
    2026/09 분기는 0번 종목만 있다(분기 5기 창이라 0번의 2025/06 은 빠진다).
    0번의 2026 확정 지배 순이익(컨센서스 연간 표)도 연결 순이익과 같은 값으로 나왔다."""
    if table == "fi_consensus_annual":
        rows.append({"ticker": tick(0), "period": "2026/12", "data_type": "A", "revenue": 1450.0,
                     "op": 150.0, "ni": 75.0, "fetched_date": "2027-01-03"})
    elif table == "fi_fin_summary":
        rows[:] = [r for r in rows if not (r["ticker"] == tick(0) and r["period"] == "2025/06")]
        rows += [{"ticker": tick(0), "period": "2026/12", "period_type": "annual",
                  "revenue": 1450.0, "op": 150.0, "ni": 75.0, "available_date": "2027-01-03"},
                 {"ticker": tick(0), "period": "2026/09", "period_type": "quarter",
                  "revenue": 250.0, "op": 20.0, "available_date": "2026-11-14"}]
    elif table == "fi_consensus":
        rows[:] = [r for r in rows
                   if not (r["ticker"] == tick(11) and r["target_period"] == "2026/12")]
        rows += [{**r, "target_period": "2028/12", "revenue": float(r["revenue"]) + 200}
                 for r in rows if r["target_period"] == "2027/12"]


def test_daily_earnings_headers_roll_over_with_the_board_year(tmp_path: Path) -> None:
    """N-19: 연간 머리글은 판 날짜의 연도 Y 로 `Y−1 · YE · (Y+1)E` 다 — 2027-01-04 판은
    `2026 · 2027E · 2028E`. 2026 확정치가 없으면 2026 추정치를 숫자 서식 `#,##0"E"`·회색 글자로
    그 칸만 표시한다(값은 숫자). 분기 창도 판 전체의 최근 분기(2026/09)를 따라 넘어간다."""
    day = "2027-01-04"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, day, write_fi_day(fi_root, day, edit=_roll_to_2027))
    res = build_daily(day, "morning", model_root=model_root, fi_root=fi_root, out_root=tmp_path)
    ws = load_workbook(res.path)["Earnings"]
    labels = [str(c.value).replace("\n", " ") for c in ws[7] if c.value is not None]
    assert labels[3:9] == ["2026", "YoY (2025-2026)", "2027E", "YoY (2026-2027E)", "2028E",
                           "YoY (2027E-2028E)"]
    assert labels[-6:] == ["25.3Q", "25.4Q", "26.1Q", "26.2Q", "26.3Q", "YoY (최근 분기)"]
    a5 = [str(ws.cell(5, c).value) for c in range(1, ws.max_column + 1) if ws.cell(5, c).value]
    assert a5[1].startswith("2026: 확정치") and "2027E·2028E: 컨센서스" in a5[1]
    assert "2025" not in labels
    rows = rows_by_code(ws)
    rev = col_of(ws, "2026", 0)
    est = ws.cell(rows[tick(10)], rev)                  # 2026 확정치 없음 → 2026 추정치
    assert est.value == 1300 and '"E"' in est.number_format
    assert est.font.color.rgb.endswith("7F7F7F")        # 회색 글자
    assert ws.cell(rows[tick(10)], rev + 1).value == pytest.approx((1300 / 1200 - 1) * 100)
    act = ws.cell(rows[tick(0)], rev)                   # 2026 확정치 있음 → 그 칸은 그대로
    assert act.value == 1450 and act.number_format == "#,##0"
    assert act.font.color is None or not act.font.color.rgb.endswith("7F7F7F")
    assert ws.cell(rows[tick(11)], rev).value is None   # 확정·추정 둘 다 없으면 빈칸
    # 열 전체 대조 — 'E' 칸은 0번(확정)·11번(빈칸)을 뺀 전 종목, 서식이 한 행 밀리면 깨진다
    e_rows = {code for code, r in rows.items() if '"E"' in ws.cell(r, rev).number_format}
    assert e_rows == set(rows) - {tick(0), tick(11)}
    assert ws.cell(rows[tick(10)], col_of(ws, "2028E", 0)).value == 1500
    h = header(ws)
    assert ws.cell(rows[tick(0)], h["26.3Q"]).value == 20
    assert ws.cell(rows[tick(10)], h["26.3Q"]).value is None          # 그 분기 행이 없다
    # 최근 분기 y-y 는 종목마다 자기 최근 분기 — 0번 2026/09 vs 2025/09, 10번 2026/06 vs 2025/06
    q_y = h["YoY (최근 분기)"]
    assert ws.cell(rows[tick(0)], q_y).value == pytest.approx((20 / 11 - 1) * 100)
    assert ws.cell(rows[tick(10)], q_y).value == pytest.approx((14 / 10 - 1) * 100)


def _off_calendar_quarters(table: str, rows: list[dict[str, object]]) -> None:
    """5번 종목을 11월 결산사처럼 — 분기 기말이 2025/08·11·2026/02·05·08(DART period_end 그대로)이고
    마지막 2026/08 은 12월 결산 종목들의 최근 분기(2026/06)보다 늦다."""
    if table == "fi_fin_summary":
        rows[:] = [r for r in rows
                   if not (r["ticker"] == tick(5) and r["period_type"] == "quarter")]
        rows += [{"ticker": tick(5), "period": per, "period_type": "quarter", "revenue": 250.0,
                  "op": 30.0 + k, "available_date": "2026-10-15"}
                 for k, per in enumerate(("2025/08", "2025/11", "2026/02", "2026/05", "2026/08"))]


def test_daily_earnings_quarter_window_ignores_off_calendar_quarters(tmp_path: Path) -> None:
    """분기 창 끝은 달력 분기(기말 03·06·09·12월)만 본다. 11월 결산 종목의 더 늦은 2026/08 분기가
    창을 끌고 가면 머리글이 '26.0Q' 로 깨지고 12월 결산 종목 칸이 전부 빈다(검토 재현).
    그 종목 분기는 달력 칸에 넣지 않고, 최근 분기 y-y 는 자기 최근 분기(2026/08 vs 2025/08)다."""
    day = "2026-10-20"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, day, write_fi_day(fi_root, day, edit=_off_calendar_quarters))
    res = build_daily(day, "morning", model_root=model_root, fi_root=fi_root, out_root=tmp_path)
    ws = load_workbook(res.path)["Earnings"]
    quarters = ["25.2Q", "25.3Q", "25.4Q", "26.1Q", "26.2Q"]
    labels = [str(c.value).replace("\n", " ") for c in ws[7] if c.value is not None]
    assert labels[-6:] == [*quarters, "YoY (최근 분기)"]
    h, rows = header(ws), rows_by_code(ws)
    assert [ws.cell(rows[tick(10)], h[q]).value for q in quarters] == [10, 11, 12, 13, 14]
    assert [ws.cell(rows[tick(5)], h[q]).value for q in quarters] == [None] * 5
    assert ws.cell(rows[tick(5)], h["YoY (최근 분기)"]).value == pytest.approx((34 / 30 - 1) * 100)


def _september_fy(table: str, rows: list[dict[str, object]]) -> None:
    """2번 종목을 9월 결산사처럼 — 컨센서스 결산기 2026/09·2027/09·2028/09, 연간 확정 행 없음
    (fi 연간 행은 12월 결산만 싣는다, U22)."""
    if table == "fi_fin_summary":
        rows[:] = [r for r in rows
                   if not (r["ticker"] == tick(2) and r["period_type"] == "annual")]
    elif table == "fi_consensus":
        base = next(r for r in rows if r["ticker"] == tick(2) and r["horizon"] == "cur")
        rows[:] = [r for r in rows if r["ticker"] != tick(2)]
        rows += [{**base, "target_period": p, "revenue": rev, "op": rev / 10, "ni": rev / 20}
                 for p, rev in (("2026/09", 900.0), ("2027/09", 990.0), ("2028/09", 1100.0))]


def test_daily_earnings_non_december_fy_uses_the_year_the_fy_ends(tmp_path: Path) -> None:
    """비12월 결산(사용자 결정 10-06): 연간 칸 = 결산기가 끝나는 연도(증권사 FY 표기 관례) —
    9월 결산이면 2026E 칸 = 2026/09 결산기, y-y 는 한 해 앞 같은 결산월과 비교한다. 점수 시트
    비고엔 'N월 결산'을 기존 비고와 ' · ' 로 잇는다. 12월 결산 종목은 그대로다."""
    day = "2026-10-02"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, day, write_fi_day(fi_root, day, edit=_september_fy))
    wb = load_workbook(build_daily(day, "morning", model_root=model_root, fi_root=fi_root,
                                   out_root=tmp_path).path)
    ws = wb["Earnings"]
    rows = rows_by_code(ws)
    r2, r10 = rows[tick(2)], rows[tick(10)]
    rev = {y: col_of(ws, y, 0) for y in ("2025", "2026E", "2027E")}
    assert ws.cell(r2, rev["2025"]).value is None                    # 2025/09 확정·추정 없음
    assert ws.cell(r2, rev["2026E"]).value == 900                    # 2026/09 결산기 추정
    assert ws.cell(r2, rev["2027E"]).value == 990                    # 2027/09 결산기 추정
    assert ws.cell(r2, rev["2027E"] + 1).value == pytest.approx((990 / 900 - 1) * 100)
    assert [ws.cell(r10, rev[y]).value for y in ("2025", "2026E", "2027E")] == [1200, 1300, 1300]
    sc = wb["Score"]
    note, srows = header(sc)["비고"], rows_by_code(sc)
    assert sc.cell(srows[tick(2)], note).value == "애널 ≤3 · 9월 결산"
    assert sc.cell(srows[tick(10)], note).value is None              # 12월 결산은 붙이지 않는다
    assert sc.cell(srows[tick(1)], note).value == "의견 0(3M)"


def _november_fy_no_opinion(table: str, rows: list[dict[str, object]]) -> None:
    """1번 종목(추정기관수 0 → '최근 3개월 의견 없음')을 11월 결산사로 — 비고가 가장 긴 조합이다."""
    if table == "fi_consensus":
        rows[:] = [{**r, "target_period": str(r["target_period"])[:5] + "11"}
                   if r["ticker"] == tick(1) else r for r in rows]


def test_daily_scores_note_column_is_short_labels_at_fixed_width(tmp_path: Path) -> None:
    """비고는 짧은 표지를 ' · ' 로 잇고 열 폭은 17 고정(샘플 22 확정 — 옛 '최근 3개월 의견 없음 ·
    11월 결산' 은 25.75 폭이 필요했다). 짧은 표지로 바꾼 뒤 흔한 두 표지 조합은 17 안에
    든다."""
    day = "2026-10-02"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, day, write_fi_day(fi_root, day, edit=_november_fy_no_opinion))
    ws = load_workbook(build_daily(day, "morning", model_root=model_root, fi_root=fi_root,
                                   out_root=tmp_path).path)["Score"]
    col, label = header(ws)["비고"], "의견 0(3M) · 11월 결산"
    assert ws.cell(rows_by_code(ws)[tick(1)], col).value == label
    assert ws.column_dimensions[ws.cell(7, col).column_letter].width == 17
    two = fit_width(Col("note", "비고", "txt", None, 99.0), [{"note": "애널 ≤3 · 매출 순액"}])
    assert two <= 17


def test_daily_sector_and_model_sheets(daily) -> None:
    _, wb = daily
    ws = wb["Sector"]
    h = header(ws)
    first = {ws.cell(r, h["코드"]).value: r for r in range(8, ws.max_row + 1)}
    r45 = first["G45"]
    assert ws.cell(r45, h["구분"]).value == "대분류"
    assert ws.cell(r45, h["종목 수"]).value == 15
    assert ws.cell(r45, h["후보 수 (최대 9)"]).value == 9
    assert ws.cell(r45, h["후보 수 (최대 9)"]).number_format == '0"/9"'      # 대분류 표는 n/9
    assert ws.cell(r45, h["상위 30 비중(%)"]).value == pytest.approx(15 / 30 * 100)
    assert str(ws.cell(r45, h["상위 3(순위)"]).value).startswith("종목0(1)")
    assert ws.cell(7, 1).fill.fgColor.rgb.endswith("7F7F7F")            # 머리 색 통일
    assert ws.cell(r45, h["업종명"]).font.color.rgb.endswith("7030A0")   # 항목명 보라
    assert {"1W 수익률 (%)", "1M 수익률 (%)", "3M 수익률 (%)"} <= set(h)
    # 아래 업종(중분류) 표 — 머리 두 줄을 다시 쓰고 후보 수는 숫자 그대로
    low = next(r for r in range(8, ws.max_row + 1) if ws.cell(r, 1).value == "중분류")
    assert ws.cell(low - 1, h["후보 수 (최대 9)"]).value == "후보 수"
    assert ws.cell(low, h["후보 수 (최대 9)"]).number_format == "#,##0"
    assert ws.auto_filter.ref.startswith(f"A{low - 1}:")
    m = wb["Compare"]
    hm = header(m)
    for sid in SPECS:
        if sid == "v4_rank@0.2":               # v4 비교 열은 뺀다(N-27) — 주 모델 v4 기본은 남는다
            assert model_label(sid) not in hm
        else:
            assert model_label(sid) in hm, sid     # 헤더는 모델 이름(spec id 는 정의·메타)
    assert m.cell(8, col_of(m, "v4 기본")).value == 1


def test_daily_meta_sheet(daily) -> None:
    _, wb = daily
    ws = wb["Meta"]
    pairs = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(8, ws.max_row + 1)}
    assert pairs["기준일"] == FRI and pairs["basis"] == "morning"
    assert pairs["model 데이터 id"] == "m_20260925T000000Z"
    assert pairs["factor_inputs 데이터 id"] == "m_20260925T010000Z"
    assert pairs["전일 비교 데이터"].startswith(THU)
    assert "1W 비교 데이터" not in pairs and pairs["1M 비교 데이터"].startswith("없음")
    assert pairs["순위 흐름 날짜"] == "2026-09-17 ~ 2026-09-25 · 6개"   # 09-23 휴장
    assert "MG0 pass" in pairs["데이터 검사 v4_rank@0.1"]
    assert "저위험 25.0%" in pairs["가중치"]
    assert "20세션 거래대금 ≥ 10억" in pairs["유니버스 규칙"]
    assert all(f"각주 {k}" in pairs for k in (1, 2, 3, 4, 5))
    sheets_in_dict = [ws.cell(r, 1).value for r in range(8, ws.max_row + 1)]
    order = [k for k, _ in itertools.groupby(
        x for x in sheets_in_dict if x in {"Score", "Raw", "Earnings", "Sector", "Compare"})]
    assert order == ["Score", "Raw", "Earnings", "Sector", "Compare", "Score"]   # 끝 = 후보 두 줄
    assert pairs["후보 규칙"].startswith("상위 30 · 대분류(L1)당 최대 9: ")
    assert "오늘 커트라인: 순위 " in pairs["후보 규칙"]


def test_daily_values_only_no_formulas(daily) -> None:
    res, wb = daily
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                assert c.data_type != "f", (ws.title, c.coordinate)
    with zipfile.ZipFile(res.path) as z:
        for name in z.namelist():
            if name.startswith("xl/worksheets/sheet"):
                assert "<f>" not in z.read(name).decode("utf-8"), name
    ws = wb["Score"]
    r7 = rows_by_code(ws)[tick(7)]
    assert ws.cell(r7, 2).value == FORMULA_NAME                         # 문자열로 남는다


def test_daily_qpack_styles(daily) -> None:
    _, wb = daily
    ws = wb["Score"]
    h = header(ws)
    assert ws.sheet_view.showGridLines is False
    assert ws.freeze_panes == "I8"                                       # 식별 8열 뒤
    assert ws.auto_filter.ref == f"A7:{ws.cell(7, ws.max_column).column_letter}{7 + N}"
    a8 = ws.cell(8, 1)
    assert a8.font.name == "맑은 고딕" and a8.font.sz == 8
    assert ws.cell(1, 1).fill.fgColor.rgb.endswith("BDD7EE")
    # 1행: 's' 없이 A1 '정렬: …' + 정렬 열 위에만 ▲(확정 양식)
    assert ws.cell(1, 1).value == "정렬: 순위 오름차순 ▲" and ws.cell(1, h["순위"]).value == "▲"
    assert [c.value for c in ws[1]].count(None) == ws.max_column - 2
    assert ws.cell(2, 1).fill.fgColor.rgb.endswith("4F81BD")
    for r in (3, 4):                                   # 3·4행 연한 파랑 띠 + 진남색 글자
        assert ws.cell(r, 1).fill.fgColor.rgb.endswith("DCE6F1")
        assert ws.cell(r, 1).font.color.rgb.endswith("1F497D")
    assert ws.cell(2, 1).value == "Score: 종목별 종합 점수와 순위" and ws.cell(2, 1).font.b
    assert ws.cell(3, 1).value.startswith("주 모델: v4 기본 · 비교: ")
    assert ws.cell(4, 1).value == (f"기준일: {FRI} 아침 확정 데이터 · 출처: quant-ledger · "
                                   "데이터 id: Meta 시트")
    assert ws.cell(7, 1).fill.fgColor.rgb.endswith("7F7F7F")
    assert ws.cell(6, 1).fill.fgColor.rgb.endswith("595959")
    assert ws.cell(7, h["순위"]).fill.fgColor.rgb.endswith("8064A2")      # 핵심 블록
    assert ws.cell(6, h["점수"]).fill.fgColor.rgb.endswith("8064A2")      # 종합 그룹 전체
    low_risk = group_col(ws, "저위험", "유니버스")
    assert ws.cell(6, low_risk).fill.fgColor.rgb.endswith("595959")
    assert ws.cell(6, low_risk).alignment.horizontal == "centerContinuous"   # 그룹 이름 가운데
    assert ws.row_dimensions[7].height == 33.75
    assert ws.cell(7, 1).border.bottom.style == "thin"
    # 블록 세로선 — 블록 첫 열 왼쪽 medium 595959, 블록 안 thin D9D9D9, 표 끝 = 다음 행 위쪽
    assert (ws.cell(8, 1).border.left.style, ws.cell(8, 1).border.left.color.rgb) == (
        "medium", "FF595959")
    assert (ws.cell(8, 2).border.left.style, ws.cell(8, 2).border.left.color.rgb) == (
        "thin", "FFD9D9D9")
    assert ws.cell(8, h["순위"]).border.left.style == "medium"
    assert ws.cell(8, ws.max_column).border.right.style == "medium"
    assert all(ws.cell(8 + N, c).border.top.style == "medium" for c in (1, h["순위"]))
    assert ws.cell(8, h["시총 (억원)"]).number_format == "#,##0"
    sep = ws.column_dimensions[ws.cell(7, h["주가 (원)"] + 1).column_letter]
    assert sep.hidden and sep.outlineLevel == 1 and sep.width == 5
    # Q.Pack 실측(보이는 숫자 열 4.4~6.4) — 고정 13 이 아니라 값·헤더에 맞춘 최소 폭
    comp_w = ws.column_dimensions[ws.cell(7, h["점수"]).column_letter].width
    assert 4.4 <= comp_w <= 6.5, comp_w
    # 인쇄 — A4 가로 1쪽 너비, 2~7행 반복, 바닥글 시트 이름 · 쪽
    assert ws.page_setup.orientation == "landscape" and ws.page_setup.fitToWidth == 1
    assert ws.print_title_rows == "$2:$7" and ws.oddFooter.left.text == "&A"
    assert wb["Compare"].page_setup.orientation == "portrait"
    shown = [ws.column_dimensions[ws.cell(7, c).column_letter] for c in range(1, ws.max_column + 1)]
    # 문자 열 상한 — 제외 사유 16 · 비고 26(가장 긴 조합 '최근 3개월 의견 없음 · 11월 결산'이
    # 25.75) · 그 밖은 14 이하
    assert max(d.width for d in shown if not d.hidden) <= 26, [d.width for d in shown]
    wide = [ws.cell(7, c).value for c in range(1, ws.max_column + 1)
            if (d := ws.column_dimensions[ws.cell(7, c).column_letter]).width > 14 and not d.hidden]
    assert set(wide) <= {"제외 사유", "비고"}, wide
    assert wb["Raw"].row_dimensions[5].height == 11.25        # 정의 행도 한 줄
    # 행 높이는 행마다 박는다 — 시트 기본값만 두면 기본 글꼴(Calibri 11)로 다시 잡혀 Q.Pack 보다
    # 1.4배 높게 그려진다(10-05 렌더: 한 쪽 29행 vs Q.Pack 37행)
    # 표 끝 선 행(마지막 데이터 다음 행)은 높이를 정하지 않는다(시트 기본 11.25)
    assert all(ws.row_dimensions[r].height == 11.25 for r in range(1, 8 + N) if r != 7)
    for s in wb.worksheets:
        last = s.max_row - (0 if s.title in ("Meta", "trend") else 1)
        assert all(s.row_dimensions[r].height for r in range(1, last + 1)), s.title
    rules = [(str(rng.sqref), rule) for rng in ws.conditional_formatting for rule in rng.rules]
    scales = [(ref, r.colorScale) for ref, r in rules if isinstance(r.colorScale, ColorScale)]
    colored = {ref.split(":")[0].rstrip("0123456789") for ref, _ in scales}
    letter = ws.cell(7, low_risk).column_letter
    assert letter in colored and ws.cell(7, h["Δ순위 1M"]).column_letter in colored
    assert ws.cell(7, h["1M 흐름"]).column_letter not in colored        # 그래프 칸은 색 없음
    assert ws.cell(7, h["순위"]).column_letter not in colored            # 점수 시트 순위 무색
    cs = next(c for ref, c in scales if ref.startswith(letter))
    assert [v.type for v in cs.cfvo] == ["percentile"] * 3
    assert [v.val for v in cs.cfvo] == [10, 50, 90]
    assert [c.rgb[-6:] for c in cs.color] == ["F8696B", "FFEB84", "63BE7B"]   # 높음 = 초록(N-15)


def test_daily_rank_trend_sheet_and_sparklines(daily) -> None:
    """숨김 'trend' 시트 = 판마다의 −순위(원순위, 위 = 상승 — E-06)(행은 Score 시트와 같은 순서),
    점수 시트에는 1M 흐름 칸마다 엑셀 스파크라인이 붙는다. 선 색 = Δ순위 1M 부호(N-16)."""
    res, wb = daily
    tr, sc = wb["trend"], wb["Score"]
    assert [tr.cell(7, c).value for c in range(1, tr.max_column + 1)] == [
        "코드", "09-17", "09-18", "09-21", "09-22", "09-24", "09-25"]
    assert [tr.cell(r, 1).value for r in range(8, tr.max_row + 1)] == [
        sc.cell(r, 1).value for r in range(8, 8 + N)]
    r0 = rows_by_code(tr)[tick(0)]
    assert tr.cell(r0, 2).value == -3                                       # 09-17 3위
    assert tr.cell(r0, 7).value == -1                                       # 09-25 1위
    assert tr.cell(rows_by_code(tr)[tick(40)], 7).value is None             # 제외 종목
    with zipfile.ZipFile(res.path) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    h = header(sc)
    c1m = sc.cell(7, h["1M 흐름"]).column_letter
    assert xml.count("<x14:sparkline>") == N
    assert 'lineWeight="0.25"' in xml and 'lineWeight="1"' not in xml    # 가장 얇은 선
    assert f"<xm:f>trend!B{r0}:G{r0}</xm:f><xm:sqref>{c1m}{r0}</xm:sqref>" in xml
    groups = xml.split("<x14:sparklineGroup ")[1:]
    def color_of(cell: str) -> str:
        g = next(g for g in groups if f"<xm:sqref>{cell}</xm:sqref>" in g)
        return g.split('colorSeries rgb="FF')[1][:6]
    assert color_of(f"{c1m}{r0}") == "8C8C8C"                    # 1M 비교 판이 없어 모름 → 회색


def test_spark_groups_color_lines_by_delta_sign() -> None:
    """선 색 = Δ순위 1M 부호 — 상승 초록 · 하락 빨강 · 모름 회색.
    원자료 범위는 비교 판 열부터 D 까지."""
    from deliver.trend import Trend, spark_groups
    tr = Trend(dates=("2026-09-02", "2026-09-15", "2026-10-02"),
               line={"A": (-50, -20, -5), "B": (-5, -20, -45), "C": (None, -20, -20)},
               base={"1M": "2026-09-02"}, base_rank={"1M": {"A": 50, "B": 5}})
    deltas = {"1M": {"A": 45, "B": -40, "C": None}}
    groups = spark_groups(["A", "B", "C"], tr, date(2026, 10, 2), {"1M": "M"}, deltas)
    assert {g.color: [c for _, c in g.cells] for g in groups} == {
        "1E8C45": ["M8"], "D0312D": ["M9"], "8C8C8C": ["M10"]}
    assert {ref for g in groups for ref, _ in g.cells} == {
        "trend!B8:D8", "trend!B9:D9", "trend!B10:D10"}


def test_month_back_and_windows() -> None:
    from deliver.trend import month_back, window_start
    assert month_back(date(2026, 3, 31)) == date(2026, 2, 28)
    assert month_back(date(2026, 1, 15)) == date(2025, 12, 15)
    assert window_start(date(2026, 10, 2), "1W") == date(2026, 9, 25)
    assert window_start(date(2026, 10, 2), "1M") == date(2026, 9, 2)


def test_daily_rank_scale_is_reversed_on_model_sheet(daily) -> None:
    _, wb = daily
    ws = wb["Compare"]
    c = ws.cell(7, col_of(ws, "v4 기본")).column_letter
    cs = next(r.colorScale for rng in ws.conditional_formatting for r in rng.rules
              if str(rng.sqref).startswith(c))
    assert [x.rgb[-6:] for x in cs.color] == ["63BE7B", "FFEB84", "F8696B"]   # 1위 = 초록(N-15)


def test_daily_missing_or_failed_run_is_an_error(world: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(DeliverError, match="없다"):
        build_daily("2026-09-26", "morning", model_root=world["model"], fi_root=world["fi"],
                    out_root=tmp_path)
    model_root = tmp_path / "model"
    write_model_day(model_root, FRI, write_fi_day(tmp_path / "fi", FRI), status="gate_failed")
    with pytest.raises(DeliverError, match="성공 판이 아니다"):
        build_daily(FRI, "morning", model_root=model_root, fi_root=tmp_path / "fi",
                    out_root=tmp_path)


# ── 주간 엑셀 ─────────────────────────────────────────────────────────────────
EXPECTED_CANDIDATES = [*range(0, 9), *range(15, 24), *range(25, 34), 35, 36, 37]


def test_weekly_sheets_and_candidates_with_sector_cap(weekly) -> None:
    res, wb = weekly
    assert res.path.parts[-2:] == ("2026-W39", "weekly_2026-W39.xlsx")
    assert wb.sheetnames == [
        "주간 후보", "팩터 카드", "주간 추이", "이탈·진입", "지난주 성적", "메타"]
    assert res.base_date == FRI and res.n_days == 4
    ws = wb["주간 후보"]
    h = header(ws)
    assert ws.max_row == 7 + 30
    codes = [ws.cell(r, h["코드"]).value for r in range(8, 38)]
    assert codes == [tick(i) for i in EXPECTED_CANDIDATES]
    per_sector: dict[str, int] = {}
    for r in range(8, 38):
        s = ws.cell(r, h["대분류"]).value
        per_sector[s] = per_sector.get(s, 0) + 1
    assert max(per_sector.values()) == 9 and per_sector["IT"] == 9
    assert [ws.cell(r, h["순번"]).value for r in range(8, 38)] == list(range(1, 31))
    assert ws.cell(8, h["금요일 순위 (원본·상한 전)"]).value == 1
    assert ws.cell(8 + 9, h["금요일 순위 (원본·상한 전)"]).value == 16   # 상한으로 10~15위 건너뜀
    assert res.n_new == 2 and res.n_out == 2


def test_weekly_rows_have_explicit_heights(weekly) -> None:
    _, wb = weekly
    for s in wb.worksheets:
        assert all(s.row_dimensions[r].height for r in range(1, s.max_row + 1)), s.title


def test_weekly_presence_marks_and_holiday(weekly) -> None:
    _, wb = weekly
    ws = wb["주간 후보"]
    h = header(ws)
    rows = {ws.cell(r, h["코드"]).value: r for r in range(8, 38)}
    marks = [f"{d} {day[5:]}" for d, day in zip("월화수목금", W39, strict=True)]
    r0 = rows[tick(0)]
    assert [ws.cell(r0, h[m]).value for m in marks] == ["●", "●", "✕", "●", "●"]
    assert ws.cell(r0, h["주간 평균 순위"]).value == pytest.approx(statistics.fmean([2, 1, 2, 1]))
    assert ws.cell(r0, h["등장 (●)"]).value == 4
    r37 = rows[tick(37)]                 # 월요일엔 38 이 끼어 37 은 후보 밖(○)
    assert ws.cell(r37, h[marks[0]]).value == "○"
    assert ws.cell(r0, h["지난주 순위"]).value == 3
    assert ws.cell(r0, h["신규/ 유지"]).value == "유지"
    r33 = rows[tick(33)]
    assert ws.cell(r33, h["신규/ 유지"]).value == "신규"
    assert ws.cell(r33, h["신규/ 유지"]).fill.fgColor.rgb.endswith("FFFF00")
    assert ws.cell(r0, h["v3 순위 (참고)"]).value == 40
    trend = wb["주간 추이"]
    ht = header(trend)
    assert trend.max_row == 7 + 32                                      # 후보 30 + 이탈 2
    wed = f"수 {HOLIDAY[5:]}"
    wed_rank, wed_comp = col_of(trend, wed, 0), col_of(trend, wed, 1)
    assert {trend.cell(r, wed_rank).value for r in range(8, trend.max_row + 1)} == {"✕"}
    assert {trend.cell(r, wed_comp).value for r in range(8, trend.max_row + 1)} == {None}
    tr = {trend.cell(r, 1).value: r for r in range(8, trend.max_row + 1)}
    assert trend.cell(tr[tick(34)], ht["구분"]).value == "이탈"
    assert trend.cell(tr[tick(0)], col_of(trend, f"화 {TUE[5:]}", 0)).value == 1
    assert trend.cell(tr[tick(0)], col_of(trend, f"월 {MON[5:]}", 1)).value == pytest.approx(96.0)


def test_weekly_moves_and_scorecard(weekly) -> None:
    _, wb = weekly
    ws = wb["이탈·진입"]
    h = header(ws)
    got = {(ws.cell(r, h["방향"]).value, ws.cell(r, h["코드"]).value)
           for r in range(8, ws.max_row + 1)}
    assert got == {("진입", tick(33)), ("진입", tick(37)), ("이탈", tick(34)), ("이탈", tick(39))}
    sc = wb["지난주 성적"]
    hs = header(sc)
    assert sc.max_row == 7 + 30
    rows = {sc.cell(r, hs["코드"]).value: r for r in range(8, sc.max_row + 1)}
    assert sc.cell(rows[tick(0)], hs["1주 수익률"]).value == pytest.approx(ret_of(0) * 100)
    assert sc.cell(rows[tick(35)], hs["1주 수익률"]).value == "결측(수정주가미해결)"
    last_ranked = [i for i in order_for(W38[1]) if i not in excluded_for(W38[1]) and i != 35]
    uni = statistics.fmean(ret_of(i) * 100 for i in last_ranked)
    assert sc.cell(rows[tick(0)], hs["유니버스 평균"]).value == pytest.approx(uni)
    assert "승률" in str(sc.cell(5, 1).value)


def test_weekly_factor_cards_and_meta(weekly) -> None:
    _, wb = weekly
    ws = wb["팩터 카드"]
    titles = [ws.cell(r, 1).value for r in range(6, ws.max_row + 1)
              if str(ws.cell(r, 1).value or "").split(".")[0].isdigit()]
    assert len(titles) == 30 and str(titles[0]).startswith(f"1. {tick(0)}")
    assert ws.freeze_panes == "A6"
    labels = {ws.cell(r, 1).value for r in range(6, ws.max_row + 1)}
    assert {"저위험", "리비전", "60일 변동성(%)", "최근 5일 순위", "기준일"} <= labels
    rank_row = next(r for r in range(6, ws.max_row + 1) if ws.cell(r, 1).value == "최근 5일 순위")
    assert [ws.cell(rank_row, k).value for k in range(3, 8)] == ["월 2", "화 1", "수 ✕", "목 2",
                                                                "금 1"]
    meta = wb["메타"]
    pairs = {meta.cell(r, 1).value: meta.cell(r, 2).value for r in range(8, meta.max_row + 1)}
    assert pairs["주"] == "2026-W39"
    assert pairs[f"수 {HOLIDAY}"].startswith("판 없음")
    assert pairs["지난주 기준일"].startswith(W38[1])
    for ws2 in wb.worksheets:
        for row in ws2.iter_rows():
            assert all(c.data_type != "f" for c in row)


def test_weekly_without_last_week_or_runs(world: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(DeliverError, match="하나도 없다"):
        build_weekly("2026-W41", model_root=world["model"], fi_root=world["fi"],
                     out_root=tmp_path)
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, FRI, write_fi_day(fi_root, FRI))
    res = build_weekly("2026-W39", model_root=model_root, fi_root=fi_root, out_root=tmp_path)
    wb = load_workbook(res.path)
    assert res.n_days == 1 and res.n_new == 0
    assert wb["이탈·진입"].max_row == 7 and wb["지난주 성적"].max_row == 7
    ws = wb["주간 후보"]
    h = header(ws)
    assert ws.cell(8, h["신규/ 유지"]).value is None


# ── CLI ───────────────────────────────────────────────────────────────────────
def _env(tmp_path: Path) -> Path:
    p = tmp_path / "fake.env"
    p.write_text(f"BOT_TOKEN={TOKEN}\nCHAT_ID_AIPLAYGROUND={CHAT}\nOTHER=x\n", encoding="utf-8")
    return p


def _roots(world: dict[str, Path], out: Path) -> list[str]:
    return ["--model-root", str(world["model"]), "--fi-root", str(world["fi"]),
            "--out-root", str(out)]


def test_cli_daily_and_weekly_dry_run(world: dict[str, Path], tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    env = _env(tmp_path)
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--dry-run",
                   "--env-file", str(env), *_roots(world, tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert (tmp_path / "daily" / "model_scores_20260925_morning.xlsx").exists()
    assert "dry-run" in out.out and "[모델 점수] 2026-09-25 morning" in out.out
    rc = cli.main(["model-weekly", "--week", "2026-W39", "--dry-run", "--send",
                   "--env-file", str(env), *_roots(world, tmp_path)])
    out2 = capsys.readouterr()
    assert rc == 0 and "[주간 후보] 2026-W39" in out2.out
    for text in (out.out, out.err, out2.out, out2.err):
        assert TOKEN not in text and CHAT not in text


def test_cli_send_uses_transport_and_errors(world: dict[str, Path], tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    import deliver.telegram as tg
    calls: list[tuple[str, dict[str, str], str]] = []

    def fake(url: str, fields, name: str, data: bytes):
        calls.append((url, dict(fields), name))
        return {"ok": True}
    monkeypatch.setattr(tg, "urllib_transport", fake)
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--send",
                   "--env-file", str(_env(tmp_path)), *_roots(world, tmp_path)])
    out = capsys.readouterr()
    assert rc == 0 and len(calls) == 1
    assert calls[0][0].endswith("/sendDocument") and calls[0][1]["chat_id"] == CHAT
    assert calls[0][2] == "model_scores_20260925_morning.xlsx"
    assert TOKEN not in out.out + out.err and CHAT not in out.out + out.err
    rc = cli.main(["model-daily", "--date", "20260926", "--basis", "morning",
                   *_roots(world, tmp_path)])
    assert rc == 2
    assert cli.main(["model-weekly", "--week", "2026W39", *_roots(world, tmp_path)]) == 2
    nokey = tmp_path / "nokey.env"
    nokey.write_text("OTHER=x\n", encoding="utf-8")
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--dry-run",
                   "--env-file", str(nokey), *_roots(world, tmp_path)])
    assert rc == 1                                                       # 비밀 키 없음
    capsys.readouterr()
    # 비밀 파일 없음·QL_ENV 미설정은 설정 오류 rc 2 — rc 3(예상 밖 예외)이 아니다(RG-C7-4)
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--dry-run",
                   "--env-file", str(tmp_path / "none.env"), *_roots(world, tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2 and "비밀 파일 설정 오류" in err and "none.env" in err
    monkeypatch.delenv("QL_ENV", raising=False)
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--dry-run",
                   *_roots(world, tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2 and "비밀 파일 설정 오류" in err and "QL_ENV" in err


# ── 발송 장부·재발송 가드(N-25 Q9) · 생성 실패 rc 3(E-13) ──────────────────────────
LEDGER = "sent_model_daily.jsonl"
FRI_BUILD, FRI_GENERATED = "m_20260925T000000Z", "2026-09-25T00:00:00Z"


def _fake_send(monkeypatch: pytest.MonkeyPatch, *, ok: bool = True) -> list[dict[str, str]]:
    """가짜 전송(실제 텔레그램 없음) — 보낸 폼 필드(caption 포함)를 모은다."""
    import deliver.telegram as tg
    calls: list[dict[str, str]] = []

    def fake(url: str, fields, name: str, data: bytes):
        calls.append(dict(fields))
        return {"ok": True} if ok else {"ok": False, "description": "Bad Request: 가짜 실패"}
    monkeypatch.setattr(tg, "urllib_transport", fake)
    return calls


def _ledger(out: Path) -> list[dict[str, object]]:
    p = out / LEDGER
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _send_args(world: dict[str, Path], tmp_path: Path) -> list[str]:
    return ["model-daily", "--date", "20260925", "--basis", "morning", "--send",
            "--env-file", str(_env(tmp_path)), *_roots(world, tmp_path)]


def test_cli_send_records_ledger_and_skips_the_same_day(world: dict[str, Path], tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """Q9 · K0-1 — 보낸 D·basis 는 발송 장부에 한 줄 남고, 같은 D·basis 의 두 번째 자동 발송은
    건너뛴다(rc 0, 로그만). 옛 코드는 장부가 없어 같은 D 를 다시 돌리면 다시 보냈다
    (E-08 — 10-02 5회)."""
    calls = _fake_send(monkeypatch)
    args = _send_args(world, tmp_path)
    assert cli.main(args) == 0
    xlsx = tmp_path / "daily" / "model_scores_20260925_morning.xlsx"
    sent = _ledger(tmp_path)
    assert len(calls) == 1 and len(sent) == 1
    e = sent[0]
    assert {k: e[k] for k in ("date", "basis", "build_id", "correction")} == {
        "date": FRI, "basis": "morning", "build_id": FRI_BUILD, "correction": 0}
    assert e["sha256"] == hashlib.sha256(xlsx.read_bytes()).hexdigest()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(e["sent_utc"]))
    assert "정정" not in calls[0]["caption"]                 # 첫 발송 캡션은 종전 그대로
    mtime = xlsx.stat().st_mtime_ns
    capsys.readouterr()
    assert cli.main(args) == 0                               # 같은 D·basis 두 번째 실행
    out = capsys.readouterr().out
    assert len(calls) == 1 and len(_ledger(tmp_path)) == 1   # 발송 0회 · 장부 그대로
    assert "건너뜀" in out and "--resend" in out
    # 보낸 파일을 다시 만들지 않는다 — 디스크 파일이 장부 sha256 과 같게 남는다
    assert xlsx.stat().st_mtime_ns == mtime


def test_cli_resend_marks_correction_in_caption(world: dict[str, Path], tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Q9 — 같은 날 다시 보내려면 `--resend` 를 명시하고, 그때 캡션에 '정정 n'·판 id·생성
    시각을 단다."""
    calls = _fake_send(monkeypatch)
    args = _send_args(world, tmp_path)
    assert cli.main(args) == 0
    assert cli.main([*args, "--resend"]) == 0
    assert len(calls) == 2
    cap = calls[1]["caption"]
    assert "정정 1" in cap and FRI_BUILD in cap and FRI_GENERATED in cap
    assert [e["correction"] for e in _ledger(tmp_path)] == [0, 1]
    with pytest.raises(SystemExit) as ex:                    # --send 없는 --resend 는 인자 오류
        cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--resend",
                  *_roots(world, tmp_path)])
    assert ex.value.code == 2 and len(calls) == 2


def test_cli_failed_send_writes_no_ledger(world: dict[str, Path], tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """장부는 발송이 성공한 뒤에만 쓴다 — 보내지 못한 날(rc 1)은 다음 자동 실행이 다시 보낸다."""
    _fake_send(monkeypatch, ok=False)
    args = _send_args(world, tmp_path)
    assert cli.main(args) == 1
    assert _ledger(tmp_path) == []
    calls = _fake_send(monkeypatch)
    assert cli.main(args) == 0
    assert len(calls) == 1 and len(_ledger(tmp_path)) == 1


def test_cli_unreadable_ledger_sends_nothing(world: dict[str, Path], tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """장부 줄을 읽지 못하면 보냈는지 알 수 없다 — 보내지 않고 rc 2(입력 오류, P1)."""
    calls = _fake_send(monkeypatch)
    (tmp_path / LEDGER).write_text('{"date": "2026-09-25", 깨진 줄\n', encoding="utf-8")
    assert cli.main(_send_args(world, tmp_path)) == 2
    assert calls == [] and f"{LEDGER}:1" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [b'\xff\xfe{"date": "2026-09-25"}\n', b"[1, 2]\n"],
                         ids=["not_utf8", "not_object"])
def test_cli_malformed_ledger_is_an_input_error(world: dict[str, Path], tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str],
                                                raw: bytes) -> None:
    """장부가 UTF-8 이 아니거나 줄이 JSON 객체가 아니어도 보냈는지 알 수 없다 — 옛 코드는
    UnicodeDecodeError·AttributeError 가 rc 3(생성 실패)으로 샜다. rc 2, 발송 0."""
    calls = _fake_send(monkeypatch)
    (tmp_path / LEDGER).write_bytes(raw)
    assert cli.main(_send_args(world, tmp_path)) == 2
    assert calls == [] and "보냈는지 알 수 없어 보내지 않는다" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 없는 파일도 읽는다")
def test_cli_unreadable_ledger_permission_is_an_input_error(
        world: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """장부를 권한 때문에 못 읽어도(OSError) 보냈는지 알 수 없다 — rc 3(예상 밖)이 아니라
    rc 2, 발송 0."""
    calls = _fake_send(monkeypatch)
    ledger = tmp_path / LEDGER
    ledger.write_text("", encoding="utf-8")
    ledger.chmod(0o000)
    try:
        assert cli.main(_send_args(world, tmp_path)) == 2
    finally:
        ledger.chmod(0o644)
    assert calls == [] and "보냈는지 알 수 없어 보내지 않는다" in capsys.readouterr().err


def test_cli_ledger_append_failure_after_send_says_it_was_sent(
        world: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """B-58 — 발송이 성공한 뒤 장부 append 가 실패하면 rc 3 이고, 메시지가 '발송은 됐다'를 밝힌다
    (장부에 없으니 다음 자동 실행이 다시 보낼 수 있다 — 사람이 장부를 채워야 한다)."""
    calls = _fake_send(monkeypatch)
    (tmp_path / LEDGER).mkdir()           # 장부 자리에 폴더 — 읽기는 '없음', 발송 뒤 append 는 실패
    rc = cli.main(_send_args(world, tmp_path))
    err = capsys.readouterr().err
    assert rc == 3 and len(calls) == 1
    assert "발송은 됐다 — 장부 기록 실패" in err and "IsADirectoryError" in err


def test_cli_ledger_append_restores_a_missing_newline(world: dict[str, Path], tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """손 편집으로 장부 끝 줄바꿈이 빠졌어도 append 가 줄을 붙이지 않는다 — 붙으면 다음 실행부터
    매일 장부 손상 rc 2 로 멈춘다."""
    calls = _fake_send(monkeypatch)
    other = {"date": "2026-09-24", "basis": "morning", "build_id": "m_x", "sha256": "0" * 64,
             "sent_utc": "2026-09-24T01:00:00Z", "correction": 0}
    (tmp_path / LEDGER).write_text(json.dumps(other), encoding="utf-8")    # 끝 줄바꿈 없음
    args = _send_args(world, tmp_path)
    assert cli.main(args) == 0
    assert [e["date"] for e in _ledger(tmp_path)] == ["2026-09-24", FRI]
    assert cli.main(args) == 0 and len(calls) == 1     # 다음 실행도 장부를 읽고 건너뛴다


def test_cli_unexpected_error_is_rc_3(world: dict[str, Path], tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    """E-13 — DeliverError 밖의 예외(엑셀 생성 실패)는 rc 3 이다. 옛 코드는 예외가 그대로 올라가
    파이썬 기본 rc 1(문서상 '발송 실패')과 섞였다. 아무것도 보내지 않고 장부도 쓰지 않는다."""
    calls = _fake_send(monkeypatch)

    def boom(*_a: object, **_k: object) -> None:
        raise ValueError("시트 '점수' 를 찾지 못했다(가짜)")
    monkeypatch.setattr(cli, "build_daily", boom)
    rc = cli.main(_send_args(world, tmp_path))
    err = capsys.readouterr().err
    assert rc == 3 and calls == [] and _ledger(tmp_path) == []
    assert "ValueError" in err and "가짜" in err and "--date 20260925" in err


# ── 실제 model 판 빌드 → 전달층 ────────────────────────────────────────────────
def test_real_model_build_feeds_deliver(tmp_path: Path) -> None:
    """`model.build` 가 쓴 판(40종목 합성 입력, 네 spec)을 그대로 읽는다 — 파일 계약 대조."""
    from model import build as mbuild
    from test_model_v4_rank import Board, _full

    b = Board()
    for i in range(40):
        _full(b, f"{100000 + i:06d}", i, sector=("G10", "G20", "G30")[i % 3])
    for r in b.tables["fi_prices"]:
        r["close"] = round(float(r["close"]))                  # type: ignore[arg-type]
    fi, fi_root, fi_bid = b.fi(), tmp_path / "fi", "m_20260929T000500_000000Z"
    for name, t in FI_TABLES.items():
        out = fi_root / name / f"v={fi_bid}" / "part0.parquet"
        mbuild.write_parquet(list(fi.tables.get(name, ())),
                             {c.name: c.dtype for c in t.columns}, out)
        (out.parent / "_meta.json").write_text(json.dumps(
            {"table": name, "build_id": fi_bid, "basis": "morning", "date": "2026-09-28"}))
    (fi_root / "latest_morning.json").write_text(json.dumps(
        {"layer": "factor_inputs", "status": "ok", "build_id": fi_bid, "date": "2026-09-28",
         "basis": "morning"}))
    res = mbuild.build("20260928", "morning", tmp_path / "model", fi_root,
                       min_prices_on_d=10, min_ranked=10)
    assert res.ok, res.specs

    d = build_daily("2026-09-28", "morning", model_root=tmp_path / "model", fi_root=fi_root,
                    out_root=tmp_path / "out")
    wb = load_workbook(d.path)
    primary = res.primary_spec                 # 기본 주 모델(scope@1.0)
    n_scores = res.specs[primary]["n_scores"]
    assert d.n_rows == n_scores and d.n_ranked == res.specs[primary]["n_ranked"]
    assert wb["Score"].max_row == 8 + n_scores                 # + 표 끝 선 행
    pairs = {wb["Meta"].cell(r, 1).value: wb["Meta"].cell(r, 2).value
             for r in range(8, wb["Meta"].max_row + 1)}
    assert pairs["model 데이터 id"] == res.build_id
    assert "MG1 pass" in pairs[f"데이터 검사 {primary}"]
    rule = pairs["유니버스 규칙"]
    # N-30 ①(10-08): scope 는 의견 0 종목도 남긴다 — 메타 유니버스 규칙 줄에서 추정기관수 조건이 빠진다
    assert "추정기관수" not in rule and "추정치 유예 없음" in rule
    # scope(v3 엔진) 점수 행엔 업종 열이 없다 — fi_universe 업종으로 채워 업종 시트가 3 대분류로 선다
    sector_rows = [wb["Sector"].cell(r, 1).value for r in range(8, wb["Sector"].max_row + 1)]
    assert len(sector_rows) >= 3, sector_rows
    # 신선도도 점수 행이 아니라 fi_universe 에서 — 빈칸이면 커버리지 열이 통째로 빈다
    ws = wb["Score"]
    cov = [ws.cell(r, header(ws)["커버리지"]).value for r in rows_by_code(ws).values()]
    assert cov and all(cov), cov[:5]
    # 종합 점수 열 이름은 엔진마다 다르다(v3 composite_score · v2 total_score) — 비면 안 된다
    comp = [ws.cell(r, header(ws)["점수"]).value for r in rows_by_code(ws).values()]
    assert all(isinstance(c, float) for c in comp), comp[:5]
    sec = wb["Sector"]
    med = [sec.cell(r, header(sec)["점수 중앙값"]).value for r in range(8, sec.max_row + 1)]
    assert any(isinstance(m, float) for m in med), med
    with pytest.raises(DeliverError, match="업종 열"):
        load_day(tmp_path / "model", None, find_run(tmp_path / "model", "2026-09-28", "morning"),
                 with_fi=False)
    w = build_weekly("2026-W40", model_root=tmp_path / "model", fi_root=fi_root,
                     out_root=tmp_path / "out")
    assert w.base_date == "2026-09-28" and w.n_days == 1
    assert w.n_candidates == min(30, 3 * 9, res.specs[primary]["n_ranked"])   # 3 업종 × 상한 9
    for book in (wb, load_workbook(w.path)):
        for ws in book.worksheets:
            for row in ws.iter_rows():
                assert all(c.data_type != "f" for c in row)


def test_failed_comparison_model_is_left_out_of_the_excel(tmp_path: Path, monkeypatch) -> None:
    """비교 모델이 게이트에서 떨어지면 그 모델 열만 빠지고 메타에 사유가 남는다(N-11 격리)."""
    from model import build as mbuild
    from test_model_build import board_fi, edit_scores, patch_engine, write_fi_tree

    v2 = "v2_percentrank@1.0"
    fi_root = write_fi_tree(tmp_path / "fi", board_fi())
    patch_engine(monkeypatch, "v2_percentrank", edit_scores(lambda s: s[0].update(rank=None)))
    res = mbuild.build("20260928", "morning", tmp_path / "model", fi_root,
                       min_prices_on_d=10, min_ranked=10)
    assert res.ok and res.excluded == (v2,)
    d = build_daily("2026-09-28", "morning", model_root=tmp_path / "model", fi_root=fi_root,
                    out_root=tmp_path / "out")
    wb = load_workbook(d.path)
    pairs = {wb["Meta"].cell(r, 1).value: wb["Meta"].cell(r, 2).value
             for r in range(8, wb["Meta"].max_row + 1)}
    assert "MG3 fail" in pairs[f"제외된 비교 모델 {v2}"]
    assert f"데이터 검사 {v2}" not in pairs and v2 not in pairs["비교 모델"]
    assert model_label(v2) not in header(wb["Compare"])


def test_erroring_comparison_model_is_left_out_of_the_excel(tmp_path: Path, monkeypatch) -> None:
    """D-01 왕복(build ↔ 엑셀 계약): 비교 모델 엔진이 예외를 내면 `model.build` 가 그 모델을 빼고,
    일일 엑셀은 그 모델 열을 빼고 메타에 '실행 오류로 …' 와 사유를 적는다."""
    from model import build as mbuild
    from test_model_build import board_fi, boom, patch_engine, write_fi_tree

    v2 = "v2_percentrank@1.0"
    fi_root = write_fi_tree(tmp_path / "fi", board_fi())
    patch_engine(monkeypatch, "v2_percentrank", boom)
    res = mbuild.build("20260928", "morning", tmp_path / "model", fi_root,
                       min_prices_on_d=10, min_ranked=10)
    assert res.ok and res.excluded == (v2,)
    d = build_daily("2026-09-28", "morning", model_root=tmp_path / "model", fi_root=fi_root,
                    out_root=tmp_path / "out")
    wb = load_workbook(d.path)
    pairs = {wb["Meta"].cell(r, 1).value: wb["Meta"].cell(r, 2).value
             for r in range(8, wb["Meta"].max_row + 1)}
    assert pairs[f"제외된 비교 모델 {v2}"] == (
        "실행 오류로 이번 데이터에서 뺐다 · ZeroDivisionError: 지표 분모 0")
    assert f"데이터 검사 {v2}" not in pairs and v2 not in pairs["비교 모델"]
    assert model_label(v2) not in header(wb["Compare"])


def test_engine_error_comparison_model_reason_in_the_meta_sheet(tmp_path: Path) -> None:
    """D-01: 실행 예외로 뺀 비교 모델(`excluded_specs` 항목에 `error`)은 메타에 '실행 오류로 …'
    와 사유를 적는다. 게이트로 뺀 것은 '게이트 실패로 …' 문구 그대로다."""
    v2, v3 = "v2_percentrank@1.0", "v3_zscore@1.0"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, FRI, write_fi_day(fi_root, FRI))
    path = model_root / "_runs" / "20260925_morning.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    gate_failed = meta["specs"].pop(v3)
    gate_failed["gates"] = {"MG3": {"status": "fail"}}
    meta["specs"].pop(v2)
    meta["excluded_specs"] = {v2: {"error": "ZeroDivisionError: x"}, v3: gate_failed}
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    res = build_daily(FRI, "morning", model_root=model_root, fi_root=fi_root, out_root=tmp_path)
    ws = load_workbook(res.path)["Meta"]
    pairs = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(8, ws.max_row + 1)}
    assert pairs[f"제외된 비교 모델 {v2}"] == (
        "실행 오류로 이번 데이터에서 뺐다 · ZeroDivisionError: x")
    assert pairs[f"제외된 비교 모델 {v3}"] == "게이트 실패로 이번 데이터에서 뺐다 · MG3 fail"


def test_display_names_strip_common_suffix_and_wics_prefix() -> None:
    from deliver.reader import display_names
    rows = display_names([
        {"name": "오리온홀딩스보통주", "sector_l1_name": "필수소비재",
         "sector_l2_name": "WICS 식품,음료,담배"},
        {"name": "현대차우", "sector_l1_name": "WICS 경기관련소비재", "sector_l2_name": None},
        {"name": "보통주", "sector_l1_name": None, "sector_l2_name": "WICS 자본재"},
    ])
    assert [r["name"] for r in rows] == ["오리온홀딩스", "현대차우", "보통주"]
    assert rows[0]["sector_l2_name"] == "식품,음료,담배"
    assert rows[1]["sector_l1_name"] == "경기관련소비재"
    assert rows[1]["sector_l2_name"] is None and rows[2]["sector_l2_name"] == "자본재"


# ── 배포 묶음 4-2a — 일간 엑셀 표시 바로잡기(E-09·E-02·E-08·E-06·E-10·Q10·N-27) ─────────────
def _meta_pairs(wb) -> list[tuple[object, object]]:
    """메타 시트 키·값 표(8행부터 첫 빈 행 전까지 — 그 뒤는 열 사전)."""
    ws, out = wb["Meta"], []
    for r in range(8, ws.max_row + 1):
        if ws.cell(r, 1).value is None:
            break
        out.append((ws.cell(r, 1).value, ws.cell(r, 2).value))
    return out


def test_meta_lines_are_placed_by_key_name(daily) -> None:
    """E-09 — 비교 판 줄은 'model 생성 시각' 뒤에 이름으로 끼운다. 옛 코드는 insert(6)·[7:7]·
    insert(9) 고정 숫자라 1W 를 뺀 뒤 '순위 흐름 판'이 factor_inputs·equity 판 id 사이에 끼었다."""
    _, wb = daily
    keys = [k for k, _ in _meta_pairs(wb)]
    at = keys.index("model 생성 시각")
    assert keys[at + 1:at + 4] == ["전일 비교 데이터", "1M 비교 데이터", "순위 흐름 날짜"]
    assert keys[keys.index("factor_inputs 데이터 id") + 1] == "equity 데이터 id"


def test_exclude_reason_column_fits_every_v4_label(daily) -> None:
    """N-26 4.5 — '제외 사유' 열 상한 26. 상한 16 이면 v4 라벨 8개 중 6개가 잘려 보인다
    (가장 긴 '고점근접+반전 게이트(pull_gate)' qpack fit 25.05)."""
    from deliver.common import EXCLUDE_LABELS, exclude_label
    _, wb = daily
    ws = wb["Score"]
    col = header(ws)["제외 사유"]
    labels = [exclude_label(c) for c in (*EXCLUDE_LABELS, "pull_gate")]
    probe = Col("excl", "제외 사유", "txt", None, 99.0)
    need = max(fit_width(probe, [{"excl": s}]) for s in labels)
    assert ws.column_dimensions[ws.cell(7, col).column_letter].width >= need


def test_meta_records_excel_time_and_deployed_rev(world: dict[str, Path], tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """E-08 — 같은 model 판으로 코드만 바뀐 정정판을 파일로 구별하게 메타에 엑셀 생성 시각과 배포
    rev 를 싣는다. rev 는 **코드 루트**(`src/` 의 부모 — 서버 ~/quant-ledger)의 DEPLOYED.json
    (deploy.sh 가 쓴다)에서 읽는다 — QL_HOME 은 데이터 루트라 다른 곳을 가리킬 수 있다(검토 사소 2).
    못 읽으면 '알 수 없음'."""
    import deliver.reader as reader
    code, home = tmp_path / "code", tmp_path / "home"
    code.mkdir()
    home.mkdir()
    monkeypatch.setattr(reader, "CODE_ROOT", code)
    monkeypatch.setenv("QL_HOME", str(home))
    (code / "DEPLOYED.json").write_text(json.dumps(
        {"rev": "abc1234", "branch": "main", "at_utc": "2026-10-08T05:00:00Z"}), encoding="utf-8")
    (home / "DEPLOYED.json").write_text(json.dumps({"rev": "zzz9999"}), encoding="utf-8")
    res = build_daily(FRI, "morning", model_root=world["model"], fi_root=world["fi"],
                      out_root=tmp_path / "a")
    pairs = dict(_meta_pairs(load_workbook(res.path)))
    assert pairs["코드 rev"] == "abc1234 · 배포 2026-10-08T05:00:00Z"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", str(pairs["엑셀 생성 시각"]))
    for broken in (None, "{깨진"):
        p = code / "DEPLOYED.json"
        if broken is None:
            p.unlink()
        else:
            p.write_text(broken, encoding="utf-8")
        res = build_daily(FRI, "morning", model_root=world["model"], fi_root=world["fi"],
                          out_root=tmp_path / "b")
        assert dict(_meta_pairs(load_workbook(res.path)))["코드 rev"] == "알 수 없음"


def _write_rank_run(model_root: Path, day: str, spec: str, ranks: dict[str, int],
                    basis: str = "morning") -> None:
    """순위만 있는 최소 model 판(순위 흐름 재료). 판 id 접두어는 basis 를 따른다(m_ · e_)."""
    bid = f"{'e' if basis == 'evening' else 'm'}_{day.replace('-', '')}T000000Z"
    _write(model_root / spec / f"v={bid}" / "scores.parquet",
           pa.schema([("ticker", pa.string()), ("rank", pa.int64())]),
           [{"ticker": t, "rank": r} for t, r in ranks.items()])
    runs = model_root / "_runs"
    runs.mkdir(parents=True, exist_ok=True)
    meta = {"layer": "model", "status": "ok", "build_id": bid, "date": day, "basis": basis,
            "fi_build_id": "x", "generated_at": f"{day}T00:00:00Z", "specs": {spec: {}},
            "primary_spec": spec}
    (runs / f"{day.replace('-', '')}_{basis}.json").write_text(json.dumps(meta), encoding="utf-8")


def test_trend_line_direction_matches_its_color_when_universe_grows(tmp_path: Path) -> None:
    """E-06 G1 — 유니버스가 100 → 110 으로 커지고 X 의 순위가 50 → 51(1칸 하락)이면 선 색은
    빨강(Δ −1)이다. 옛 코드는 선을 백분위(50.5 → 54.1, 위로)로 그려 선은 오르는데 색은 빨강이었다.
    선 = 원순위(위 = 상승)라 선의 처음 → 끝 방향과 색이 늘 같다."""
    from deliver.reader import load_run
    from deliver.trend import LINE_DOWN, load_trend, spark_groups
    from deliver.trend import write_sheet as write_trend
    from openpyxl import Workbook
    spec, d0, d1 = "v4_rank@0.1", "2026-09-01", "2026-10-01"
    others0 = {f"T{k:03d}": k + (1 if k >= 50 else 0) for k in range(1, 100)}
    others1 = {f"T{k:03d}": k + (1 if k >= 51 else 0) for k in range(1, 110)}
    _write_rank_run(tmp_path, d0, spec, {"X": 50, **others0})
    _write_rank_run(tmp_path, d1, spec, {"X": 51, **others1})
    run = load_run(tmp_path, d1, "morning")
    trend = load_trend(tmp_path, run, spec, "morning")
    assert trend.dates == (d0, d1) and trend.base["1M"] == d0
    delta = trend.delta("1M", "X", 51)
    assert delta == -1
    wb = Workbook()
    write_trend(wb, ["X"], trend)
    ws = wb["trend"]
    first, last = ws.cell(8, 2).value, ws.cell(8, 3).value
    assert isinstance(first, int | float) and isinstance(last, int | float)
    groups = spark_groups(["X"], trend, date.fromisoformat(d1), {"1M": "M"}, {"1M": {"X": delta}})
    assert [g.color for g in groups] == [LINE_DOWN]          # 선 색 = 하락
    assert last < first                                       # 선도 내려간다(색과 같은 방향)


def test_evening_trend_and_previous_run_read_only_evening_runs(tmp_path: Path) -> None:
    """PR-6 — 1M 순위 흐름·전일 비교 판은 같은 basis 의 `_runs` 만 읽는다. 한 루트에 아침판과
    장 마감 판(evening)이 섞여 있어도(운영은 루트부터 다르다 — T-3) evening 흐름에 아침판 날짜·순위가
    끼지 않는다. 음성 대조: 아침판은 매일(09-01·09-15·10-01) 있고 evening 은 09-01·10-01 뿐이다."""
    from deliver.reader import load_run, previous_run
    from deliver.trend import load_trend
    spec, d0, d1, d2 = "v4_rank@0.1", "2026-09-01", "2026-09-15", "2026-10-01"
    for day in (d0, d1, d2):
        _write_rank_run(tmp_path, day, spec, {"X": 10, "Y": 20})
    _write_rank_run(tmp_path, d0, spec, {"X": 30, "Y": 1}, basis="evening")
    _write_rank_run(tmp_path, d2, spec, {"X": 5, "Y": 2}, basis="evening")
    run = load_run(tmp_path, d2, "evening")
    trend = load_trend(tmp_path, run, spec, "evening")
    assert trend.dates == (d0, d2) and trend.base["1M"] == d0
    assert trend.line["X"] == (-30, -5) and trend.delta("1M", "X", 5) == 25
    prev = previous_run(tmp_path, d2, "evening")
    assert prev is not None and (prev.date, prev.basis) == (d0, "evening")
    morning = load_trend(tmp_path, load_run(tmp_path, d2, "morning"), spec, "morning")
    assert morning.dates == (d0, d1, d2) and morning.line["X"] == (-10, -10, -10)


# ── 장 마감 직후 판(PR-6) — 별도 루트 data/model_db · basis evening ───────────────
POSTCLOSE_NOTE = ("장 마감 직후 데이터(가격 15:35·수급 15:40 정규장 기준) · "
                  "컨센서스 기준일 = 직전 거래일")


def _tree_state(root: Path) -> dict[str, object]:
    """폴더 아래 경로마다 (파일이면 크기·mtime_ns·sha256, 폴더면 'dir') — 건드렸는지 대조용."""
    return {p.relative_to(root).as_posix():
            "dir" if p.is_dir() else (p.stat().st_size, p.stat().st_mtime_ns,
                                      hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*"))}


def test_postclose_run_in_model_db_leaves_operating_roots_alone(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """PR-6(T-3·T-7) — 장 마감 판은 `data/model_db/{factor_inputs,model,deliver}` 를 루트 인자로만
    받아 model CLI·deliver CLI 를 돈다. 같은 D 의 아침판이 운영 루트(CLI 기본 `data/…`)에 있어도
    읽지도 쓰지도 않는다. 그림자 기간처럼 `--send` 없이 돌리면 엑셀만 짓는다 — 전송·발송 장부 0.
    엑셀 메타에는 데이터 성격 줄이 붙고, 같은 루트에 직전 evening 판이 없으니 전일 비교 데이터는
    '전일 데이터 없음'."""
    from model.__main__ import main as model_cli
    from test_model_build import board_fi, write_fi_tree

    home = tmp_path / "ql_home"
    monkeypatch.setenv("QL_HOME", str(home))
    ops, mdb = home / "data", home / "data" / "model_db"
    small = ["--min-prices-on-d", "10", "--min-ranked", "10"]
    # 운영 루트 — 같은 D 의 아침판 fi·model·엑셀·발송 장부(전부 CLI 기본 루트)
    write_fi_tree(ops / "factor_inputs", board_fi())
    assert model_cli(["build", "--date", "20260928", "--basis", "morning", *small]) == 0
    assert cli.main(["model-daily", "--date", "20260928", "--basis", "morning"]) == 0
    (ops / "deliver" / LEDGER).write_text(
        '{"date": "2026-09-28", "basis": "morning"}\n', encoding="utf-8")
    morning_meta = dict(_meta_pairs(load_workbook(
        ops / "deliver" / "daily" / "model_scores_20260928_morning.xlsx")))
    assert morning_meta["basis"] == "morning" and "데이터 성격" not in morning_meta
    before = {n: _tree_state(ops / n) for n in ("factor_inputs", "model", "deliver")}
    assert all(before.values())

    write_fi_tree(mdb / "factor_inputs", board_fi(), "e_20260928T063000_000000Z",
                  basis="evening")
    fi = ["--fi-root", str(mdb / "factor_inputs")]
    assert model_cli(["build", "--date", "20260928", "--basis", "evening", *small, *fi,
                      "--root", str(mdb / "model")]) == 0
    calls = _fake_send(monkeypatch)
    capsys.readouterr()
    assert cli.main(["model-daily", "--date", "20260928", "--basis", "evening", *fi,
                     "--model-root", str(mdb / "model"),
                     "--out-root", str(mdb / "deliver")]) == 0
    out = capsys.readouterr().out

    assert {n: _tree_state(ops / n) for n in ("factor_inputs", "model", "deliver")} == before
    assert calls == [] and not (mdb / "deliver" / LEDGER).exists()
    assert POSTCLOSE_NOTE in out                                  # 표준출력 캡션
    xlsx = mdb / "deliver" / "daily" / "model_scores_20260928_evening.xlsx"
    book = load_workbook(xlsx)
    meta = dict(_meta_pairs(book))
    assert meta["basis"] == "evening" and meta["데이터 성격"] == POSTCLOSE_NOTE
    assert meta["전일 비교 데이터"] == "전일 데이터 없음"           # 변화 열도 비운다(I-2)
    assert str(book["Score"]["A4"].value).startswith("기준일: 2026-09-28 장 마감 데이터")
    assert meta["순위 흐름 날짜"] == "2026-09-28 ~ 2026-09-28 · 1개"
    assert json.loads((mdb / "model" / "latest_evening.json").read_text())["basis"] == "evening"


# ── T-50(PR-6b) — 장 마감 판 1M 비교 판이 evening 이력에 없으면 아침 확정판으로 ─────────────
T50_NOTE = "(아침 확정 데이터 — 장 마감 데이터 비교 데이터 없음, T-50)"


def _evening_copy(src: Path, dst: Path, day: str) -> None:
    """합성 아침판(world) 하루를 다른 루트에 장 마감 판(basis evening)으로 옮겨 쓴다
    — 점수·지표 파일은 그대로."""
    meta = json.loads((src / "_runs" / f"{day.replace('-', '')}_morning.json").read_text(
        encoding="utf-8"))
    for sid in meta["specs"]:
        part = Path(sid) / f"v={meta['build_id']}"
        shutil.copytree(src / part, dst / part)
    (dst / "_runs").mkdir(parents=True, exist_ok=True)
    (dst / "_runs" / f"{day.replace('-', '')}_evening.json").write_text(
        json.dumps({**meta, "basis": "evening"}, ensure_ascii=False), encoding="utf-8")


def _t50_roots(world: dict[str, Path], tmp_path: Path) -> tuple[Path, Path]:
    """장 마감 루트(09-24·09-25 evening 판뿐 — 1M 비교 판 없음)와 대체 루트(아침 확정판
    08-24·08-26). D = 09-25 의 D−1개월 = 08-25 라 같은 규칙 비교 판은 08-24 다(08-26 은 그 뒤라
    음성 대조)."""
    ev, fb = tmp_path / "model_db" / "model", tmp_path / "model"
    for day in (THU, FRI):
        _evening_copy(world["model"], ev, day)
    _write_rank_run(fb, "2026-08-24", PRIMARY, {tick(0): 30, tick(1): 1})
    _write_rank_run(fb, "2026-08-26", PRIMARY, {tick(0): 40, tick(1): 40})
    return ev, fb


def _d1m(wb, tickers: tuple[str, ...]) -> dict[str, object]:
    sc = wb["Score"]
    h, rows = header(sc), rows_by_code(sc)
    return {t: sc.cell(rows[t], h["Δ순위 1M"]).value for t in tickers}


def _t50_warns(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "deliver.trend" and r.levelname == "WARNING" and "T-50" in r.getMessage()]


def test_t50_evening_1m_base_falls_back_to_the_morning_board(
        world: dict[str, Path], tmp_path: Path) -> None:
    """T-50 — 장 마감 판(evening)의 1M 비교 판(D−1개월 이하 마지막 evening 판)이 evening
    이력에 없으면 `--trend-fallback-root`(연구 루트) 아침 확정판의 같은 규칙 판(08-24) 순위로
    Δ순위 1M 을 채운다. 흐름 선 첫 점도 그 판이라 선 처음 → 끝 = Δ(선 색 = Δ 부호)가 그대로다.
    메타 '1M 비교 판'에 표시. 전일 순위는 evening 판(09-24) 그대로. 옛 동작(PR-6)은 컷오버 뒤
    약 4주 Δ순위 1M 이 전부 빈칸이었다."""
    ev, fb = _t50_roots(world, tmp_path)
    out = tmp_path / "deliver"
    assert cli.main(["model-daily", "--date", "20260925", "--basis", "evening",
                     "--model-root", str(ev), "--fi-root", str(world["fi"]),
                     "--out-root", str(out), "--trend-fallback-root", str(fb)]) == 0
    path = out / "daily" / "model_scores_20260925_evening.xlsx"
    wb = load_workbook(path)
    # 장 마감 엑셀 4행 기준일 말 = '장 마감 데이터'(아침은 '아침 확정 데이터') — 확정 양식 시트 모두
    for name in ("Score", "Sector", "Earnings", "Compare", "Raw", "Meta"):
        a4 = str(wb[name]["A4"].value)
        assert a4 == f"기준일: {FRI} 장 마감 데이터" or a4.startswith(
            f"기준일: {FRI} 장 마감 데이터 · "), (name, a4)
    meta = dict(_meta_pairs(wb))
    assert meta["1M 비교 데이터"] == f"2026-08-24 {T50_NOTE}"
    assert meta["순위 흐름 날짜"] == "2026-08-24 ~ 2026-09-25 · 3개"
    assert str(meta["전일 비교 데이터"]).startswith(THU)
    d1m = _d1m(wb, (tick(0), tick(1), tick(2)))
    assert d1m == {tick(0): 29, tick(1): -1, tick(2): None}     # 30 → 1 · 1 → 2 · 아침 판에 없음
    sc, tr = wb["Score"], wb["trend"]
    assert sc.cell(rows_by_code(sc)[tick(0)], header(sc)["전일 순위"]).value == 2   # 09-24 evening
    assert [tr.cell(7, c).value for c in range(1, tr.max_column + 1)] == [
        "코드", "08-24", "09-24", "09-25"]
    r0, r1 = rows_by_code(tr)[tick(0)], rows_by_code(tr)[tick(1)]
    line0 = [tr.cell(r0, c).value for c in (2, 3, 4)]
    assert line0 == [-30, -2, -1] and line0[-1] - line0[0] == d1m[tick(0)]
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    c1m = sc.cell(7, header(sc)["1M 흐름"]).column_letter
    # 스파크라인은 첫 점(아침 판 열 B)부터 D 까지 — 선 색 = Δ 부호
    assert f"<xm:f>trend!B{r0}:D{r0}</xm:f><xm:sqref>{c1m}{r0}</xm:sqref>" in xml
    groups = xml.split("<x14:sparklineGroup ")[1:]

    def color_of(cell: str) -> str:
        g = next(g for g in groups if f"<xm:sqref>{cell}</xm:sqref>" in g)
        return g.split('colorSeries rgb="FF')[1][:6]
    assert color_of(f"{c1m}{r0}") == "1E8C45" and color_of(f"{c1m}{r1}") == "D0312D"


@pytest.mark.parametrize(("spec", "base", "d0"), [
    (PRIMARY, "2026-08-21", 9),
    ("v3_zscore@1.0", f"2026-08-24 {T50_NOTE}", 29),
], ids=["evening_board_readable", "evening_board_without_primary"])
def test_t50_evening_history_wins_and_unreadable_board_falls_back(
        world: dict[str, Path], tmp_path: Path, spec: str, base: str, d0: int,
        caplog: pytest.LogCaptureFixture) -> None:
    """T-50 — evening 이력이 D−1개월까지 닿으면(08-21 evening 판) 대체 루트를 줘도 그 evening
    판을 쓴다(아침 08-24 가 더 가까워도 — 표시 없음). 그 판에 주 모델 순위가 없으면(다른 spec
    만) 못 읽은 것이라 아침 판으로 메운다. 어느 쪽도 'T-50 대체 안 씀' 경고는 없다(이행 기간 밖)."""
    ev, fb = _t50_roots(world, tmp_path)
    _write_rank_run(ev, "2026-08-21", spec, {tick(0): 10, tick(1): 1}, basis="evening")
    with caplog.at_level("WARNING", logger="deliver.trend"):
        res = build_daily(FRI, "evening", model_root=ev, fi_root=world["fi"],
                          out_root=tmp_path / "o", trend_fallback_root=fb)
    assert _t50_warns(caplog) == []
    wb = load_workbook(res.path)
    meta = dict(_meta_pairs(wb))
    assert meta["1M 비교 데이터"] == base
    assert meta["순위 흐름 날짜"] == f"{base[:10]} ~ 2026-09-25 · 3개"
    assert _d1m(wb, (tick(0),)) == {tick(0): d0}


def test_t50_evening_without_fallback_root_keeps_the_blank(
        world: dict[str, Path], tmp_path: Path) -> None:
    """회귀 — 대체 인자가 없으면 PR-6 그대로: evening 1M 비교 판이 없어 Δ순위 1M 은 빈칸,
    흐름은 evening 판뿐."""
    ev, _ = _t50_roots(world, tmp_path)
    res = build_daily(FRI, "evening", model_root=ev, fi_root=world["fi"], out_root=tmp_path / "o")
    wb = load_workbook(res.path)
    meta = dict(_meta_pairs(wb))
    assert meta["1M 비교 데이터"] == "없음(그 전 데이터가 없다)"
    assert meta["순위 흐름 날짜"] == "2026-09-24 ~ 2026-09-25 · 2개"
    assert set(_d1m(wb, tuple(tick(i) for i in range(N))).values()) == {None}


@pytest.mark.parametrize(("case", "reason"), [
    ("manifest", "읽기 실패(DeliverError"),
    ("scores_parquet", "읽기 실패(InvalidInputException"),
    ("no_board", "판 없음"),
    ("no_primary", "주 모델 없음(2026-08-24 판 specs 에 v4_rank@0.1 없음)"),
    ("no_scores", "점수 표 없음(2026-08-24 판 v4_rank@0.1)"),
], ids=["manifest", "scores_parquet", "no_board", "no_primary", "no_scores"])
def test_t50_unusable_fallback_board_leaves_the_blank_with_one_warning(
        world: dict[str, Path], tmp_path: Path, case: str, reason: str,
        caplog: pytest.LogCaptureFixture) -> None:
    """T-50 컨트롤러 결정 — 대체 루트는 표시 열 하나를 채우는 보조 입력이다. 08-24 아침판을
    못 쓰면(manifest·점수 parquet 깨짐 · 판 없음 · 판에 주 모델 없음 · 점수 표 없음) 장 마감 판
    엑셀은 rc 0 으로 나가고, 대체 없이 PR-6 그대로 빈칸이다(P1 — 모호하면 결측). 경고는 정확히 한
    줄 — 대체 루트 · 기준일(D−1개월 08-25) · 사유. 깨짐을 삼키기 전에는 rc 2(manifest) ·
    rc 3(parquet)으로 15:41 발송이 연구 루트 상태에 묶였다."""
    ev, fb = _t50_roots(world, tmp_path)
    manifest = fb / "_runs" / "20260824_morning.json"
    scores = fb / PRIMARY / "v=m_20260824T000000Z" / "scores.parquet"
    if case in ("manifest", "scores_parquet"):
        (manifest if case == "manifest" else scores).write_text("{깨진", encoding="utf-8")
    elif case == "no_board":
        manifest.unlink()
    elif case == "no_primary":
        _write_rank_run(fb, "2026-08-24", "v3_zscore@1.0", {tick(0): 30})
    else:
        scores.unlink()
    out = tmp_path / "deliver"
    with caplog.at_level("WARNING", logger="deliver.trend"):
        rc = cli.main(["model-daily", "--date", "20260925", "--basis", "evening",
                       "--model-root", str(ev), "--fi-root", str(world["fi"]),
                       "--out-root", str(out), "--trend-fallback-root", str(fb)])
    assert rc == 0
    wb = load_workbook(out / "daily" / "model_scores_20260925_evening.xlsx")
    meta = dict(_meta_pairs(wb))
    assert meta["1M 비교 데이터"] == "없음(그 전 데이터가 없다)"
    assert meta["순위 흐름 날짜"] == "2026-09-24 ~ 2026-09-25 · 2개"
    assert set(_d1m(wb, tuple(tick(i) for i in range(N))).values()) == {None}
    warns = _t50_warns(caplog)
    assert len(warns) == 1, warns
    assert str(fb) in warns[0] and "기준일 2026-08-25" in warns[0]
    assert f"사유 {reason}" in warns[0]


def test_t50_fallback_board_on_the_month_back_day_counts(
        world: dict[str, Path], tmp_path: Path) -> None:
    """MINOR-1 — 같은 규칙은 D−1개월 **이하** 마지막 판이다. 대체 루트에 경계 날짜 08-25 아침판이
    있으면 08-24 가 아니라 08-25 판을 쓴다(미만으로 고르면 하루 앞 판이 끼어든다)."""
    ev, fb = _t50_roots(world, tmp_path)
    _write_rank_run(fb, "2026-08-25", PRIMARY, {tick(0): 20, tick(1): 1})
    res = build_daily(FRI, "evening", model_root=ev, fi_root=world["fi"], out_root=tmp_path / "o",
                      trend_fallback_root=fb)
    wb = load_workbook(res.path)
    assert dict(_meta_pairs(wb))["1M 비교 데이터"] == f"2026-08-25 {T50_NOTE}"
    assert _d1m(wb, (tick(0),)) == {tick(0): 19}


def test_t50_fallback_programming_error_is_not_swallowed(
        world: dict[str, Path], tmp_path: Path) -> None:
    """MINOR-3 (나) — 삼키는 것은 입력 깨짐(DeliverError · OSError · ValueError · duckdb.Error)
    뿐이다. manifest 가 JSON 은 맞지만 객체가 아니면(`[1, 2]` → reader 의 `meta["date"]`
    TypeError) 코드가 다루지 못한 모양이라 그대로 전파한다(CLI 는 rc 3)."""
    ev, fb = _t50_roots(world, tmp_path)
    (fb / "_runs" / "20260824_morning.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(TypeError):
        build_daily(FRI, "evening", model_root=ev, fi_root=world["fi"], out_root=tmp_path / "o",
                    trend_fallback_root=fb)


def test_t50_morning_excel_ignores_the_fallback_root(world: dict[str, Path], daily,
                                                     tmp_path: Path) -> None:
    """회귀 — 아침 판(morning) 엑셀은 대체 루트를 줘도 그대로다. world 아침 루트에도 1M 비교
    판이 없어 basis 가드가 없으면 08-24 로 메워지는 조건이다. 모든 시트 값이 대체 인자 없는
    판(daily)과 같다(메타 '엑셀 생성 시각' 만 다르다)."""
    _, fb = _t50_roots(world, tmp_path)
    res = build_daily(FRI, "morning", model_root=world["model"], fi_root=world["fi"],
                      out_root=tmp_path / "o", trend_fallback_root=fb)
    got, (_, want) = load_workbook(res.path), daily

    def values(wb) -> dict[str, list[list[object]]]:
        return {ws.title: [[c.value for c in row] for row in ws.iter_rows()
                           if row[0].value != "엑셀 생성 시각"] for ws in wb.worksheets}
    assert values(got) == values(want)
    assert dict(_meta_pairs(got))["1M 비교 데이터"] == "없음(그 전 데이터가 없다)"


def test_rank_delta_cell_color_is_centered_on_zero(daily) -> None:
    """E-06(Q8) — Δ순위 1M 칸 색의 가운데는 0 이다(음수 빨강 · 양수 초록, 끝점 ±M 대칭).
    옛 코드는 3색 백분위 10/50/90 이라 가운데가 중앙값(10-02 판 −21.5) — 소폭 하락이 연두로
    보였다."""
    _, wb = daily
    ws = wb["Score"]
    letter = ws.cell(7, header(ws)["Δ순위 1M"]).column_letter
    cs = next(r.colorScale for rng in ws.conditional_formatting for r in rng.rules
              if str(rng.sqref).startswith(letter))
    assert [v.type for v in cs.cfvo] == ["num", "num", "num"]
    lo, mid, hi = (float(v.val) for v in cs.cfvo)
    assert mid == 0 and lo == -hi and hi > 0
    assert [c.rgb[-6:] for c in cs.color] == ["F8696B", "FFEB84", "63BE7B"]


def test_scale_zero_mid_endpoints_are_symmetric_90th_magnitude() -> None:
    from deliver.qpack import scale_zero_mid
    vals: list[object] = [-192.0, -21.5, -1.0, 0.0, 5.0, 174.9, None, "x"]

    def points(values: list[object]) -> tuple[float, ...]:
        cs = scale_zero_mid(values).colorScale
        assert cs is not None and [v.type for v in cs.cfvo] == ["num"] * 3
        return tuple(float(v.val) for v in cs.cfvo)
    lo, mid, hi = points(vals)
    mags = sorted(abs(float(v)) for v in vals if isinstance(v, float))
    assert mid == 0 and hi == pytest.approx(quantile(mags, 0.9)) and lo == -hi
    assert points([None, 0]) == (-1.0, 0.0, 1.0)            # 값이 없거나 전부 0 이면 ±1


def test_daily_earnings_estimated_prior_year_yoy_is_grey_too(tmp_path: Path) -> None:
    """Q10 — Y−1 칸을 추정치로 채우면(회색 'E', N-19) 그 옆 y-y 칸도 회색 글자다.
    확정치 칸 옆 y-y 는 그대로."""
    day = "2027-01-04"
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, day, write_fi_day(fi_root, day, edit=_roll_to_2027))
    res = build_daily(day, "morning", model_root=model_root, fi_root=fi_root, out_root=tmp_path)
    ws = load_workbook(res.path)["Earnings"]
    rows = rows_by_code(ws)
    for occurrence in (0, 1, 2):                       # 매출 · 영업이익 · 순이익
        yy = col_of(ws, "YoY (2025-2026)", occurrence)   # YoY 칸은 머리글로 찾는다(위치 가정 없음)
        est_y = ws.cell(rows[tick(10)], yy)             # 2026 = 추정치 → y-y 회색
        assert est_y.value is not None and est_y.font.color.rgb.endswith("7F7F7F")
        act_y = ws.cell(rows[tick(0)], yy)              # 2026 = 확정치 → y-y 그대로
        assert act_y.font.color is None or not act_y.font.color.rgb.endswith("7F7F7F")
    grey = {code for code, r in rows.items()
            if (f := ws.cell(r, col_of(ws, "YoY (2025-2026)")).font).color is not None
            and f.color.rgb.endswith("7F7F7F")}
    est = {code for code, r in rows.items()
           if '"E"' in ws.cell(r, col_of(ws, "2026")).number_format}
    assert grey == est                                   # y-y 회색 = 'E' 칸 행 그대로


# ── Earnings 순이익 = 지배주주 순이익(결함 2) ──────────────────────────────────────
CTRL, CTRL_EST, CTRL_NONE = tick(20), tick(21), tick(22)
STAGE_PIN = "b_pin_0001"
NI_DEF = "지배주주 순이익(컨센서스 표 기준 — 확정·추정 같은 정의)"


def _controlling_ni(table: str, rows: list[dict[str, object]]) -> None:
    """LG에너지솔루션(373220, 10-14 판) 모양 — 연결 순이익(fi_fin_summary 60)은 흑자인데 지배 순이익
    (컨센서스 연간 표 확정 −10,728.1 · 2026E −2,520.9)은 적자. 컨센서스 연간 표 창은 실제처럼
    Y−1~Y+1 이라 2024 행이 없다(stage 에서 읽는다). CTRL_EST = 2025 확정 지배 행 없이 2025
    추정치만, CTRL_NONE = 둘 다 없음(연결 순이익은 둘 다 있다)."""
    if table == "fi_consensus_annual":
        rows[:] = [r for r in rows if r["period"] != "2024/12" and not (
            r["ticker"] in (CTRL_EST, CTRL_NONE) and r["period"] == "2025/12")]
        for r in rows:
            if r["ticker"] == CTRL:
                r["ni"] = -10728.1
    elif table == "fi_consensus":
        for r in rows:
            if r["ticker"] == CTRL and r["horizon"] == "cur" and r["target_period"] == "2026/12":
                r["ni"] = -2520.9
        rows.append({"ticker": CTRL_EST, "target_period": "2025/12", "horizon": "cur",
                     "revenue": 1310.0, "op": 141.0, "ni": 55.0, "eps": 900.0, "per": 12.5,
                     "pbr": 1.1, "n_analysts": 5, "obs_date": "2026-09-24",
                     "fetched_date": "2026-09-24"})


def write_stage_consensus_annual(root: Path, build_id: str,
                                 rows: list[tuple[str, str, str, str, float]]) -> None:
    """stage `stg_consensus_annual` 판 하나 — (종목, 수집일, 기 YYYYMM, 종류, 순이익). 나중에 쓴 판이
    current."""
    vdir = root / "stg_consensus_annual" / f"v={build_id}"
    vdir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "ticker": pa.array([r[0] for r in rows], pa.string()),
        "fetched_date": pa.array([date.fromisoformat(r[1]) for r in rows], pa.date32()),
        "period_label": pa.array([f"{r[2][:4]}.{r[2][4:]}({r[3]})" for r in rows], pa.string()),
        "period": pa.array([r[2] for r in rows], pa.string()),
        "period_kind": pa.array([r[3] for r in rows], pa.string()),
        "ni": pa.array([r[4] for r in rows], pa.float64())}), vdir / "part0.parquet")
    manifest.commit(root / "stg_consensus_annual", manifest.BuildRecord(
        build_id=build_id, snapshot_id="", rules_version="v_test",
        built_at_utc="2026-09-26T00:00:00+00:00", n_rows=len(rows), content_hash="test",
        partitions=[{"path": f"v={build_id}", "n_rows": len(rows)}]))


# 고정 판 행 — asof(09-25) 뒤 수집 · 추정('E') 행이 섞여 있다(둘 다 쓰지 않는다)
PINNED_ROWS = [
    (CTRL, "2026-09-24", "202412", "A", -10187.4),
    (CTRL, "2026-09-24", "202412", "E", 777.0),
    (CTRL, "2026-09-28", "202412", "A", 999.0),
    (CTRL_EST, "2026-09-24", "202412", "A", 50.0),
    (CTRL_NONE, "2026-09-24", "202412", "A", 50.0),
    (tick(10), "2026-09-24", "202412", "A", 50.0)]
# 고정 판과 같은 수집일 값을 다르게 가진 current 판 — 고정 판을 읽는지 가린다
REWRITTEN = [(CTRL, "2026-09-24", "202412", "A", 12345.0)]


def _controlling_board(tmp_path: Path, pin: str,
                       current: list[tuple[str, str, str, str, float]] | None = REWRITTEN) -> Path:
    """`_controlling_ni` 판(FRI 아침) + stage 2024 확정 지배 순이익. fi 판 JSON 은 stage 판을 `pin` 에
    고정한다. stage 판은 `PINNED_ROWS`(STAGE_PIN) 뒤에 `current` 행의 판이 current 로 선다 —
    `current` 가 None 이면 stage 표 자체가 없다. 엑셀 경로를 돌려준다."""
    model_root, fi_root, st = tmp_path / "model", tmp_path / "fi", tmp_path / "stage"
    fi_bid = write_fi_day(fi_root, FRI, edit=_controlling_ni)
    write_model_day(model_root, FRI, fi_bid)
    if current is not None:
        write_stage_consensus_annual(st, STAGE_PIN, PINNED_ROWS)
        write_stage_consensus_annual(st, "b_new_0002", current)
    runs = fi_root / "_runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "20260925_morning.json").write_text(json.dumps({
        "build_id": fi_bid, "asof": FRI, "stage_root": str(st),
        "stage_builds": {"stg_consensus_annual": pin}}), encoding="utf-8")
    return build_daily(FRI, "morning", model_root=model_root, fi_root=fi_root,
                       out_root=tmp_path / "out").path


def test_daily_earnings_net_income_is_controlling_in_every_cell(tmp_path: Path) -> None:
    """결함 2 — 순이익 행 모든 칸(2025 확정 · 2026E · 2027E · 각 YoY)이 지배주주 순이익 하나다.
    2025 확정 = 같은 fi 판 컨센서스 연간 표 확정 행(연결 60 이 아니다), 2024 = 고정 stage 판
    stg_consensus_annual 확정 행(asof 이하 최신 수집). 매출 · 영업이익 칸은 그대로(연결 확정치)."""
    wb = load_workbook(_controlling_board(tmp_path, STAGE_PIN))
    ws = wb["Earnings"]
    rows = rows_by_code(ws)
    r = rows[CTRL]
    ni = {h: col_of(ws, h, 2) for h in ("2025", "YoY (2024-2025)", "2026E", "YoY (2025-2026E)",
                                        "2027E", "YoY (2026E-2027E)")}
    assert ws.cell(r, ni["2025"]).value == -10728.1
    assert ws.cell(r, ni["2025"]).number_format == "#,##0"               # 확정치 — 'E' 아님
    assert ws.cell(r, ni["YoY (2024-2025)"]).value == "적자지속"           # −10,187.4 → −10,728.1
    assert ws.cell(r, ni["2026E"]).value == -2520.9
    assert ws.cell(r, ni["YoY (2025-2026E)"]).value == "적자지속"          # 연결 기준이면 '적자전환'
    assert ws.cell(r, ni["2027E"]).value == 80
    assert ws.cell(r, ni["YoY (2026E-2027E)"]).value == "흑자전환"
    # 매출 · 영업이익은 연결 확정치 그대로(20번 종목: 2024 → 2025 매출 1,200 → 1,300 · 영업이익 120 → 140)
    assert ws.cell(r, col_of(ws, "2025", 0)).value == 1300
    rev_y, op_y = (ws.cell(r, col_of(ws, "YoY (2024-2025)", k)).value for k in (0, 1))
    assert rev_y == pytest.approx((1300 / 1200 - 1) * 100)
    assert ws.cell(r, col_of(ws, "2025", 1)).value == 140
    assert op_y == pytest.approx((140 / 120 - 1) * 100)
    # 확정 지배 순이익이 없으면 같은 결산기 추정치(회색 'E') — 연결 순이익(60)으로 대신하지 않는다
    est = ws.cell(rows[CTRL_EST], ni["2025"])
    assert est.value == 55 and '"E"' in est.number_format and est.font.color.rgb.endswith("7F7F7F")
    est_y = ws.cell(rows[CTRL_EST], ni["YoY (2024-2025)"])
    assert est_y.value == pytest.approx((55 / 50 - 1) * 100)
    assert est_y.font.color.rgb.endswith("7F7F7F")
    assert ws.cell(rows[CTRL_NONE], ni["2025"]).value is None              # 둘 다 없으면 빈칸
    assert ws.cell(rows[CTRL_NONE], ni["YoY (2024-2025)"]).value is None
    assert ws.cell(rows[CTRL_NONE], ni["YoY (2025-2026E)"]).value is None
    y10 = ws.cell(rows[tick(10)], ni["YoY (2024-2025)"]).value
    assert y10 == pytest.approx((60 / 50 - 1) * 100)
    # 정의 글(Meta 열 사전) — 순이익 열만 지배주주 순이익이라고 밝힌다
    meta = wb["Meta"]
    defs = {(meta.cell(rr, 1).value, meta.cell(rr, 2).value): str(meta.cell(rr, 3).value)
            for rr in range(1, meta.max_row + 1)}
    for head in ni:
        assert NI_DEF in defs[("Earnings", f"순이익(억원 · YoY %) · {head}")], head
    assert NI_DEF not in defs[("Earnings", "매출(억원 · YoY %) · 2025")]


def test_daily_earnings_gc_pinned_two_years_ago_falls_back_to_current_as_of(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Y−2(2024) 고정 stage 판이 지워졌으면(keep=3 — 늦은 재생성) current 판을 같은 규칙(종목마다
    fetched_date ≤ fi asof 최신 수집 · 확정 행)으로 읽는다. stage 이력은 덧붙기만 해 current 판에
    asof 를 걸면 고정 판과 같은 값이다 — 2025 YoY 가 통째로 비지 않는다. 대체했다는 경고 한 줄."""
    history = [*PINNED_ROWS, (CTRL, "2026-09-29", "202412", "A", 888.0)]   # 그 뒤 수집이 덧붙음
    with caplog.at_level("WARNING"):
        wb = load_workbook(_controlling_board(tmp_path, "b_gone_0000", current=history))
    ws = wb["Earnings"]
    rows = rows_by_code(ws)
    y25 = col_of(ws, "YoY (2024-2025)", 2)
    assert ws.cell(rows[CTRL], y25).value == "적자지속"                   # −10,187.4 → −10,728.1
    assert ws.cell(rows[tick(10)], y25).value == pytest.approx((60 / 50 - 1) * 100)
    assert ws.cell(rows[CTRL_EST], y25).value == pytest.approx((55 / 50 - 1) * 100)
    assert any("고정 판 없음 — current 판 + asof 필터로 대체" in rec.getMessage()
               for rec in caplog.records if rec.levelname == "WARNING")


def test_daily_earnings_unreadable_two_years_ago_blanks_only_that_yoy(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Y−2(2024) 확정 지배 순이익을 고정 판 · current 판 모두 못 읽으면(stage 표 없음) 2025 YoY 칸만
    빈다 — 연결 순이익으로 대신하지 않는다. 다른 칸은 그대로, 경고 로그."""
    with caplog.at_level("WARNING"):
        wb = load_workbook(_controlling_board(tmp_path, "b_gone_0000", current=None))
    ws = wb["Earnings"]
    rows = rows_by_code(ws)
    y25 = col_of(ws, "YoY (2024-2025)", 2)
    assert ws.cell(rows[CTRL], y25).value is None
    assert ws.cell(rows[tick(10)], y25).value is None
    assert ws.cell(rows[CTRL], col_of(ws, "2025", 2)).value == -10728.1
    assert ws.cell(rows[CTRL], col_of(ws, "YoY (2025-2026E)", 2)).value == "적자지속"
    assert ws.cell(rows[CTRL], col_of(ws, "YoY (2024-2025)", 1)).value == pytest.approx(
        (140 / 120 - 1) * 100)
    assert any("YoY 칸을 비운다" in rec.getMessage() for rec in caplog.records
               if rec.levelname == "WARNING")


@pytest.mark.parametrize("case", ["missing", "broken"])
def test_daily_earnings_unreadable_consensus_annual_leaves_only_those_cells_blank(
        tmp_path: Path, caplog: pytest.LogCaptureFixture, case: str) -> None:
    """fi_consensus_annual 을 못 읽으면(옛 fi 판 — 표 없음 · 깨진 파일) 엑셀은 그대로 나가고 순이익
    확정 칸만 추정치 · 빈칸이다(연결 순이익으로 대신하지 않는다). 경고 로그."""
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    fi_bid = write_fi_day(fi_root, FRI)
    write_model_day(model_root, FRI, fi_bid)
    part = fi_root / "fi_consensus_annual" / f"v={fi_bid}"
    shutil.rmtree(part)
    if case == "broken":
        part.mkdir(parents=True)
        (part / "part0.parquet").write_bytes(b"not a parquet file")
    with caplog.at_level("WARNING"):
        res = build_daily(FRI, "morning", model_root=model_root, fi_root=fi_root,
                          out_root=tmp_path / "out")
    ws = load_workbook(res.path)["Earnings"]
    r = rows_by_code(ws)[tick(10)]
    assert ws.cell(r, col_of(ws, "2025", 2)).value is None        # 연결 60 이 아니다
    assert ws.cell(r, col_of(ws, "2026E", 2)).value == 70
    assert ws.cell(r, col_of(ws, "2025", 0)).value == 1200        # 매출은 그대로
    assert any("fi_consensus_annual" in rec.getMessage() for rec in caplog.records
               if rec.levelname == "WARNING")


def test_daily_unreadable_previous_universe_keeps_the_excel(
        world: dict[str, Path], tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """전일 판 fi_universe 를 못 읽으면(깨진 파일) 엑셀은 그대로 나가고 오늘 모집단 밖 전일 종목만
    대분류 없음 · 이름 없이(코드만) 다룬다. 경고 로그."""
    model, fi = tmp_path / "model", tmp_path / "fi"
    shutil.copytree(world["model"], model)
    shutil.copytree(world["fi"], fi)
    fri, thu = find_run(model, FRI, "morning"), find_run(model, THU, "morning")
    assert fri is not None and thu is not None
    path = model / PRIMARY / f"v={fri.build_id}" / "scores.parquet"
    tbl = pq.read_table(path)
    pq.write_table(tbl.filter(pc.not_equal(tbl["ticker"], tick(0))), path)
    for f in (fi / "fi_universe" / f"v={thu.fi_build_id}").glob("*.parquet"):
        f.write_bytes(b"not a parquet file")
    with caplog.at_level("WARNING"):
        wb = load_workbook(build_daily(FRI, "morning", model_root=model, fi_root=fi,
                                       out_root=tmp_path / "out").path)
    assert dict(_meta_pairs(wb))["전일 후보 중 오늘 모집단 밖"] == tick(0)
    msgs = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    assert any("전일 fi_universe" in m for m in msgs)
    assert any("전일 대분류" in m for m in msgs)


# ── scope 실물 모양 판(실제 model.build — 주 모델 scope@1.0) ─────────────────────
# 연간 확정 PER·PBR·배당수익률이 없는 종목 → scope 밸류 버킷 결측(279570 모양)
NO_VAL = "100007"
# 연간 행은 있는데(WISE 매출·PER) 손익·자산 재료가 다 빈 종목 → 퀄리티 = 변동성만(241560 모양)
VOL_ONLY = "100011"
SCOPE_FI_BID = "m_20260929T000500_000000Z"
LONE = "100040"            # 대분류 G50 의 유일한 종목


def _write_fi_tree(b, fi_root: Path, fi_bid: str, day: str) -> None:
    """test_model_v4_rank.Board → factor_inputs 고정 경로(8표 + latest)."""
    from model import build as mbuild
    fi = b.fi()
    for name, t in FI_TABLES.items():
        out = fi_root / name / f"v={fi_bid}" / "part0.parquet"
        mbuild.write_parquet(list(fi.tables.get(name, ())),
                             {c.name: c.dtype for c in t.columns}, out)
        (out.parent / "_meta.json").write_text(json.dumps(
            {"table": name, "build_id": fi_bid, "basis": "morning", "date": day}))
    (fi_root / "latest_morning.json").write_text(json.dumps(
        {"layer": "factor_inputs", "status": "ok", "build_id": fi_bid, "date": day,
         "basis": "morning"}))


def _scope_board(root: Path, edit: Callable[[object], None] | None = None):
    """실제 `model.build` 판(40종목, 다섯 spec) — 주 모델 scope@1.0 은 원값을 점수 표 열로 싣고
    지표 긴 표는 0행이다(서버 10-02 판과 같은 모양). NO_VAL 만 밸류 재료가 없다.
    `edit(board)` = fi 를 쓰기 직전에 합성 판을 고친다."""
    from model import build as mbuild
    from test_model_v4_rank import Board, _full
    b = Board()
    for i in range(40):
        _full(b, f"{100000 + i:06d}", i, sector=("G10", "G20", "G30")[i % 3])
    _full(b, LONE, 40, sector="G50")        # 혼자인 대분류 — 업종 z 는 유니버스 z 로 되돌린다
    for r in b.tables["fi_prices"]:
        r["close"] = round(float(r["close"]))                  # type: ignore[arg-type]
    for r in b.tables["fi_fin_summary"]:
        if r["period_type"] == "annual" and r["ticker"] != NO_VAL:
            k = int(str(r["ticker"])) % 11
            r.update(per=6.0 + k, pbr=0.6 + 0.1 * k, dividend_yield=0.5 + 0.2 * k, roa=2.0 + k,
                     debt_ratio=60.0 + 5 * k, gross_profit=100.0 + 3 * k)
        if r["period_type"] == "annual" and r["ticker"] == VOL_ONLY:
            r.update(roa=None, debt_ratio=None, gross_profit=None, fcf=None, total_assets=None)
    if edit is not None:
        edit(b)
    fi_root = root / "fi"
    _write_fi_tree(b, fi_root, SCOPE_FI_BID, "2026-09-28")
    res = mbuild.build("20260928", "morning", root / "model", fi_root,
                       min_prices_on_d=10, min_ranked=10)
    assert res.ok and res.primary_spec == "scope@1.0", res.specs
    d = build_daily("2026-09-28", "morning", model_root=root / "model", fi_root=fi_root,
                    out_root=root / "out")
    scores = {str(r["stock_code"]): r for r in duckdb_rows(
        root / "model" / "scope@1.0" / f"v={res.build_id}" / "scores.parquet")}
    return res, load_workbook(d.path), scores, root


@pytest.fixture(scope="module")
def scope_board(tmp_path_factory: pytest.TempPathFactory):
    return _scope_board(tmp_path_factory.mktemp("scope"))


def duckdb_rows(path: Path) -> list[dict[str, object]]:
    import duckdb
    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def test_scope_bucket_missing_is_marked_and_counted(scope_board) -> None:
    """E-02(N-26 4.5) — scope 는 지표 긴 표가 0행이라 옛 코드는 `t in view.ind` 로 '점수 대상'을
    가려 결측 버킷을 빈칸으로 두고 결측 축·메타 결측 수를 0 으로 냈다. 점수 행이 있으면 점수 대상:
    빈 버킷 = '결측(원천없음)', 결측 축 = 밸류, 메타 '축별 결측 수' 밸류 1."""
    _, wb, scores, _ = scope_board
    assert scores[NO_VAL]["valuation_score"] is None
    assert all(r["valuation_score"] is not None for t, r in scores.items() if t != NO_VAL)
    ws = wb["Score"]
    h, rows = header(ws), rows_by_code(ws)
    r = rows[NO_VAL]
    assert ws.cell(r, group_col(ws, "밸류", "유니버스 (Z)")).value == "결측(원천없음)"   # z 축
    assert ws.cell(r, group_col(ws, "밸류", "업종 (Z)")).value == "결측(원천없음)"
    assert ws.cell(r, h["축"]).value == "밸류"
    assert {ws.cell(rows[t], h["축"]).value for t in scores if t != NO_VAL} <= {None, ""}
    meta = dict(_meta_pairs(wb))
    assert "밸류 1" in str(meta["축별 결측 수"]) and "모멘텀 0" in str(meta["축별 결측 수"])


# scope 점수 표 원값 27열 + 표식 6열(contracts.V3_SCORE_COLUMNS) — 엑셀 (6행 그룹, 7행 머리 —
# 줄바꿈은 공백). 그룹에 있는 말(수익률 · 영업이익 추정 · 단위)은 머리에서 되풀이하지 않는다
_MOM = "모멘텀 · 수익률(비율)"
_OP, _NI = "리비전 · 영업이익 추정 변화(비율)", "리비전 · 순이익 추정 변화(비율)"
_FLOW = "수급 · 순매수/시총(%)"
SCOPE_RAW_HEADERS = {
    "r1m": (_MOM, "1M"), "r3m": (_MOM, "3M"), "r6m": (_MOM, "6M"), "r9m": (_MOM, "9M"),
    "r12m": (_MOM, "12M"),
    "op_change_1w": (_OP, "1W"), "op_1w_flag": (_OP, "1W 표식"),
    "op_change_1m": (_OP, "1M"), "op_1m_flag": (_OP, "1M 표식"),
    "op_change_3m": (_OP, "3M"), "op_3m_flag": (_OP, "3M 표식"),
    "ni_change_1w": (_NI, "1W"), "ni_1w_flag": (_NI, "1W 표식"),
    "ni_change_1m": (_NI, "1M"), "ni_1m_flag": (_NI, "1M 표식"),
    "ni_change_3m": (_NI, "3M"), "ni_3m_flag": (_NI, "3M 표식"),
    "flow_inst_5d": (_FLOW, "기관 5일"), "flow_inst_20d": (_FLOW, "기관 20일"),
    "flow_for_5d": (_FLOW, "외국인 5일"), "flow_for_20d": (_FLOW, "외국인 20일"),
    "flow_pe_5d": (_FLOW, "사모 5일"), "flow_pe_20d": (_FLOW, "사모 20일"),
    "qual_gpa": ("퀄리티", "GP/A (비율)"), "qual_roa": ("퀄리티", "ROA (%)"),
    "qual_fcf_assets": ("퀄리티", "FCF/자산 (비율)"), "qual_debt_ratio": ("퀄리티", "부채비율 (%)"),
    "qual_gpa_change": ("퀄리티", "GP/A 변화 (비율)"),
    "qual_std_20d": ("퀄리티", "20일 변동성 (비율)"),
    "val_per": ("밸류", "PER (배)"), "val_pbr": ("밸류", "PBR (배)"),
    "val_ev_ebitda": ("밸류", "EV/EBITDA (배)"), "val_dividend_yield": ("밸류", "배당수익률 (%)"),
}


def raw_col(ws, key: str) -> int:
    return group_col(ws, *SCOPE_RAW_HEADERS[key])


def head_note(ws, col: int) -> str:
    """7행 머리 칸 메모 글(300px 로 접힌 줄을 다시 잇는다)."""
    cm = ws.cell(7, col).comment
    return "" if cm is None else cm.text.replace("\n", " ").replace("\xa0", " ")


def test_scope_raw_sheet_carries_the_score_table_raw_columns(scope_board) -> None:
    """E-10(Q7) G1 — scope 엑셀 '점수 원자료'에 점수 표 원값 27열 + 표식 6열을 엔진 값 그대로
    싣는다. 옛 코드는 지표 긴 표(v4 전용)만 읽어 scope 원자료 시트가 코드·이름·기준 열뿐이었다
    (10-02 발송본)."""
    from model.contracts import V3_SCORE_COLUMNS
    _, wb, scores, _ = scope_board
    assert set(SCOPE_RAW_HEADERS) <= set(V3_SCORE_COLUMNS) and len(SCOPE_RAW_HEADERS) == 33
    ws = wb["Raw"]
    rows = rows_by_code(ws)
    groups = [str(c.value) for c in ws[6] if c.value is not None]
    assert groups == ["종목", _MOM, _OP, _NI, _FLOW, "퀄리티", "밸류", "데이터 기준"]
    # 리비전 두 블록은 구분 열 없이 붙는다(한 버킷)
    assert raw_col(ws, "ni_change_1w") == raw_col(ws, "op_3m_flag") + 1
    t = "100005"
    for key in SCOPE_RAW_HEADERS:
        got, want = ws.cell(rows[t], raw_col(ws, key)).value, scores[t][key]
        if isinstance(want, float):
            assert got == pytest.approx(want), key                   # 배율 없이 엔진 값 그대로
        else:
            assert got == want, key                                  # 표식 글자 · 값 없음 = 빈칸
        # 정의 = 머리 칸 메모, 열 이름부터
        assert head_note(ws, raw_col(ws, key)).startswith(f"{key}:"), key
    assert isinstance(ws.cell(rows[t], raw_col(ws, "r1m")).value, float)
    assert ws.cell(rows[t], raw_col(ws, "r1m")).number_format == "0.0000"
    defs = {key: head_note(ws, raw_col(ws, key)) for key in SCOPE_RAW_HEADERS}
    assert "비율" in defs["r1m"] and "%" in defs["val_dividend_yield"]
    assert "순매수" in defs["flow_for_5d"] and "시총" in defs["flow_for_5d"]
    unit = dict(_meta_pairs(wb))["Raw 단위"]
    assert "r1m" in str(unit) and "val_dividend_yield" in str(unit) and "flow_" in str(unit)


def test_scope_sector_sheet_fills_per_revision_and_return(scope_board) -> None:
    """E-10(Q7) — 업종 시트 '업종 지표' 3열을 scope 점수 표 원값으로 채운다: PER 중앙값(val_per) ·
    리비전 상향 비율(op_change_1m > 0, 값 있는 종목 중) · 1M 수익률(r1m 시총가중 × 100).
    옛 코드는 v4 지표 긴 표만 봐서 scope 판에선 세 열이 전부 빈칸이었다."""
    res, wb, scores, root = scope_board
    ws = wb["Sector"]
    h = header(ws)
    first = {ws.cell(r, h["코드"]).value: r for r in range(8, ws.max_row + 1)
             if ws.cell(r, h["구분"]).value == "대분류"}
    uni = duckdb_rows(root / "fi" / "fi_universe" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    caps = {str(r["ticker"]): r["market_cap"] for r in uni if isinstance(r["market_cap"], float)}
    l1 = {str(r["ticker"]): r["sector_l1"] for r in uni}
    for code, r in first.items():
        members = [t for t in scores if l1[t] == code]
        per = [scores[t]["val_per"] for t in members if scores[t]["val_per"] is not None]
        rv = [scores[t]["op_change_1m"] for t in members if scores[t]["op_change_1m"] is not None]
        r1 = {t: scores[t]["r1m"] for t in members if scores[t]["r1m"] is not None}
        w = sum(caps[t] for t in r1)
        assert ws.cell(r, h["PER 중앙값 (배)"]).value == pytest.approx(statistics.median(per))
        assert ws.cell(r, h["리비전 상향 비율(%)"]).value == pytest.approx(
            sum(1 for v in rv if v > 0) / len(rv) * 100)
        assert ws.cell(r, h["1M 수익률 (%)"]).value == pytest.approx(
            sum(v * caps[t] for t, v in r1.items()) / w * 100)


def test_v4_comparison_columns_are_left_out_until_fixed(scope_board) -> None:
    """N-27 §8-17 — v4 비교 열(v4_rank@*)은 결함 수정 전까지 점수 시트 '다른 모델 순위'와
    '모델 비교' 시트에서 뺀다. v3 원본·v2 원본 비교와 '최대 차이'(남은 모델끼리)는 남고, 메타에
    한 줄 남긴다.
    v4 판 계산·저장은 그대로다(판 게이트 줄도 그대로)."""
    res, wb, _, _ = scope_board
    assert {"v4_rank@0.1", "v4_rank@0.2"} <= set(res.specs)
    sc, mc = wb["Score"], wb["Compare"]
    for ws in (sc, mc):
        h = header(ws)
        assert "v3 원본" in h and "v2 원본" in h, ws.title
        assert "v4 기본" not in h and "v4 동일가중" not in h, ws.title
    h, rows = header(sc), rows_by_code(sc)
    for t, r in rows.items():
        got = [sc.cell(r, h[k]).value for k in ("순위", "v3 원본", "v2 원본")]
        ranks = [x for x in got if x is not None]
        want = max(ranks) - min(ranks) if len(ranks) >= 2 else None
        assert sc.cell(r, h["최대 차이"]).value == want, t
    meta = dict(_meta_pairs(wb))
    left_out = str(meta["비교에서 뺀 모델"])
    assert "N-27" in left_out and "v4_rank@0.1" in left_out and "v4_rank@0.2" in left_out
    assert "v4_rank" not in str(meta["비교 모델"])
    assert "데이터 검사 v4_rank@0.1" in meta                                   # 판은 그대로


# ── 4-2a 검토 후속(G-27 표기 · 순액 매출 y-y · 원값 열 부분 결측 · insert_after) ─────────────
VOL_ONLY_NOTE = "퀄리티 변동성만"          # 짧은 표지(샘플 22) — 뜻은 비고 머리 칸 메모 범례
NET_NOTE = "매출 순액"


def test_scope_volatility_only_quality_is_noted(scope_board) -> None:
    """G-27(컨트롤러 결정) — scope 점수 행의 퀄리티 손익 지표(qual_gpa·roa·fcf_assets·debt_ratio·
    gpa_change)가 다 비고 변동성(qual_std_20d)만 있으면 비고에 '퀄리티 = 변동성만(손익 지표 없음)'.
    원인(외화 재무 등)은 적지 않는다 — 다른 이유로 비는 종목도 있다(477850·0011T0)."""
    _, wb, scores, _ = scope_board
    row = scores[VOL_ONLY]
    assert row["qual_std_20d"] is not None and row["quality_score"] is not None
    assert all(row[k] is None for k in ("qual_gpa", "qual_roa", "qual_fcf_assets",
                                        "qual_debt_ratio", "qual_gpa_change"))
    ws = wb["Score"]
    h, rows = header(ws), rows_by_code(ws)
    notes = {t: ws.cell(r, h["비고"]).value for t, r in rows.items()}
    assert VOL_ONLY_NOTE in str(notes[VOL_ONLY]) and "외화" not in str(notes[VOL_ONLY])
    assert [t for t, n in notes.items() if VOL_ONLY_NOTE in str(n)] == [VOL_ONLY]
    legend = ws.cell(7, h["비고"]).comment.text
    assert f"■ {VOL_ONLY_NOTE}: 퀄리티를 주가 변동성 하나로만 계산" in legend


def _net_revenue(table: str, rows: list[dict[str, object]]) -> None:
    """12번 종목 = 증권사처럼 연간 확정 매출이 순액(순영업이익, fi1.2.0 `revenue_basis` 'net')
    두 해, 13번 = 2025 만 순액(2024 총액). 컨센서스 매출은 총액 그대로."""
    if table == "fi_fin_summary":
        for r in rows:
            net = r["ticker"] == tick(12) or (r["ticker"] == tick(13) and r["period"] == "2025/12")
            if r["period_type"] == "annual" and net:
                r["revenue_basis"] = "net"


def test_net_revenue_yoy_against_gross_estimate_is_left_blank(tmp_path: Path) -> None:
    """4-2b 명세 검토 #1 — 확정 매출이 순액(순영업이익)인데 추정치는 총액이라 2025 순액 대 2026E
    총액 y-y 가 가짜 급증(현장 증권·카드 6종목 +180~+1,100%)으로 찍혔다. 한쪽만 순액인 매출 y-y
    칸은 비우고 점수 시트 비고에 적는다. 둘 다 순액(12번 2025 대 2024)·영업이익·총액 종목은
    그대로."""
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, FRI, write_fi_day(fi_root, FRI, edit=_net_revenue))
    wb = load_workbook(build_daily(FRI, "morning", model_root=model_root, fi_root=fi_root,
                                   out_root=tmp_path).path)
    ws = wb["Earnings"]
    rows = rows_by_code(ws)
    y25, y26, y27 = (col_of(ws, y, 0) for y in (
        "YoY (2024-2025)", "YoY (2025-2026E)", "YoY (2026E-2027E)"))
    r12, r13, r10 = rows[tick(12)], rows[tick(13)], rows[tick(10)]
    assert ws.cell(r12, y26).value is None                        # 2026E 총액 vs 2025 순액
    num = (int, float)                                            # 엑셀 왕복에서 0.0 은 0
    assert isinstance(ws.cell(r12, y25).value, num)               # 2025 순액 vs 2024 순액
    assert isinstance(ws.cell(r12, y27).value, num)               # 추정 vs 추정
    assert ws.cell(r13, y25).value is None and ws.cell(r13, y26).value is None
    assert isinstance(ws.cell(r12, col_of(ws, "YoY (2025-2026E)", 1)).value, num)  # 영업이익 그대로
    assert ws.cell(r10, y26).value == pytest.approx((1300 / 1200 - 1) * 100)       # 총액 종목
    sc = wb["Score"]
    note, srows = header(sc)["비고"], rows_by_code(sc)
    assert sc.cell(srows[tick(12)], note).value == NET_NOTE
    assert sc.cell(srows[tick(13)], note).value == NET_NOTE
    assert sc.cell(srows[tick(10)], note).value is None


def test_scope_raw_columns_survive_a_missing_engine_column(scope_board, tmp_path: Path,
                                                          caplog: pytest.LogCaptureFixture) -> None:
    """검토 사소 1 — 원값 열은 '전부 아니면 전무'가 아니다. 엔진(v3_zscore)으로 판정하고, 점수 표에
    없는 열만 빈칸으로 두며 빠진 열 이름을 로그 한 줄로 남긴다. 옛 코드는 열 하나만 빠져도 원자료
    33열·업종 3열·메타 단위 줄이 통째로 사라졌다."""
    import shutil

    import duckdb
    res, _, scores, root = scope_board
    model = tmp_path / "model"
    shutil.copytree(root / "model", model)
    path = model / "scope@1.0" / f"v={res.build_id}" / "scores.parquet"
    tmp = path.with_name("cut.parquet")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * EXCLUDE (val_ev_ebitda) FROM read_parquet('{path}')) "
                f"TO '{tmp}' (FORMAT parquet)")
    con.close()
    tmp.replace(path)
    with caplog.at_level("WARNING"):
        d = build_daily("2026-09-28", "morning", model_root=model, fi_root=root / "fi",
                        out_root=tmp_path / "out")
    wb = load_workbook(d.path)
    ws = wb["Raw"]
    rows = rows_by_code(ws)
    ev = raw_col(ws, "val_ev_ebitda")                    # 33열 모두 그대로 선다
    assert all(ws.cell(r, ev).value is None for r in rows.values())
    t = "100005"
    for key in SCOPE_RAW_HEADERS:
        if key != "val_ev_ebitda" and isinstance(scores[t][key], float):
            assert ws.cell(rows[t], raw_col(ws, key)).value == pytest.approx(scores[t][key]), key
    sec = wb["Sector"]
    hs = header(sec)
    for label in ("PER 중앙값 (배)", "리비전 상향 비율(%)", "1M 수익률 (%)", "3M 수익률 (%)"):
        assert isinstance(sec.cell(8, hs[label]).value, int | float), label
    assert "Raw 단위" in dict(_meta_pairs(wb))
    assert any("val_ev_ebitda" in rec.getMessage() for rec in caplog.records)


def test_insert_after_appends_when_the_key_is_missing(caplog: pytest.LogCaptureFixture) -> None:
    """검토 사소 6 — 끼울 자리(키)가 없어도 그날 엑셀·발송을 막지 않는다: 끝에 덧붙이고
    경고 한 줄."""
    from deliver.excel_daily import insert_after
    pairs: list[tuple[str, object]] = [("기준일", "2026-10-02")]
    with caplog.at_level("WARNING"):
        insert_after(pairs, "없는 줄", [("엑셀 생성 시각", "x")])
    assert pairs == [("기준일", "2026-10-02"), ("엑셀 생성 시각", "x")]
    assert any("없는 줄" in rec.getMessage() for rec in caplog.records)


# ── 점수 시트 축 = z(사용자 결정 10-08 '둘 다 z') ────────────────────────────────────
SCOPE_BUCKETS = (("momentum", "모멘텀"), ("revision", "리비전"), ("flow", "수급"),
                 ("quality", "퀄리티"), ("valuation", "밸류"))


def _sector_z(values: list[float], v: float) -> float:
    """손 계산 — 대분류 평균·표본 표준편차, ±3σ 로 자른 뒤 표준화(엔진 z 와 같은 식)."""
    mean, sd = statistics.mean(values), statistics.stdev(values)
    return (max(mean - 3 * sd, min(mean + 3 * sd, v)) - mean) / sd


def test_scope_score_sheet_axes_are_engine_z(scope_board) -> None:
    """z 엔진(scope·v3_zscore) 주 모델이면 점수 시트 5팩터 칸이 z 다. 유니버스 z = 엔진 버킷 점수
    (종합 점수에 들어간 값) 그대로 — 다시 순위 매기지 않는다. 업종 z = 같은 z 를 WICS 대분류 안에서
    다시 z(표본 < 5 · 표준편차 0 이면 유니버스 z). 옛 코드는 버킷 z 를 0~100 백분위로 바꿔
    실었다."""
    _, wb, scores, root = scope_board
    ws = wb["Score"]
    rows = rows_by_code(ws)
    uni = duckdb_rows(root / "fi" / "fi_universe" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    l1 = {str(r["ticker"]): r["sector_l1"] for r in uni}
    for b, name in SCOPE_BUCKETS:
        u, s = group_col(ws, name, "유니버스 (Z)"), group_col(ws, name, "업종 (Z)")
        for t, row in scores.items():
            got_u, got_s = ws.cell(rows[t], u).value, ws.cell(rows[t], s).value
            if row[f"{b}_score"] is None:
                assert got_u == got_s == "결측(원천없음)", (b, t)       # 결측 규칙 그대로
                continue
            assert got_u == pytest.approx(row[f"{b}_score"]), (b, t)
            assert -3.0 <= got_s <= 3.0, (b, t)
        # 손 계산 대조 — 100005(G30) 와 혼자인 G50 종목
        t = "100005"
        peers = [r[f"{b}_score"] for x, r in scores.items()
                 if l1[x] == l1[t] and r[f"{b}_score"] is not None]
        assert len(peers) >= 5
        assert ws.cell(rows[t], s).value == pytest.approx(_sector_z(peers, scores[t][f"{b}_score"]))
        assert ws.cell(rows[LONE], s).value == pytest.approx(scores[LONE][f"{b}_score"])
        assert ws.cell(rows[t], u).number_format == "0.0"           # 축 Z 소수 1자리(샘플 22)
    # 색 — z 칸은 고정 3색 −3·0·+3(초록 = 높음)
    for name, label in (("모멘텀", "유니버스 (Z)"), ("밸류", "업종 (Z)")):
        letter = ws.cell(7, group_col(ws, name, label)).column_letter
        cs = next(r.colorScale for rng in ws.conditional_formatting for r in rng.rules
                  if str(rng.sqref).startswith(letter))
        assert [(v.type, float(v.val)) for v in cs.cfvo] == [("num", -3.0), ("num", 0.0),
                                                            ("num", 3.0)]
        assert [c.rgb[-6:] for c in cs.color] == ["F8696B", "FFEB84", "63BE7B"]
    first_axis = group_col(ws, "모멘텀", "유니버스 (Z)")
    assert "유니버스(Z)" in str(ws.cell(5, first_axis).value)            # 축 블록 위 설명
    # Meta 열 사전 이름은 '유니버스(Z)' · '업종(Z)'(줄바꿈 공백 없이)
    meta = wb["Meta"]
    names = {meta.cell(r, 2).value for r in range(8, meta.max_row + 1)
             if meta.cell(r, 1).value == "Score"}
    assert {"모멘텀 30% · 유니버스(Z)", "밸류 10% · 업종(Z)"} <= names


def test_scope_sector_sheet_axis_means_are_z(scope_board) -> None:
    """업종 시트 축별 평균도 z 엔진이면 유니버스 z(엔진 버킷 점수)의 업종 평균 — 머리글
    '축별 평균(Z)'."""
    _, wb, scores, root = scope_board
    ws = wb["Sector"]
    assert "축별 평균(Z)" in [str(c.value) for c in ws[6] if c.value is not None]
    h = header(ws)
    uni = duckdb_rows(root / "fi" / "fi_universe" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    l1 = {str(r["ticker"]): r["sector_l1"] for r in uni}
    first = {ws.cell(r, h["코드"]).value: r for r in range(8, ws.max_row + 1)
             if ws.cell(r, h["구분"]).value == "대분류"}
    for b, name in SCOPE_BUCKETS:
        for code, r in first.items():
            vals = [x[f"{b}_score"] for t, x in scores.items()
                    if l1[t] == code and x[f"{b}_score"] is not None]
            assert ws.cell(r, h[name]).value == pytest.approx(statistics.fmean(vals)), (b, code)


def test_v4_score_sheet_axes_stay_percentiles(daily) -> None:
    """회귀 가드 — 백분위 엔진(v4_rank) 주 모델은 지금처럼 0~100 백분위·백분위 색 10/50/90."""
    _, wb = daily
    ws = wb["Score"]
    h = header(ws)
    u = group_col(ws, "저위험", "유니버스")
    assert ws.cell(7, u + 1).value == "업종"
    assert not [k for k in h if k.endswith("(Z)")]
    vals = [ws.cell(r, u).value for r in range(8, ws.max_row + 1)]
    assert all(0 <= v <= 100 for v in vals if isinstance(v, float))
    sec = wb["Sector"]
    assert "축별 평균 백분위" in [str(c.value) for c in sec[6] if c.value is not None]


def test_group_z_falls_back_to_universe_value() -> None:
    from deliver.common import group_z
    # Y 는 표준편차가 0 이 아닌 2종목 — 표본 문턱(< 5)만으로 되돌아가는지 따로 본다
    vals = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0, "f": 0.7, "g": 1.4, "h": 9.0}
    grp = {"a": "X", "b": "X", "c": "X", "d": "X", "e": "X", "f": "Y", "g": "Y", "h": None}
    out = group_z(vals, grp, 5)
    assert out["c"] == pytest.approx(0.0) and out["e"] == pytest.approx(_sector_z(
        [1.0, 2.0, 3.0, 4.0, 5.0], 5.0))
    assert out["f"] == 0.7 and out["g"] == 1.4 and out["h"] == 9.0   # 표본 < 5 · 업종 없음
    same = group_z({k: 1.5 for k in "abcde"}, dict.fromkeys("abcde", "X"), 5)
    assert all(v == 1.5 for v in same.values())                        # 표준편차 0


# ── 4-2a 수정분 검토 후속(10-08) ─────────────────────────────────────────────────
def test_group_z_min_size_one_does_not_crash_on_a_single_member_sector() -> None:
    """min_sector_size ≤ 1 인 spec 이면 종목 하나인 대분류에서 표본 표준편차를 구할 수 없다 —
    옛 코드는 `statistics.stdev` StatisticsError 로 엑셀 생성 rc 3. 2 미만은 늘 유니버스 z."""
    from deliver.common import group_z
    vals = {"a": 1.0, "b": 2.0, "c": 3.0}
    out = group_z(vals, {"a": "X", "b": "Y", "c": "Y"}, 1)
    assert out["a"] == 1.0                                              # 혼자인 X 는 그대로
    assert out["b"] == pytest.approx(_sector_z([2.0, 3.0], 2.0))       # 2종목 Y 는 다시 z


def test_quality_vol_only_needs_a_volatility_value() -> None:
    """G-27 표기 음성 — 퀄리티 손익 지표가 다 비어도 qual_std_20d 까지 비면(퀄리티 자체가 없음)
    '퀄리티 = 변동성만' 비고를 붙이지 않는다. 손익 지표가 하나라도 있거나 z 엔진이 아니어도
    붙지 않는다."""
    from types import SimpleNamespace
    from typing import cast

    from deliver.excel_daily import QUALITY_PL_KEYS, quality_vol_only
    from deliver.view import DayView
    empty = dict.fromkeys(QUALITY_PL_KEYS)
    rows = {"vol": {**empty, "qual_std_20d": 0.02}, "none": {**empty, "qual_std_20d": None},
            "pl": {**empty, "qual_roa": 3.0, "qual_std_20d": 0.02}}

    def view(z: bool) -> DayView:
        return cast(DayView, SimpleNamespace(z_engine=z, by_ticker=rows))
    assert quality_vol_only(view(True), "vol")
    assert not quality_vol_only(view(True), "none")
    assert not quality_vol_only(view(True), "pl")
    assert not quality_vol_only(view(False), "vol")


def _net_revenue_no_estimate(table: str, rows: list[dict[str, object]]) -> None:
    """12번 = 순액 두 해(2024·2025) + 2026 결산기 cur 컨센서스 없음 → 견줄 YE 추정치가 없다."""
    _net_revenue(table, rows)
    if table == "fi_consensus":
        rows[:] = [r for r in rows if not (r["ticker"] == tick(12) and r["horizon"] == "cur"
                                           and r["target_period"] == "2026/12")]


def test_net_revenue_note_only_when_a_yoy_cell_was_blanked(tmp_path: Path) -> None:
    """순액 매출 비고는 실제로 비운 y-y 칸이 있을 때만 붙인다. 견줄 추정치(YE 컨센서스)가 없어
    y-y 칸이 원래 비는 종목에 '총액 추정치와 y-y 비교 안 함'을 달면 없는 비교를 말한다(옛 코드)."""
    model_root, fi_root = tmp_path / "model", tmp_path / "fi"
    write_model_day(model_root, FRI, write_fi_day(fi_root, FRI, edit=_net_revenue_no_estimate))
    wb = load_workbook(build_daily(FRI, "morning", model_root=model_root, fi_root=fi_root,
                                   out_root=tmp_path).path)
    ws = wb["Earnings"]
    r12 = rows_by_code(ws)[tick(12)]
    assert ws.cell(r12, col_of(ws, "2026E", 0)).value is None             # 견줄 추정치 없음
    assert ws.cell(r12, col_of(ws, "YoY (2025-2026E)", 0)).value is None   # 원래 빈 칸
    assert isinstance(ws.cell(r12, col_of(ws, "YoY (2024-2025)", 0)).value, int | float)  # 순액끼리
    sc = wb["Score"]
    srows = rows_by_code(sc)
    assert sc.cell(srows[tick(12)], header(sc)["비고"]).value is None
    assert sc.cell(srows[tick(13)], header(sc)["비고"]).value == NET_NOTE  # 실제로 비운 칸 있음


# ── 확정 양식(샘플 22 — DECISIONS §7 #6·#7) 새 계산 ────────────────────────────────
class _CandView:
    """`excel_daily.candidates` 가 읽는 DayView 부분만(순위순 → 제외 행 순서, 대분류 L1)."""

    def __init__(self, ranks: dict[str, int | None], sectors: dict[str, str], top_n: int, k: int):
        from types import SimpleNamespace

        from model.contracts import OutputRule
        self.output = OutputRule(top_n=top_n, sector_level="L1", max_per_sector=k)
        self.spec_id, self.date, self.run = "s@1", "2026-10-08", SimpleNamespace(basis="morning")
        ranked = sorted((t for t in ranks if ranks[t] is not None), key=lambda t: ranks[t] or 0)
        rest = sorted(t for t in ranks if ranks[t] is None)
        self.rows = [{"ticker": t, "rank": ranks[t], "sector_l1": sectors[t]}
                     for t in [*ranked, *rest]]
        self.by_ticker = {str(r["ticker"]): r for r in self.rows}
        self.uni = {t: {"sector_l1_name": f"{s}업종"} for t, s in sectors.items()}
        self.candidates = [str(r["ticker"]) for r in cap_candidates(
            self.rows[:len(ranked)], top_n, k, lambda r: r["sector_l1"])]

    def rank(self, t: str) -> int | None:
        return self.by_ticker[t]["rank"]           # type: ignore[return-value]


def test_candidates_and_change_follow_the_sector_cap_against_yesterday() -> None:
    """Score 후보 · 변화(make_pm 규칙 · 쉬운 말): 후보 = 순위순으로 대분류마다 최대 k 까지 top_n,
    전일 후보 = 전일 순위에 같은 규칙(전일 대분류 = 오늘 값). 변화 = 진입 · 이탈 · 이탈(k종목 초과)
    (오늘 순위가 top_n 안인데 밀림) · k종목 초과(top_n 안인데 대분류가 이미 k, 전일도 후보 아님)."""
    from deliver.excel_daily import candidate_rule_text, candidates
    sectors = {"t1": "A", "t2": "A", "t3": "A", "t7": "A", "t4": "B", "t6": "B",
               "t5": "C", "t9": "C"}
    today = {"t1": 1, "t2": 2, "t3": 3, "t7": 4, "t4": 5, "t5": 6, "t6": 7, "t9": None}
    prev = {"t9": 0, "t3": 1, "t1": 2, "t4": 3, "t5": 4, "t2": 5, "t6": 6, "t7": 7}
    c = candidates(_CandView(today, sectors, top_n=5, k=2), prev)      # type: ignore[arg-type]
    assert c.picked == {"t1", "t2", "t4", "t5", "t6"}       # A 는 t1 · t2, B 는 t4 · t6 에서 찬다
    assert dict(c.change) == {
        "t2": "진입", "t6": "진입",                 # 전일 후보(t9 · t3 · t1 · t4 · t5) 밖
        "t3": "이탈(2종목 초과)",                    # 오늘 3위(5 안)인데 A 가 이미 2종목
        "t9": "이탈",                               # 오늘 순위 없음(제외)
        "t7": "2종목 초과"}                          # 4위인데 A 가득 · 전일도 후보 아님
    assert (c.cut, c.full, c.full_text) == (7, ("A업종", "B업종"), "A업종·B업종")
    assert candidate_rule_text(c) == (
        "상위 5 · 대분류(L1)당 최대 2: 순위순으로 내려가며 2 가 찬 대분류는 건너뛰고 아래 순위로 "
        "채움 · 오늘 커트라인: 순위 7 · 2종목이 찬 대분류: A업종·B업종")
    # 전일 순위가 없으면(첫 날 · 읽기 실패) 변화는 비운다 — 후보 전부 '진입'은 틀린 말(I-2)
    for prev_none in ({}, {"x": None}):
        none = candidates(_CandView({"x": 1}, {"x": "A"}, 5, 2), prev_none)  # type: ignore[arg-type]
        assert none.change == {} and none.no_prev and none.picked == {"x"}
    assert not c.no_prev and none.full_text == "없음"


def test_previous_candidates_include_stocks_that_left_todays_population(
        caplog: pytest.LogCaptureFixture) -> None:
    """결함 3 — 전일 후보 = 전일 순위 **전체**(오늘 모집단에서 빠진 종목 포함)에 같은 규칙. 빠진
    종목(g)의 대분류는 전일 판 fi_universe 값. 옛 코드는 오늘 모집단 종목만으로 다시 뽑아 g 자리를
    전일 후보가 아니던 종목이 채웠다 — ① t5 거짓 '이탈' ② t2 '진입' 누락. 빠진 전일 후보는 Score 에
    행이 없어 `gone`(Meta 줄)으로 낸다. 전일 대분류를 모르면 경고하고 대분류 없음으로 둔다."""
    from deliver.excel_daily import candidates
    sectors = {"t1": "A", "t2": "A", "t3": "B", "t4": "B", "t5": "C", "t6": "C"}
    today = {"t1": 1, "t2": 2, "t3": 3, "t4": 4, "t5": 5, "t6": 6}
    view = _CandView(today, sectors, top_n=4, k=2)
    prev_uni = {"g": {"sector_l1": "A", "name": "빠진종목"}}
    # ① 옛 코드: 전일 후보 t1 · t3 · t6 · t5(g 자리) → t5 거짓 '이탈'
    prev1 = {"g": 1, "t1": 2, "t3": 3, "t6": 4, "t5": 5, "t2": 6, "t4": 7}
    c = candidates(view, prev1, prev_uni)                         # type: ignore[arg-type]
    assert dict(c.change) == {"t2": "진입", "t4": "진입", "t6": "이탈"}
    assert c.gone == (("g", "빠진종목"),) and c.gone_text == "빠진종목(g)"
    # ② 옛 코드: 전일 후보 t1 · t6 · t3 · t2(g 자리) → t2 '진입' 누락
    prev2 = {"g": 1, "t1": 2, "t6": 3, "t3": 4, "t2": 5, "t5": 6, "t4": 7}
    c = candidates(view, prev2, prev_uni)                         # type: ignore[arg-type]
    assert dict(c.change) == {"t2": "진입", "t4": "진입", "t6": "이탈"}
    # 전일 대분류를 모르면(전일 판 fi_universe 를 못 읽음) 대분류 없음 한 묶음 — 경고 한 줄
    caplog.clear()
    with caplog.at_level("WARNING"):
        c = candidates(view, {"g": 1, "t1": 2, "t3": 3, "t6": 4, "t5": 5},  # type: ignore[arg-type]
                       {})
    assert c.gone == (("g", ""),) and c.gone_text == "g"
    assert any("전일 대분류" in r.getMessage() for r in caplog.records)
    # 빠진 종목이 없는 날 — 전일 판 fi_universe 를 넘겨도 결과가 같다
    prev = {"t1": 1, "t3": 2, "t5": 3, "t2": 4, "t4": 5, "t6": 6}
    same = candidates(view, prev, prev_uni)                       # type: ignore[arg-type]
    assert same == candidates(view, prev)                          # type: ignore[arg-type]
    assert same.gone == () and same.gone_text == "없음"


def test_daily_previous_candidate_outside_todays_population(world: dict[str, Path],
                                                            tmp_path: Path) -> None:
    """결함 3 실판 경로 — 금요일 주 모델 모집단에서 0번(목요일 2위 · 후보)이 빠진 날. 목요일 후보
    G45 9자리는 1 · 0 · 2~8번이라 9번은 '진입'이다(옛 코드는 0번 자리를 9번으로 채워 진입을 놓쳤다).
    0번은 Score 에 행이 없어 Meta '전일 후보 중 오늘 모집단 밖' 줄에 이름(코드)으로 적는다. 대분류 ·
    이름은 목요일 판 fi_universe 에서 읽는다."""
    model = tmp_path / "model"
    shutil.copytree(world["model"], model)
    fri = find_run(model, FRI, "morning")
    assert fri is not None
    path = model / PRIMARY / f"v={fri.build_id}" / "scores.parquet"
    tbl = pq.read_table(path)
    pq.write_table(tbl.filter(pc.not_equal(tbl["ticker"], tick(0))), path)
    wb = load_workbook(build_daily(FRI, "morning", model_root=model, fi_root=world["fi"],
                                   out_root=tmp_path / "out").path)
    ws = wb["Score"]
    h, rows = header(ws), rows_by_code(ws)
    assert tick(0) not in rows
    chg = {t: ws.cell(r, h["변화"]).value for t, r in rows.items()}
    assert chg[tick(9)] == "진입"
    assert not [t for t, v in chg.items() if v is not None and str(v).startswith("이탈")]
    meta = dict(_meta_pairs(wb))
    assert meta["전일 후보 중 오늘 모집단 밖"] == f"종목0({tick(0)})"


def test_daily_score_candidate_columns_and_entry_highlight(daily) -> None:
    """Score 끝 '후보(대분류당 최대 9)' 블록 — 후보 = view 후보(30), 변화 열, 전일 순위 칸 노랑 =
    변화 '진입'(조건부 서식이라 정렬해도 따라간다), 커버리지 '신선' 은 옅은 회색."""
    _, wb = daily
    ws = wb["Score"]
    h, rows = header(ws), rows_by_code(ws)
    cand = [t for t, r in rows.items() if ws.cell(r, h["후보"]).value == "후보"]
    assert len(cand) == 30
    sec = wb["Sector"]
    hs = header(sec)
    l1_counts = [sec.cell(r, hs["후보 수 (최대 9)"]).value for r in range(8, sec.max_row + 1)
                 if sec.cell(r, 1).value == "대분류"]
    assert sum(l1_counts) == 30 and max(l1_counts) <= 9                # Sector 후보 수와 일치
    chg = {ws.cell(r, h["변화"]).value for r in rows.values()} - {None}
    assert chg <= {"진입", "이탈", "이탈(9종목 초과)", "9종목 초과"}
    prev, chg_l = ws.cell(7, h["전일 순위"]).column_letter, ws.cell(7, h["변화"]).column_letter
    cov = ws.cell(7, h["커버리지"]).column_letter
    rules = {str(rng.sqref): r for rng in ws.conditional_formatting for r in rng.rules
             if r.type == "expression"}
    assert rules[f"{prev}8:{prev}{7 + N}"].formula == [f'${chg_l}8="진입"']
    assert rules[f"{prev}8:{prev}{7 + N}"].dxf.fill.fgColor.rgb == "FFFFFF00"
    assert rules[f"{cov}8:{cov}{7 + N}"].formula == [f'${cov}8="신선"']
    notes = {k: ws.cell(7, h[k]).comment for k in ("비고", "후보", "변화")}
    assert all(notes.values())
    assert "오늘 커트라인: 순위" in notes["후보"].text.replace("\n", " ")
    meta = wb["Meta"]
    tail = [(meta.cell(r, 1).value, meta.cell(r, 2).value) for r in (meta.max_row - 1,
                                                                     meta.max_row)]
    assert tail == [("Score", "후보 · 후보"), ("Score", "후보 · 변화")]
    assert dict(_meta_pairs(wb))["전일 후보 중 오늘 모집단 밖"] == "없음"


def test_sector_week_and_quarter_returns_are_cap_weighted(scope_board) -> None:
    """Sector 1W = 종목 5세션 전 대비 수익률(엔진 v3_zscore `_price_histories` · `_period_return`
    그대로 — fi_prices [D−550일, D], 수정종가 있으면 그것)을 fi_universe 시총으로 가중, 3M = 점수 표
    r3m 시총가중(× 100). 표마다 백분위 색. 계산을 복사하지 않고 엔진 함수를 부른다."""
    import deliver.excel_daily as ed
    from model.engines import v3_zscore
    assert ed._period_return is v3_zscore._period_return
    assert ed._price_histories is v3_zscore._price_histories
    _, wb, scores, root = scope_board
    fi = root / "fi"
    prices = duckdb_rows(fi / "fi_prices" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    adj = {(str(r["ticker"]), str(r["date"])[:10]): r["adj_close"] for r in duckdb_rows(
        fi / "fi_adj_prices" / f"v={SCOPE_FI_BID}" / "part0.parquet")}
    hist: dict[str, list[tuple[str, float]]] = {}
    for r in prices:
        t, d = str(r["ticker"]), str(r["date"])[:10]
        if "2025-03-27" <= d <= "2026-09-28":                         # D−550일 ~ D
            a = adj.get((t, d))
            hist.setdefault(t, []).append((d, float(cast(float, a if a is not None
                                                          else r["close"]))))
    week = {}
    for t, h in hist.items():
        px = [p for _, p in sorted(h)]
        if len(px) > 5 and px[-6]:
            week[t] = px[-1] / px[-6] - 1
    uni = duckdb_rows(fi / "fi_universe" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    caps = {str(r["ticker"]): float(cast(float, r["market_cap"])) for r in uni
            if r["market_cap"] is not None}
    l1 = {str(r["ticker"]): r["sector_l1"] for r in uni}
    ws = wb["Sector"]
    h = header(ws)
    first = {ws.cell(r, h["코드"]).value: r for r in range(8, ws.max_row + 1)
             if ws.cell(r, h["구분"]).value == "대분류"}

    def cap_avg(vals: dict[str, float]) -> float:
        w = sum(caps[t] for t in vals if caps.get(t))
        return sum(v * caps[t] for t, v in vals.items() if caps.get(t)) / w * 100
    for code, r in first.items():
        members = [t for t in scores if l1[t] == code]
        assert ws.cell(r, h["1W 수익률 (%)"]).value == pytest.approx(
            cap_avg({t: week[t] for t in members if t in week}))
        assert ws.cell(r, h["3M 수익률 (%)"]).value == pytest.approx(
            cap_avg({t: scores[t]["r3m"] for t in members if scores[t]["r3m"] is not None}))
    # 색 — 수익률 1W · 1M · 3M 은 표마다 백분위 10/50/90, 축 평균은 대분류 ±0.5 · 업종 ±1
    y = ws.cell(7, h["1W 수익률 (%)"]).column_letter
    q = ws.cell(7, h["모멘텀"]).column_letter
    by_ref = {str(rng.sqref): r for rng in ws.conditional_formatting for r in rng.rules}
    top = max(first.values())
    assert [v.type for v in by_ref[f"{y}8:{y}{top}"].colorScale.cfvo] == ["percentile"] * 3
    assert [float(v.val) for v in by_ref[f"{q}8:{q}{top}"].colorScale.cfvo] == [-0.5, 0.0, 0.5]


def test_scale_zero_anchor_uses_each_sign_90th_magnitude_two_digits() -> None:
    """Earnings YoY 색 경계 — 가운데 0, 음수 끝 −lo · 양수 끝 +hi(각 부호 |값| 90% 분위 = 정렬한
    값의 int(0.9·(n−1)) 번째, 유효숫자 2자리). 글자 칸(흑자전환 등) · 빈칸은 뺀다. 날마다 그날
    값으로 다시 잰다."""
    from deliver.qpack import scale_zero_anchor

    def bounds(values: list[object]) -> list[float]:
        cs = scale_zero_anchor(values).colorScale
        assert cs is not None
        return [float(v.val) for v in cs.cfvo]
    pos = [float(x) for x in range(1, 101)]                   # 90% 분위 = 정렬 89번째 = 90
    neg = [-x * 1.234 for x in range(1, 11)]                  # |값| 90% = 정렬 8번째 = 11.106
    assert bounds([*pos, *neg, "흑자전환", None, 0.0]) == [-11.0, 0.0, 90.0]
    assert bounds([3.0, 47.0, 123.0]) == [-47.0, 0.0, 47.0]  # 음수 없으면 양수 값으로
    assert bounds([-0.0123, -5.0]) == [-0.012, 0.0, 0.012]   # 양수 없으면 음수 값으로
    assert bounds(["적자지속", None]) == [-1.0, 0.0, 1.0]
    cs = scale_zero_anchor([1.0, -1.0]).colorScale
    assert cs is not None and [c.rgb[-6:] for c in cs.color] == ["F8696B", "FFEB84", "63BE7B"]


def test_daily_earnings_yoy_colour_is_zero_anchored(daily) -> None:
    _, wb = daily
    ws = wb["Earnings"]
    yoy_cols = [c.column_letter for c in ws[7] if str(c.value or "").startswith("YoY")]
    assert len(yoy_cols) == 10
    scales = {str(rng.sqref).split(":")[0].rstrip("0123456789"): r.colorScale
              for rng in ws.conditional_formatting for r in rng.rules}
    assert set(scales) == set(yoy_cols)                      # 값 열 · 분기 값 열은 무색
    for letter in yoy_cols:
        cfvo = scales[letter].cfvo
        assert [v.type for v in cfvo] == ["num"] * 3 and float(cfvo[1].val) == 0.0
        assert float(cfvo[0].val) < 0 < float(cfvo[2].val)


def test_plain_wording_has_no_board_or_cap_words(daily, scope_board) -> None:
    """쉬운 말(10-11 사용자): 읽는 사람이 '판'을 모른다 → 데이터 · 날짜 · 신규 편입, '상한' →
    '대분류당 최대 9종목'(엔진 z 의 '±3 상한'은 그대로). 시트 칸 · 메모 어디에도 남지 않는다
    (숨김 trend 시트 제외). 4행 기준일 말은 basis 를 따른다."""
    left = re.compile(r"판(?!단|매|정|별|결|례|독)")
    for wb in (daily[1], scope_board[1]):
        for ws in wb.worksheets:
            if ws.title == "trend":
                continue
            for row in ws.iter_rows():
                for c in row:
                    for t in (c.value, c.comment.text if c.comment else None):
                        if isinstance(t, str):
                            assert not left.search(t), (ws.title, c.coordinate, t[:80])
                            assert "상한" not in t.replace("±3 상한", ""), (ws.title, c.coordinate)
        assert "아침 확정 데이터" in str(wb["Score"]["A4"].value)


def test_basis_words_in_the_band_follow_basis() -> None:
    from deliver.excel_daily import BASIS_WORDS
    assert BASIS_WORDS == {"morning": "아침 확정 데이터", "evening": "장 마감 데이터"}


def test_note_box_wraps_at_300px_and_keeps_symbols_glued(tmp_path: Path) -> None:
    """머리 칸 메모 — 줄은 300px(한글 14 · 그 밖 8)에서 끊고 기호(= ≤ − …)는 앞뒤 낱말과, '·' 는
    앞 낱말과 붙여(NBSP) 줄 머리 · 끝에 홀로 두지 않는다. 상자 = 가장 긴 줄 + 30 · 줄 수 × 21 + 30
    (엑셀 메모 상자는 글에 맞춰 자라지 않는다). vml 에 그 크기가 그대로 들어간다."""
    from deliver.qpack import NBSP, note, note_lines, note_px
    assert note_px("가a·") == 14 + 8 + 8
    text = ("순위 ≤ 30 인데 대분류 9 초과 · 전일 후보 = 전일 순위에 같은 규칙 적용"
            "(전일 대분류 = 오늘 값)")
    lines = note_lines(text)
    assert all(note_px(ln) <= 300 for ln in lines) and len(lines) >= 2
    for ln in lines:
        first, last = ln.split(" ")[0], ln.split(" ")[-1]
        assert first not in {"=", "≤", "·"} and last not in {"=", "≤"}, lines
    assert f"순위{NBSP}≤{NBSP}30" in lines[0] and f"초과{NBSP}·" in "".join(lines)
    plain = note_lines("1 = 시총의 1%. " * 12, glue=False)      # Raw 정의 메모는 붙이지 않는다
    assert NBSP not in "".join(plain) and all(note_px(ln) <= 300 for ln in plain)
    cm = note("한줄\n\n■ 둘째 줄", wrap=False)
    assert cm.text == "한줄\n\n■ 둘째 줄"
    assert (cm.width, cm.height) == (note_px("■ 둘째 줄") + 30, 21 * 3 + 30)
    from openpyxl import Workbook
    wb = Workbook()
    sheet = wb.active
    assert sheet is not None
    sheet["A1"].comment = cm
    wb.save(tmp_path / "n.xlsx")
    with zipfile.ZipFile(tmp_path / "n.xlsx") as z:
        vml = next(z.read(n).decode() for n in z.namelist() if n.endswith(".vml"))
    assert f"width:{cm.width}px;height:{cm.height}px" in vml


def test_daily_raw_definitions_are_header_notes(daily) -> None:
    """Raw 5행 열 정의는 7행 머리 칸 메모로(빨간 삼각형) — 5행은 A5 · 블록 설명만, 메모 상자는 글
    크기에 맞춘다(144×79 기본 상자 아님)."""
    res, wb = daily
    ws = wb["Raw"]
    assert [c for c in ws[7] if c.comment is not None]
    assert [c.value for c in ws[5] if c.value] == [
        "결측: 결측(사유)", "열 뜻: 머리 칸 메모(빨간 삼각형) · 색 없음"]
    with zipfile.ZipFile(res.path) as z:
        vml = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".vml"))
    assert "width:144px;height:79px" not in vml


# ── 코드 리뷰 후속(I-1 · I-2) ─────────────────────────────────────────────────────────
BIG = "100003"            # G10 대형주 — 최근 3세션에 미해결 분할(수정종가 반토막 · 점프 계단)


def _unresolved_split(b) -> None:
    """BIG 의 시총을 크게, 최근 3세션 종가 · 수정종가를 반으로(분할이 수정되지 않음), 그 3행의
    `adj_jump_ok` 를 False(앞은 True)로 — 1W 창(최근 6행) 안에서 계단이 바뀐다."""
    for r in b.tables["fi_universe"]:
        if r["ticker"] == BIG:
            r["market_cap"] = 1_000_000.0
    for table in ("fi_prices", "fi_adj_prices"):
        mine = sorted((r for r in b.tables[table] if r["ticker"] == BIG), key=lambda r: r["date"])
        for i, r in enumerate(mine):
            late = i >= len(mine) - 3
            if table == "fi_prices":
                r["close"] = round(float(r["close"]) * (0.5 if late else 1.0))
            else:
                r["adj_close"] = float(r["adj_close"]) * (0.5 if late else 1.0)
                r["adj_jump_ok"] = not late


def test_sector_week_leaves_out_an_unresolved_split_like_engine_r1m(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """I-1 — 주 모델(scope)이 `adj_jump_missing` 을 켰으면 Sector 1W 도 엔진 r1m 처럼 창(최근 6행)
    안에서 `adj_jump_ok` 계단이 바뀌는 종목을 뺀다. 미해결 분할이 낀 대형주(−50%)가 업종 1W 를 끌지
    않는다(옛 코드는 `_price_histories` 의 계단 표식을 버렸다)."""
    with caplog.at_level("INFO", logger="deliver.excel_daily"):
        _, wb, scores, root = _scope_board(tmp_path, edit=_unresolved_split)
    assert scores[BIG]["r1m"] is None                       # 엔진도 r1m 을 비웠다(같은 규칙)
    fi = root / "fi"
    prices = duckdb_rows(fi / "fi_prices" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    adj = {(str(r["ticker"]), str(r["date"])[:10]): r["adj_close"] for r in duckdb_rows(
        fi / "fi_adj_prices" / f"v={SCOPE_FI_BID}" / "part0.parquet")}
    hist: dict[str, list[tuple[str, float]]] = {}
    for r in prices:
        t, d = str(r["ticker"]), str(r["date"])[:10]
        a = adj.get((t, d))
        hist.setdefault(t, []).append((d, float(cast(float, a if a is not None else r["close"]))))
    week = {t: px[-1] / px[-6] - 1 for t, h in hist.items()
            if len(px := [p for _, p in sorted(h)]) > 5 and px[-6]}
    assert week[BIG] < -0.45                                 # 분할 반토막이 그대로 낀 수익률
    uni = duckdb_rows(fi / "fi_universe" / f"v={SCOPE_FI_BID}" / "part0.parquet")
    caps = {str(r["ticker"]): float(cast(float, r["market_cap"])) for r in uni}
    g10 = [str(r["ticker"]) for r in uni if r["sector_l1"] == "G10" and str(r["ticker"]) in scores]

    def cap_avg(members: list[str]) -> float:
        w = sum(caps[t] for t in members)
        return sum(week[t] * caps[t] for t in members) / w * 100
    ws = wb["Sector"]
    h = header(ws)
    r = next(r for r in range(8, ws.max_row + 1)
             if ws.cell(r, h["구분"]).value == "대분류" and ws.cell(r, h["코드"]).value == "G10")
    got = float(cast(float, ws.cell(r, h["1W 수익률 (%)"]).value))
    assert got == pytest.approx(cap_avg([t for t in g10 if t != BIG]))
    assert cap_avg(g10) < -40 < got                           # 넣었으면 업종 1W 가 −40% 아래
    assert any(BIG in rec.getMessage() and "Sector 1W" in rec.getMessage()
               for rec in caplog.records)
    meta = wb["Meta"]
    week_def = next(meta.cell(r, 3).value for r in range(8, meta.max_row + 1)
                    if meta.cell(r, 1).value == "Sector"
                    and str(meta.cell(r, 2).value).endswith("1W 수익률 (%)"))
    assert "미해결 수정주가 점프가 낀 종목은 뺀다" in str(week_def)


def test_sector_week_warns_when_adjusted_prices_are_missing(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """수정종가 표(fi_adj_prices)를 읽지 못하면 종가로 계산하고 경고 한 줄을 남긴다(분할이 낀 종목
    수익률이 틀릴 수 있다)."""
    from types import SimpleNamespace

    from deliver.excel_daily import week_returns
    from deliver.view import DayView
    bid, day = "m_x", "2026-09-28"
    write_fi(tmp_path, "fi_prices", bid, [
        {"ticker": "A", "date": f"2026-09-{d:02d}", "close": 100 + d, "price_source": "krx"}
        for d in range(20, 29)])
    view = cast(DayView, SimpleNamespace(
        date=day, rows=[{"ticker": "A"}], spec=None,
        run=SimpleNamespace(fi_build_id=bid, basis="morning")))
    with caplog.at_level("WARNING"):
        got = week_returns(tmp_path, view)
    assert got == {"A": pytest.approx(128 / 123 - 1)}
    assert any("fi_adj_prices" in rec.getMessage() for rec in caplog.records)


def test_no_previous_day_leaves_change_blank(world: dict[str, Path], tmp_path: Path,
                                            caplog: pytest.LogCaptureFixture) -> None:
    """I-2 — 전일 순위가 없으면(첫 날 · 같은 basis 직전 데이터 없음 · 읽기 실패) Score 변화 열을
    모두 비운다(후보 30 전부 '진입'은 틀린 말). 변화 머리 메모 · Meta '전일 비교 데이터'에 '전일
    데이터 없음', 경고 로그 한 줄."""
    def change_and_meta(path: Path) -> tuple[list[object], int, dict[object, object], str]:
        wb = load_workbook(path)
        ws = wb["Score"]
        h, rows = header(ws), rows_by_code(ws)
        chg: list[object] = [ws.cell(r, h["변화"]).value for r in rows.values()]
        n_cand = sum(1 for r in rows.values() if ws.cell(r, h["후보"]).value == "후보")
        memo = ws.cell(7, h["변화"]).comment
        assert memo is not None
        return chg, n_cand, dict(_meta_pairs(wb)), memo.text

    first = W38[0]                                        # world 의 첫 날 — 그 전 데이터가 없다
    with caplog.at_level("WARNING"):
        res = build_daily(first, "morning", model_root=world["model"], fi_root=world["fi"],
                          out_root=tmp_path / "a")
    chg, n_cand, meta, memo = change_and_meta(res.path)
    assert set(chg) == {None} and n_cand == 30
    assert meta["전일 비교 데이터"] == "전일 데이터 없음"
    assert "전일 데이터 없음: 오늘은 변화 열을 비움" in memo.replace("\n", " ").replace("\xa0", " ")
    assert any("전일 데이터 없음" in rec.getMessage() for rec in caplog.records)
    # 직전 데이터는 있는데 순위를 읽지 못하는 날 — 같은 처리 + 읽기 실패 경고
    model = tmp_path / "model"
    shutil.copytree(world["model"], model)
    prev = find_run(model, THU, "morning")
    assert prev is not None
    (model / PRIMARY / f"v={prev.build_id}" / "scores.parquet").unlink()
    caplog.clear()
    with caplog.at_level("WARNING"):
        res = build_daily(FRI, "morning", model_root=model, fi_root=world["fi"],
                          out_root=tmp_path / "b")
    chg, n_cand, meta, _memo = change_and_meta(res.path)
    assert set(chg) == {None} and n_cand == 30
    assert meta["전일 비교 데이터"] == f"{THU} {prev.build_id} — 순위를 쓸 수 없어 전일 데이터 없음"
    assert any("전일 순위를 읽지 못했다" in rec.getMessage() for rec in caplog.records)
    # 보통 날(전일 순위 있음)은 변화를 매긴다 — 회귀 가드
    chg, _n, meta, memo = change_and_meta(build_daily(
        FRI, "morning", model_root=world["model"], fi_root=world["fi"],
        out_root=tmp_path / "c").path)
    assert set(chg) - {None} and str(meta["전일 비교 데이터"]).startswith(THU)
    assert "전일 데이터 없음" not in memo
