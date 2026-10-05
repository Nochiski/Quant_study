"""전달층 엑셀(`src/deliver/`, 플랜 M2 W2 T2.5b · MODEL_EXCEL_SPEC) — 합성 판으로 검증.

합성 세계: ISO 2026-W39(월 09-21 ~ 금 09-25, 수 09-23 휴장 = 판 없음) + 지난주 목·금(09-17·18).
종목 45(대분류 G45 15 · G10 10 · G20 10 · G30 5 · G40 5). 금요일 순위는 G45 가 1~15위라
업종 상한 9 가 걸린다. model 판·factor_inputs 판은 고정 계약 경로에 parquet 로 쓴다.
"""
from __future__ import annotations

import json
import statistics
import zipfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deliver import __main__ as cli
from deliver.common import cap_candidates, change_pct, quantile, winsorize, yoy
from deliver.excel_daily import build_daily, model_label
from deliver.excel_weekly import build_weekly, prev_week, week_days
from deliver.reader import DeliverError, find_run
from deliver.view import load_day
from model import registry
from model.contracts import FI_TABLES, score_columns
from openpyxl import load_workbook
from openpyxl.formatting.rule import ColorScale

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


def write_fi_day(fi_root: Path, day: str) -> str:
    bid = f"m_{day.replace('-', '')}T010000Z"
    uni, prices, adj, flows, fins, cons = [], [], [], [], [], []
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
                        ("fi_credit", []), ("fi_consensus_annual", [])):
        write_fi(fi_root, table, bid, rows)
    return bid


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


def header(ws) -> dict[str, int]:
    """7행 열 이름 → 열 번호(줄바꿈은 공백으로)."""
    return {str(c.value).replace("\n", " "): c.column for c in ws[7] if c.value is not None}


def rows_by_code(ws) -> dict[str, int]:
    return {str(ws.cell(r, 1).value): r for r in range(8, ws.max_row + 1)}


def col_of(ws, label: str, occurrence: int = 0) -> int:
    hits = [c.column for c in ws[7] if str(c.value).replace("\n", " ") == label]
    return hits[occurrence]


# ── 순수 함수 ──────────────────────────────────────────────────────────────────
def test_yoy_sign_flags_replace_growth() -> None:
    assert yoy(20.0, -10.0) == (None, "흑전")
    assert yoy(-5.0, 30.0) == (None, "적전")
    assert yoy(-20.0, -10.0) == (None, "적지")
    assert yoy(110.0, 100.0) == (pytest.approx(10.0), None)
    assert yoy(None, 1.0) == (None, None)


def test_change_pct_follows_v3_revision_convention() -> None:
    assert change_pct(10.0, -5.0) == (None, "흑전")          # 이전값<0 → 표식만
    assert change_pct(-20.0, -10.0) == (None, "적확")
    assert change_pct(-5.0, -10.0) == (None, "적축")
    v, f = change_pct(-5.0, 10.0)                            # 적전 = 변화율 + 표식
    assert f == "적전" and v == pytest.approx(-150.0)
    assert change_pct(12.0, 10.0) == (pytest.approx(20.0), None)


def test_winsorize_clips_to_1_99_and_keeps_text() -> None:
    vals: list[object] = [float(x) for x in range(100)] + [10_000.0, "결측(x)", None]
    out = winsorize(vals)
    nums = sorted(float(v) for v in vals if isinstance(v, float))
    hi = quantile(nums, 0.99)
    assert max(v for v in out if isinstance(v, float)) == pytest.approx(hi)
    assert min(v for v in out if isinstance(v, float)) == pytest.approx(quantile(nums, 0.01))
    assert out[-2:] == ["결측(x)", None]


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
    assert wb.sheetnames == [
        "점수", "점수 원자료", "지표(표시용)", "실적", "업종", "모델 비교", "메타"]
    for name in ("점수", "점수 원자료", "지표(표시용)", "실적", "모델 비교"):
        assert wb[name].max_row == 7 + N, name              # 주 모델 모집단 45행
    assert wb["업종"].max_row == 7 + 10                       # 대분류 5 + 중분류 5
    assert res.n_rows == N and res.n_ranked == 41
    assert [t for _, t, _ in res.top] == [tick(0), tick(1), tick(2), tick(3), tick(4)]


def test_daily_scores_sheet_contents(daily) -> None:
    _, wb = daily
    ws = wb["점수"]
    h = header(ws)
    for label in ("코드", "이름", "시장", "대분류", "중분류", "시총(억)", "거래대금 20일(억)",
                  "주가",
                  "순위", "종합 점수", "전일 순위", "Δ순위", "제외 사유", "커버리지",
                  "저위험 유니버스", "저위험 업종", "리비전 유니버스", "결측 축", "최대 차이"):
        assert label in h, label
    rows = rows_by_code(ws)
    assert ws.cell(8, 1).value == tick(0) and ws.cell(8, h["순위"]).value == 1
    r0 = rows[tick(0)]
    assert ws.cell(r0, h["전일 순위"]).value == 2 and ws.cell(r0, h["Δ순위"]).value == 1
    assert ws.cell(rows[tick(40)], h["제외 사유"]).value == "관리종목(admin)"
    assert ws.cell(rows[tick(42)], h["제외 사유"]).value == "고점근접+반전 게이트(pull_gate)"
    assert ws.cell(rows[tick(40)], h["순위"]).value is None
    assert ws.cell(rows[tick(5)], h["커버리지"]).value == "유예 D+2"
    r3 = rows[tick(3)]
    assert ws.cell(r3, h["리비전 유니버스"]).value == "결측(커버리지<3)"
    assert ws.cell(r3, h["결측 축"]).value == "리비전"
    assert isinstance(ws.cell(r3, h["저위험 유니버스"]).value, float)
    # 제외 순서: 순위 종목 뒤에 코드순
    tail = [ws.cell(r, 1).value for r in range(8 + 41, 8 + N)]
    assert tail == sorted(tail) == [tick(i) for i in (40, 41, 42, 43)]
    # 다른 모델 순위 — v3 는 역순 유니버스
    v3 = col_of(ws, "v3 원본")                       # 헤더는 모델 이름만(spec id 는 정의·메타)
    assert ws.cell(r0, v3).value == 40


def test_daily_scores_sheet_note_flags_thin_coverage(daily) -> None:
    _, wb = daily
    ws = wb["점수"]
    h = header(ws)
    assert h["비고"] == h["커버리지"] + 1
    rows = rows_by_code(ws)
    note = {i: ws.cell(rows[tick(i)], h["비고"]).value for i in range(5)}
    assert note == {0: None, 1: "최근 3개월 의견 없음", 2: "애널리스트 3명 이하",
                    3: "애널리스트 3명 이하", 4: "애널리스트 수 미상"}


def test_daily_raw_sheet_flags_and_definitions(daily) -> None:
    _, wb = daily
    ws = wb["점수 원자료"]
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
    assert vol_def in str(ws.cell(5, h["60일 변동성(%)"]).value)
    assert ws.cell(rows[tick(0)], h["연간 기준기"]).value == "2025/12"
    assert ws.cell(rows[tick(0)], h["재무 접수일"]).value == "2026-03-15"
    assert ws.cell(rows[tick(10)], h["수급 최신일"]).value is None
    assert ws.cell(rows[tick(0)], h["수급 최신일"]).value == FRI
    # 표시 지표는 이 시트에 없다(중복 표기 금지)
    assert "1M 수익률(%)" not in h


def test_daily_display_sheet_is_winsorized(daily) -> None:
    _, wb = daily
    ws = wb["지표(표시용)"]
    h = header(ws)
    rows = rows_by_code(ws)
    c = h["1M 수익률(%)"]
    vals = [ws.cell(r, c).value for r in range(8, ws.max_row + 1)]
    nums = [v for v in vals if isinstance(v, float)]
    raw = sorted(_raw(i, "R1M")[0] * 100 for i in range(N) if i not in (40, 41))
    assert max(nums) == pytest.approx(quantile(raw, 0.99))
    assert max(nums) < 5000.0
    assert min(nums) == pytest.approx(quantile(raw, 0.01))
    assert ws.cell(rows[tick(2)], h["선행 PER(배)"]).value == "적자"
    assert ws.cell(rows[tick(4)], h["EPS 1M 표식"]).value == "흑전"
    assert ws.cell(rows[tick(4)], h["선행 EPS 1M 변화(%)"]).value is None
    assert "VOL60 백분위" not in h and "60일 변동성(%)" not in h


def test_daily_earnings_sign_flags(daily) -> None:
    _, wb = daily
    ws = wb["실적"]
    rows = rows_by_code(ws)
    fy_1 = col_of(ws, "FY-1", 1)          # 0 = 매출, 1 = 영업이익, 2 = 순이익
    op_y, op_f = fy_1 + 1, fy_1 + 2
    assert ws.cell(rows[tick(1)], op_y).value is None
    assert ws.cell(rows[tick(1)], op_f).value == "흑전"
    assert ws.cell(rows[tick(3)], op_f).value == "적지"
    ni_f = col_of(ws, "FY-1", 2) + 2
    assert ws.cell(rows[tick(2)], ni_f).value == "적전"
    rev = col_of(ws, "FY-1", 0)
    r10 = rows[tick(10)]
    assert ws.cell(r10, rev).value == 1200.0
    assert ws.cell(r10, rev + 1).value == pytest.approx((1200 / 1100 - 1) * 100)
    e0 = col_of(ws, "FY0 E", 0)
    assert ws.cell(r10, e0).value == 1300.0                              # 2026/12 컨센서스
    assert ws.cell(r10, e0 + 1).value == pytest.approx((1300 / 1200 - 1) * 100)
    h = header(ws)
    assert ws.cell(r10, h["FY-1 (확정)"]).value == "2025/12"
    assert ws.cell(r10, h["최근 분기"]).value == "2026/06"
    assert ws.cell(rows[tick(1)], h["Q0 표식"]).value == "흑전"
    assert ws.cell(r10, h["Q0 y-y(%)"]).value == pytest.approx((14 / 10 - 1) * 100)


def test_daily_sector_and_model_sheets(daily) -> None:
    _, wb = daily
    ws = wb["업종"]
    h = header(ws)
    first = {ws.cell(r, h["코드"]).value: r for r in range(8, ws.max_row + 1)}
    r45 = first["G45"]
    assert ws.cell(r45, h["구분"]).value == "대분류"
    assert ws.cell(r45, h["종목 수"]).value == 15
    assert ws.cell(r45, h["후보 수"]).value == 9
    assert ws.cell(r45, h["상위 30 비중"]).value == pytest.approx(15 / 30 * 100)
    assert str(ws.cell(r45, h["상위 3(순위)"]).value).startswith("종목0(1)")
    assert ws.cell(7, 1).fill.fgColor.rgb.endswith("0070C0")            # Style 시트 헤더
    assert ws.cell(r45, h["업종명"]).font.color.rgb.endswith("7030A0")   # 항목명 보라
    m = wb["모델 비교"]
    hm = header(m)
    for sid in SPECS:
        assert model_label(sid) in hm, sid     # 헤더는 모델 이름(spec id 는 정의·메타)
    assert m.cell(8, col_of(m, "v4 기본")).value == 1
    assert m.cell(8, col_of(m, "v4 동일가중")).value == 3


def test_daily_meta_sheet(daily) -> None:
    _, wb = daily
    ws = wb["메타"]
    pairs = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(8, ws.max_row + 1)}
    assert pairs["기준일"] == FRI and pairs["basis"] == "morning"
    assert pairs["model 판 id"] == "m_20260925T000000Z"
    assert pairs["factor_inputs 판 id"] == "m_20260925T010000Z"
    assert pairs["전일 비교 판"].startswith(THU)
    assert "MG0 pass" in pairs["판 게이트 v4_rank@0.1"]
    assert "저위험 25.0%" in pairs["가중치"]
    assert "20세션 거래대금 ≥ 10억" in pairs["유니버스 규칙"]
    assert all(f"각주 {k}" in pairs for k in (1, 2, 3, 4))
    sheets_in_dict = {ws.cell(r, 1).value for r in range(8, ws.max_row + 1)}
    assert {"점수", "점수 원자료", "지표(표시용)", "실적", "업종", "모델 비교"} <= sheets_in_dict


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
    ws = wb["점수"]
    r7 = rows_by_code(ws)[tick(7)]
    assert ws.cell(r7, 2).value == FORMULA_NAME                         # 문자열로 남는다


def test_daily_qpack_styles(daily) -> None:
    _, wb = daily
    ws = wb["점수"]
    h = header(ws)
    assert ws.sheet_view.showGridLines is False
    assert ws.freeze_panes == "I8"                                       # 식별 8열 뒤
    assert ws.auto_filter.ref == f"A7:{ws.cell(7, ws.max_column).column_letter}{7 + N}"
    a8 = ws.cell(8, 1)
    assert a8.font.name == "맑은 고딕" and a8.font.sz == 8
    assert ws.cell(1, 1).fill.fgColor.rgb.endswith("BDD7EE")
    assert ws.cell(1, 1).value == "sort" and ws.cell(1, h["순위"]).value == "▲"
    for r in (2, 3, 4):
        assert ws.cell(r, 1).fill.fgColor.rgb.endswith("4F81BD")
    assert ws.cell(2, 1).value == "점수" and ws.cell(2, 1).font.b
    assert ws.cell(7, 1).fill.fgColor.rgb.endswith("7F7F7F")
    assert ws.cell(7, h["순위"]).fill.fgColor.rgb.endswith("7030A0")      # 핵심 열 보라
    assert ws.cell(6, h["종합 점수"]).fill.fgColor.rgb.endswith("FFC000")  # 종합 그룹 금색
    assert ws.cell(6, h["저위험 유니버스"]).fill.fgColor.rgb.endswith("4F81BD")
    assert ws.row_dimensions[7].height == 33.75
    assert ws.cell(7, 1).border.bottom.style == "thin"
    assert ws.cell(8, h["시총(억)"]).number_format == "#,##0"
    sep = ws.column_dimensions[ws.cell(7, h["주가"] + 1).column_letter]
    assert sep.hidden and sep.outlineLevel == 1 and sep.width == 5
    # Q.Pack 실측(보이는 숫자 열 4.4~6.4) — 고정 13 이 아니라 값·헤더에 맞춘 최소 폭
    comp_w = ws.column_dimensions[ws.cell(7, h["종합 점수"]).column_letter].width
    assert 4.4 <= comp_w <= 6.5, comp_w
    shown = [ws.column_dimensions[ws.cell(7, c).column_letter] for c in range(1, ws.max_column + 1)]
    # 문자 열 상한 16(제외 사유·비고 — '애널리스트 3명 이하'가 15.5) · 그 밖은 14 이하
    assert max(d.width for d in shown if not d.hidden) <= 16, [d.width for d in shown]
    wide = [ws.cell(7, c).value for c in range(1, ws.max_column + 1)
            if (d := ws.column_dimensions[ws.cell(7, c).column_letter]).width > 14 and not d.hidden]
    assert set(wide) <= {"제외 사유", "비고"}, wide
    assert wb["점수 원자료"].row_dimensions[5].height == 11.25        # 정의 행도 한 줄
    # 행 높이는 행마다 박는다 — 시트 기본값만 두면 기본 글꼴(Calibri 11)로 다시 잡혀 Q.Pack 보다
    # 1.4배 높게 그려진다(10-05 렌더: 한 쪽 29행 vs Q.Pack 37행)
    assert all(ws.row_dimensions[r].height == 11.25 for r in range(1, ws.max_row + 1) if r != 7)
    for s in wb.worksheets:
        assert all(s.row_dimensions[r].height for r in range(1, s.max_row + 1)), s.title
    rules = [(str(rng.sqref), rule) for rng in ws.conditional_formatting for rule in rng.rules]
    scales = [(ref, r.colorScale) for ref, r in rules if isinstance(r.colorScale, ColorScale)]
    colored = {ref.split(":")[0].rstrip("0123456789") for ref, _ in scales}
    letter = ws.cell(7, h["저위험 유니버스"]).column_letter
    assert letter in colored and ws.cell(7, h["Δ순위"]).column_letter in colored
    assert ws.cell(7, h["순위"]).column_letter not in colored            # 점수 시트 순위 무색
    cs = next(c for ref, c in scales if ref.startswith(letter))
    assert [v.type for v in cs.cfvo] == ["percentile"] * 3
    assert [v.val for v in cs.cfvo] == [10, 50, 90]
    assert [c.rgb[-6:] for c in cs.color] == ["63BE7B", "FFEB84", "F8696B"]


def test_daily_rank_scale_is_reversed_on_model_sheet(daily) -> None:
    _, wb = daily
    ws = wb["모델 비교"]
    c = ws.cell(7, col_of(ws, "v4 기본")).column_letter
    cs = next(r.colorScale for rng in ws.conditional_formatting for r in rng.rules
              if str(rng.sqref).startswith(c))
    assert [x.rgb[-6:] for x in cs.color] == ["F8696B", "FFEB84", "63BE7B"]


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
    rc = cli.main(["model-daily", "--date", "20260925", "--basis", "morning", "--dry-run",
                   "--env-file", str(tmp_path / "none.env"), *_roots(world, tmp_path)])
    assert rc == 1                                                       # 비밀 키 없음


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
    assert wb["점수"].max_row == 7 + n_scores
    pairs = {wb["메타"].cell(r, 1).value: wb["메타"].cell(r, 2).value
             for r in range(8, wb["메타"].max_row + 1)}
    assert pairs["model 판 id"] == res.build_id and "MG1 pass" in pairs[f"판 게이트 {primary}"]
    # scope(v3 엔진) 점수 행엔 업종 열이 없다 — fi_universe 업종으로 채워 업종 시트가 3 대분류로 선다
    sector_rows = [wb["업종"].cell(r, 1).value for r in range(8, wb["업종"].max_row + 1)]
    assert len(sector_rows) >= 3, sector_rows
    # 신선도도 점수 행이 아니라 fi_universe 에서 — 빈칸이면 커버리지 열이 통째로 빈다
    ws = wb["점수"]
    cov = [ws.cell(r, header(ws)["커버리지"]).value for r in range(8, ws.max_row + 1)]
    assert cov and all(cov), cov[:5]
    # 종합 점수 열 이름은 엔진마다 다르다(v3 composite_score · v2 total_score) — 비면 안 된다
    comp = [ws.cell(r, header(ws)["종합 점수"]).value for r in range(8, ws.max_row + 1)]
    assert all(isinstance(c, float) for c in comp), comp[:5]
    sec = wb["업종"]
    med = [sec.cell(r, header(sec)["종합 점수 중앙값"]).value for r in range(8, sec.max_row + 1)]
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
