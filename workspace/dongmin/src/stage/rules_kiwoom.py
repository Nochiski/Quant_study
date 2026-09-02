"""키움 stage 테이블 선언 (STAGE_DESIGN v2.2 §4 키움). 코드가 아니라 목록이다.

(p,s)·결측·부호는 `survey_out/v2/kiwoom.*.json` 전수 실측이 근거다.
**단위 접미사는 실측된 컬럼에만 붙인다** (§5 "단위를 모르면 접미사 금지", §4 KIS `*_amt` 처방):
  · `_krw` — SPEC §2-4 단위표(ka10060 투자자 13컬럼 백만원 · ka10014 `shrts_trde_prica` 천원 ·
    ka20068 `remn_amt` 백만원)와 KRX 종가 100% 대조 컬럼(`cur_prc`·`close_pric`) 및 그 항등식
    상대(`pred_pre` — SPEC §2-3 전일종가−pred_pre=종가 실증)
  · `_shr` — 원장 컬럼명이 수량을 명시한 것(`*_qty`·`*_stkcnt`)과 KRX `LIST_SHRS` 와 97.45%
    대조된 `listCount`
  · 나머지 수치(`shrts_avg_pric`·`lastPrice`·ka20068 대차 4컬럼)는 단위 미측정 → 원문 이름 유지
키움 티커는 `9999A9` 패턴이 실재한다(ka10014 2,777 · ka10060 7,831행) — TEXT expected_len=6,
숫자 캐스팅·zfill 금지.
"""
from __future__ import annotations

from .model import (
    AVAILABLE_NONE,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    ExtraColumn,
    Invariant,
    SourceRef,
    TableRule,
    p_headroom,
)

# ka10099 는 snap_date 축 누적 스냅샷이라 커버리지 시작일을 함께 실어야 한다(§3 temporality ⓑ).
# TableRule 에 필드가 없어 모듈 상수로 둔다 — 빌더 필요 기능(보고서 참조).
MASTER_COVERAGE_FROM = "2026-09-01"

_TICKER = ColumnRule("ticker", "ticker", KIND_TEXT, expected_len=6, key=True)
_DT = ColumnRule("dt", "date", KIND_DATE_YMD8, key=True)
# 수집 산물 컬럼 — 값은 상수(API 코드)지만 원장 데이터 컬럼이라 전수 적재 원칙대로 싣는다
_SRC_API = ColumnRule("src_api", "src_api", KIND_TEXT)


def _flow_krw(src: str) -> ColumnRule:
    """ka10060 투자자 수급 13컬럼 — 백만원(SPEC §2-4) ×1e6 → `_krw`.

    부호 keep: 순매수는 부호가 값이다(SPEC §2-3 abs 금지 목록). survey 최대 7자리 ×1e6 = 13자리.
    """
    return ColumnRule(src, f"{src}_krw", KIND_NUMERIC, *p_headroom(13), unit_scale=1_000_000)


# ── stg_flow_daily_kiwoom (ka10060) ───────────────────────────────────────────
STG_FLOW_DAILY_KIWOOM = TableRule(
    name="stg_flow_daily_kiwoom",
    sources=(SourceRef("kiwoom", "ka10060_investor_flows", "ka10060"),),
    columns=(
        _DT, _TICKER,
        # 부호는 전일 대비 방향 표시자 — abs 필수(KRX 종가 대조 abs 100% / raw 51.6%, SPEC §2-3)
        ColumnRule("cur_prc", "close_krw", KIND_NUMERIC, *p_headroom(7), sign="abs"),
        ColumnRule("pred_pre", "pred_pre_krw", KIND_NUMERIC, *p_headroom(6)),
        # 명세 오표기: 이름은 누적거래대금이지만 실측은 거래량(주) — KRX ACC_TRDVOL 99.9864% 일치
        ColumnRule("acc_trde_prica", "volume_shr", KIND_NUMERIC, *p_headroom(10)),
        _flow_krw("ind_invsr"), _flow_krw("frgnr_invsr"), _flow_krw("orgn"),
        _flow_krw("fnnc_invt"), _flow_krw("insrnc"), _flow_krw("invtrt"),
        _flow_krw("etc_fnnc"), _flow_krw("bank"), _flow_krw("penfnd_etc"),
        _flow_krw("samo_fund"), _flow_krw("natn"), _flow_krw("etc_corp"),
        _flow_krw("natfor"),
        _SRC_API,
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(dt, 1, 4)",
    partition_src="dt",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,                     # 수급 = 공표 시점 미상 (§6 주의 — lag 0 적용 금지)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 원장 PK (ticker, dt) — load_kiwoom_raw.py DDL
    extras=(
        ExtraColumn("close_krw_dir", "CASE WHEN s.\"cur_prc\" LIKE '-%' THEN -1 ELSE 1 END"),
    ),
    invariants=(
        Invariant("volume_negative", "volume_shr < 0"),      # survey 전수 n_neg=0
        Invariant("close_negative", "close_krw < 0"),        # abs 정책 회귀 가드
    ),
)

# ── stg_short_daily_kiwoom (ka10014) ──────────────────────────────────────────
# `ovr_shrts_qty` 는 원문 그대로 싣는다 — 77.7% 리셋 판정(`_valid`)은 LAG·shard 조인이라
# §1 1:1 원리 위반. equity 가 `stg_shards_kiwoom` 을 재료로 파생한다(§4).
STG_SHORT_DAILY_KIWOOM = TableRule(
    name="stg_short_daily_kiwoom",
    sources=(SourceRef("kiwoom", "ka10014_short_selling", "ka10014"),),
    columns=(
        _DT, _TICKER,
        ColumnRule("close_pric", "close_krw", KIND_NUMERIC, *p_headroom(7), sign="abs"),
        ColumnRule("pred_pre_sig", "pred_pre_sig", KIND_TEXT),   # 부호 구분 코드 — 값이 아니라 어휘
        ColumnRule("pred_pre", "pred_pre_krw", KIND_NUMERIC, *p_headroom(6)),
        ColumnRule("flu_rt", "flu_rt_pct", KIND_NUMERIC, *p_headroom(4, 2)),
        ColumnRule("trde_qty", "trde_qty_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("shrts_qty", "shrts_qty_shr", KIND_NUMERIC, *p_headroom(8)),
        ColumnRule("ovr_shrts_qty", "ovr_shrts_qty_shr", KIND_NUMERIC, *p_headroom(9)),
        ColumnRule("trde_wght", "trde_wght_pct", KIND_NUMERIC, *p_headroom(5, 2),
                   sign="strip_plus"),   # SPEC §2-3: 음수 0행 — '+' 스트립만
        # 천원 단위(SPEC §2-4) ×1e3 → survey 최대 10자리 ×1e3 = 13자리
        ColumnRule("shrts_trde_prica", "shrts_trde_prica_krw", KIND_NUMERIC, *p_headroom(13),
                   unit_scale=1_000),
        ColumnRule("shrts_avg_pric", "shrts_avg_pric", KIND_NUMERIC, *p_headroom(7)),
        _SRC_API,
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(dt, 1, 4)",
    partition_src="dt",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 원장 PK (ticker, dt)
    extras=(
        ExtraColumn("close_krw_dir", "CASE WHEN s.\"close_pric\" LIKE '-%' THEN -1 ELSE 1 END"),
    ),
    invariants=(
        Invariant("short_qty_negative", "shrts_qty_shr < 0 OR ovr_shrts_qty_shr < 0"),
        Invariant("volume_negative", "trde_qty_shr < 0"),
    ),
)

# ── stg_foreign_daily (ka10008) ───────────────────────────────────────────────
STG_FOREIGN_DAILY = TableRule(
    name="stg_foreign_daily",
    sources=(SourceRef("kiwoom", "ka10008_foreign_holdings", "ka10008"),),
    columns=(
        _DT, _TICKER,
        ColumnRule("close_pric", "close_krw", KIND_NUMERIC, *p_headroom(7), sign="abs"),
        ColumnRule("pred_pre", "pred_pre_krw", KIND_NUMERIC, *p_headroom(6)),
        ColumnRule("trde_qty", "trde_qty_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("chg_qty", "chg_qty_shr", KIND_NUMERIC, *p_headroom(9)),
        # 음수 3행(전부 2010-05-12) 실재 — abs 금지. 씌우면 wght -900% 가 900% 로 승격된다
        ColumnRule("poss_stkcnt", "poss_stkcnt_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("wght", "wght_pct", KIND_NUMERIC, *p_headroom(5, 2), sign="strip_plus"),
        ColumnRule("gain_pos_stkcnt", "gain_pos_stkcnt_shr", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("frgnr_limit", "frgnr_limit", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("frgnr_limit_irds", "frgnr_limit_irds", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("limit_exh_rt", "limit_exh_rt_pct", KIND_NUMERIC, *p_headroom(5, 2),
                   sign="strip_plus"),
        _SRC_API,
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(dt, 1, 4)",
    partition_src="dt",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 원장 PK (ticker, dt)
    extras=(
        ExtraColumn("close_krw_dir", "CASE WHEN s.\"close_pric\" LIKE '-%' THEN -1 ELSE 1 END"),
    ),
    invariants=(
        Invariant("volume_negative", "trde_qty_shr < 0"),     # survey 전수 n_neg=0
        Invariant("gain_pos_negative", "gain_pos_stkcnt_shr < 0"),
    ),
)

# ── stg_lending_daily (ka20068) ───────────────────────────────────────────────
STG_LENDING_DAILY = TableRule(
    name="stg_lending_daily",
    sources=(SourceRef("kiwoom", "ka20068_lending_balance", "ka20068"),),
    columns=(
        _DT, _TICKER,
        ColumnRule("dbrt_trde_cntrcnt", "dbrt_trde_cntrcnt", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("dbrt_trde_rpy", "dbrt_trde_rpy", KIND_NUMERIC, *p_headroom(10)),
        # 증감은 부호가 값이다 — abs 금지(SPEC §2-3). 음수 1,338,339행 실재
        ColumnRule("dbrt_trde_irds", "dbrt_trde_irds", KIND_NUMERIC, *p_headroom(10)),
        ColumnRule("rmnd", "rmnd", KIND_NUMERIC, *p_headroom(10)),
        # 백만원(SPEC §2-4) ×1e6 → survey 최대 8자리 ×1e6 = 14자리
        ColumnRule("remn_amt", "remn_amt_krw", KIND_NUMERIC, *p_headroom(14),
                   unit_scale=1_000_000),
        _SRC_API,
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(dt, 1, 4)",
    partition_src="dt",
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 원장 PK (ticker, dt)
    invariants=(
        # SPEC §2-3 항등식 실측 100.000% — 체결 − 상환 = 증감
        Invariant("lending_delta", "dbrt_trde_irds <> dbrt_trde_cntrcnt - dbrt_trde_rpy"),
        Invariant("rmnd_negative", "rmnd < 0"),               # survey 전수 n_neg=0
    ),
)

# ── stg_master_daily (ka10099) — snap_date 축 누적 스냅샷 ──────────────────────
# `_current` 금지(§3 temporality ⓑ): 하루만 지나면 이름이 거짓이 된다. 대신 date 축으로 쌓인다.
# 상태 어휘는 COLLECT_PLAN §R1 게이트 E2 실측: 관리종목은 `state` 파이프 토큰(auditInfo 단독은
# 47% 누락) · 거래정지는 `auditInfo` · 정리매매는 `orderWarning='2'`.
STG_MASTER_DAILY = TableRule(
    name="stg_master_daily",
    sources=(SourceRef("kiwoom", "ka10099_stock_master", "ka10099"),),
    columns=(
        ColumnRule("snap_date", "date", KIND_DATE_YMD8, key=True),
        ColumnRule("code", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("mrkt_tp", "mrkt_tp", KIND_TEXT),          # 요청 시장 코드 0/10 — 어휘
        ColumnRule("name", "name", KIND_TEXT, normalize_text=True),
        # KRX LIST_SHRS 와 97.45% 대조(차이 24건은 기업행위) → 같은 개념·같은 이름
        ColumnRule("listCount", "list_shrs", KIND_NUMERIC, *p_headroom(16)),
        ColumnRule("auditInfo", "audit_info", KIND_TEXT),     # 상태 어휘 — 정규화 금지
        ColumnRule("regDay", "reg_date", KIND_DATE_YMD8),
        ColumnRule("lastPrice", "last_price", KIND_NUMERIC, *p_headroom(8)),
        ColumnRule("state", "state", KIND_TEXT),              # 파이프 다중값 — 분해는 extras
        ColumnRule("marketCode", "market_code", KIND_TEXT),
        ColumnRule("marketName", "market_name", KIND_TEXT, normalize_text=True),
        ColumnRule("upName", "up_name", KIND_TEXT, normalize_text=True),
        ColumnRule("upSizeName", "up_size_name", KIND_TEXT, normalize_text=True),
        ColumnRule("companyClassName", "company_class_name", KIND_TEXT, normalize_text=True),
        ColumnRule("orderWarning", "order_warning", KIND_TEXT),
        ColumnRule("nxtEnable", "nxt_enable", KIND_TEXT),
        ColumnRule("kind", "kind", KIND_TEXT),
    ),
    natural_key=("ticker", "date"),
    partition_class="date_axis",
    partition_expr="substr(snap_date, 1, 4)",
    partition_src="snap_date",
    observed_src="collected_at",
    write_mode="first_write_wins",       # INSERT OR IGNORE — master_daily.py
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,                     # 마스터 = 공표 시점 미상 (§6 주의)
    available=AvailableRule("column", column="date"),
    key_unique=True,                     # 원장 PK (snap_date, code) — master_daily.py DDL
    extras=(
        ExtraColumn("state_parts",
                    "CASE WHEN s.\"state\" IS NULL OR s.\"state\" = '' THEN NULL::VARCHAR[] "
                    "ELSE string_split(s.\"state\", '|') END"),
        ExtraColumn("is_admin_issue",
                    "list_contains(string_split(coalesce(s.\"state\", ''), '|'), '관리종목')"),
        ExtraColumn("is_trade_halt", "s.\"auditInfo\" = '거래정지'"),
        ExtraColumn("is_liquidation", "s.\"orderWarning\" = '2'"),
    ),
    invariants=(
        # survey/targets.py INVARIANTS "상장주식수 0이하" — survey 전수 n_zero=0 ∧ n_neg=0
        Invariant("list_shrs_nonpositive", "list_shrs <= 0"),
    ),
)

# ── stg_shards_kiwoom (ingest_shard) — 수집 단위 원구조 ────────────────────────
# 커버리지 한계: collected_at 08-23~24 2일뿐(§4). 결측 3분류 재료로 쓸 때 이 한계를 명시한다.
STG_SHARDS_KIWOOM = TableRule(
    name="stg_shards_kiwoom",
    sources=(SourceRef("kiwoom", "ingest_shard", "kiwoom"),),
    columns=(
        ColumnRule("src_api", "src_api", KIND_TEXT, key=True),
        ColumnRule("ticker", "ticker", KIND_TEXT, expected_len=6, key=True),
        ColumnRule("req_start", "req_start", KIND_DATE_YMD8, key=True),
        ColumnRule("req_end", "req_end", KIND_DATE_YMD8, key=True),
        ColumnRule("n_rows", "n_rows", KIND_NUMERIC, *p_headroom(4)),
        ColumnRule("cap", "cap", KIND_NUMERIC, *p_headroom(3)),
        ColumnRule("status", "status", KIND_TEXT),            # ok / truncated / empty
        ColumnRule("first_dt", "first_dt", KIND_DATE_YMD8),   # empty 샤드 60행은 NULL
        ColumnRule("last_dt", "last_dt", KIND_DATE_YMD8),
        ColumnRule("next_cursor", "next_cursor", KIND_TEXT),  # 전건 NULL — seam 복원 불가 증거
    ),
    natural_key=("src_api", "ticker", "req_start", "req_end"),
    partition_class="whole",
    partition_expr=None,
    partition_src=None,
    observed_src="collected_at",
    write_mode="upsert",
    fanout=1,
    payload_exclude=("collected_at",),
    lag_known=False,
    available=AVAILABLE_NONE,            # 수집 로그 — 비부여 (§6)
    key_unique=True,                     # 원장 PK (src_api, ticker, req_start, req_end)
    invariants=(
        Invariant("n_rows_negative", "n_rows < 0"),
    ),
)

TABLES: tuple[TableRule, ...] = (STG_FLOW_DAILY_KIWOOM, STG_SHORT_DAILY_KIWOOM,
                                 STG_FOREIGN_DAILY, STG_LENDING_DAILY, STG_MASTER_DAILY,
                                 STG_SHARDS_KIWOOM)
