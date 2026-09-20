"""S25 WICS 섹터 구성 스냅샷 — `sector_snapshot` (플랜 `2026-09-20-wics-weekly` T3, 사용자 09-20).

grain (`ticker`, `snapshot_date`) · date_axis `year(snapshot_date)`. 입력은 stage `stg_wics_components`
하나라 1단계 산출을 읽지 않는다(원장 → stage → 여기, 백필 없음 — 2026-09-18 부터).

**무엇을 주나** — 종목의 WICS L1(10)·L2(28) 소속과 유동주식수·유동시총(wiseindex 값). 용도는
업종중립화·상대성과·섹터캡(DATA_CATALOG MS-08). KRX 업종분류(MS-09)와 체계가 다르다.

**주간 스냅샷이 축이다** — 매주 토요일 03:00 KST 에 dt=금요일로 받는다. 스냅샷 사이 날짜는 이 표에
없고, 매크로 `v_sector(as_of)` 가 `snapshot_date <= as_of` 최신 행과 `days_since_snapshot` 을 준다.
소비자는 그 경과일로 "얼마나 묵은 분류인가" 를 판단한다(EQUITY_DESIGN §4-1).

**L2 행만 격자** — stage 에는 같은 종목이 L1 코드로 부른 행과 L2 코드로 부른 행으로 두 번 있다.
L2 행에 L1 코드·라벨이 함께 실리므로(`sec_cd`·`sec_nm`) L2 행만 쓰고, L1 행은 검산축이다.

테이블 특화 술어(`extra_gates`):
  EG3_sector_snapshot — 폐기형: 한 종목이 같은 날 두 L2 에 있음 0 · L1 행에는 있고 L2 행에는 없는
                        (ticker, date) 0 · L1 어휘 폐쇄(10코드) · L2 코드가 L1 접두와 어긋남 0 ·
                        ticker 폭 · 유동시총 ≤ 0 (원문 MKT_VAL 은 양수). 기록형: 스냅샷 수·종목 수·
                        L1 별 종목 수·최신 스냅샷일.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, FieldProfile, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# WICS L1 10코드 — 수집기 `wics_snapshot.L1` 과 같다(그쪽이 정본, 여기는 어휘 폐쇄용 사본).
WICS_L1_VOCAB: tuple[str, ...] = ("G10", "G15", "G20", "G25", "G30", "G35", "G40", "G45", "G50", "G55")
REJECT_REASONS: tuple[str, ...] = ()     # 격리 사유 없음 — stage 행을 그대로 싣는다
# EG1 우변 — L2 행의 (ticker, date) 수. 산출을 읽지 않는다.
# L2 행 = 요청 코드가 행의 L1 코드와 다른 행(G4530 ≠ G45). L1 행 = 같은 행(G45 = G45). `.sql` 과 같은 술어.
L2_GRID_SQL = ("SELECT DISTINCT ticker, date FROM stg_wics_components "
               "WHERE req_sec_cd <> sec_cd")
L1_GRID_SQL = ("SELECT DISTINCT ticker, date FROM stg_wics_components "
               "WHERE req_sec_cd = sec_cd")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


# ── EG3_sector_snapshot ──────────────────────────────────────────────────────

def eg3_sector_snapshot(ctx: EquityGateContext) -> GateResult:
    """L2 유일·L1↔L2 정합·어휘·폭·부호. 산출식을 재계산하지 않고 stage 원표로 대조한다."""
    v = _q(ctx.out_view)
    n_l2_dup, n_l1_only, n_l1_vocab, n_l2_prefix, n_ticker_bad, n_nonpositive = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM (SELECT ticker, snapshot_date FROM {v} GROUP BY 1, 2 HAVING count(*) > 1)),
          (SELECT count(*) FROM ({L1_GRID_SQL}) a
            WHERE NOT EXISTS (SELECT 1 FROM {v} s WHERE s.ticker = a.ticker AND s.snapshot_date = a.date)),
          (SELECT count(*) FROM {v} WHERE wics_l1_cd IS NULL OR wics_l1_cd NOT IN ({_vocab_sql(WICS_L1_VOCAB)})),
          (SELECT count(*) FROM {v} WHERE wics_l2_cd IS NULL OR substr(wics_l2_cd, 1, 3) <> wics_l1_cd),
          (SELECT count(*) FROM {v} WHERE ticker IS NULL OR length(ticker) <> {TICKER_LEN}),
          (SELECT count(*) FROM {v} WHERE float_mktcap_krw IS NOT NULL AND float_mktcap_krw <= 0)""")
    checks = {
        "n_l2_duplicate_membership": int(str(n_l2_dup)),
        "n_l1_only_tickers": int(str(n_l1_only)),
        "n_l1_outside_vocab": int(str(n_l1_vocab)),
        "n_l2_prefix_mismatch": int(str(n_l2_prefix)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
        "n_nonpositive_float_mktcap": int(str(n_nonpositive)),
    }
    n_snap, n_ticker, max_date = _row(ctx, f"""
        SELECT count(DISTINCT snapshot_date), count(DISTINCT ticker), max(snapshot_date) FROM {v}""")
    by_l1 = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT wics_l1_cd, count(*) FROM {v} WHERE snapshot_date = (SELECT max(snapshot_date) FROM {v}) "
        "GROUP BY 1 ORDER BY 1").fetchall()}
    metrics: dict[str, object] = {
        "n_snapshots": int(str(n_snap)), "n_tickers": int(str(n_ticker)),
        "latest_snapshot_date": None if max_date is None else str(max_date),
        "latest_by_l1": by_l1, "l1_vocab": list(WICS_L1_VOCAB),
    }
    return _result("EG3_sector_snapshot", checks, metrics, "L2 유일·L1↔L2 정합·어휘·폭 성립")


eg3_sector_snapshot.gate_name = "EG3_sector_snapshot"     # type: ignore[attr-defined]


# ── 필드 프로파일 (선언만 — S19 등재는 보류, 아래 선언부 주석) ──────────────────
_AXIS = ("ticker", "snapshot_date")
_DISC = ("wiseindex 일별 산출(관행). 우리는 토요일 03:00 KST 에 dt=금요일 스냅샷을 받는다 — "
         "공표 시각은 실측하지 않았고, 실사용 랙은 1캘린더일 이상이다")
_WEEKLY = ("주 1회 스냅샷(2026-09-18~). 스냅샷 사이 날짜는 v_sector(as_of) 의 days_since_snapshot 으로 "
           "묵은 정도를 읽는다. 백필 없음(사용자 09-20) — 2026-09-18 이전 구간에는 섹터 축이 없다")


def _sector(field_id: str, columns: tuple[str, ...], label: str, unit: str, value_type: str,
            evidence: str) -> FieldProfile:
    return FieldProfile(
        field_id=field_id, columns=columns, label=label, unit=unit, value_type=value_type,
        frequency="event", recommended_lag_sessions=1, recommended_lag_days=1,
        point_in_time=True, requires_confirmation=False, disclosure_basis=_DISC,
        evidence=f"{evidence} {_WEEKLY}", coverage_axis="grid_security", scope="internal",
        axis_columns=_AXIS)


FIELDS: tuple[FieldProfile, ...] = (
    _sector("sector.wics_l1", ("wics_l1_cd", "wics_l1_nm"), "WICS L1 섹터(10)", "", "category",
            "sector_snapshot.wics_l1_cd — L2 행의 SEC_CD(WICS_PROBE §6). 업종중립화·섹터캡 축."),
    _sector("sector.wics_l2", ("wics_l2_cd", "wics_l2_nm"), "WICS L2 산업(28)", "", "category",
            "sector_snapshot.wics_l2_cd — 요청 코드(IDX_CD), 라벨은 IDX_NM_KOR."),
    _sector("sector.float_shares", ("float_shares_shr",), "유동주식수(WICS)", "주", "count",
            "sector_snapshot.float_shares_shr — wiseindex APT_SHR_CNT. E04 실질 유통비율의 후보 분모(현 DART 축, TECH_DEBT)."),
    _sector("sector.float_mktcap", ("float_mktcap_krw",), "유동시총(WICS)", "KRW", "amount",
            "sector_snapshot.float_mktcap_krw — wiseindex MKT_VAL(백만원) × 1e6, stage 픽스처로 스케일 고정."),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

SECTOR_SNAPSHOT = register(EquityTable(
    name="sector_snapshot",
    grain=("ticker", "snapshot_date"),
    columns={"ticker": "VARCHAR", "snapshot_date": "DATE",
             "wics_l1_cd": "VARCHAR", "wics_l1_nm": "VARCHAR",
             "wics_l2_cd": "VARCHAR", "wics_l2_nm": "VARCHAR",
             "float_shares_shr": "DECIMAL(20,0)", "float_mktcap_krw": "DECIMAL(20,0)",
             "wgt_in_l2_pct": "DECIMAL(12,6)",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_wics_components",),
    partition_class="date_axis",
    partition_key_expr="year(snapshot_date)",
    available_rule=("column:snapshot_date — WICS 기준일(dt). stage lag_known=false, 공표 시각 미실측이라 "
                    "basis 'convention'"),
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=f"SELECT count(*) FROM ({L2_GRID_SQL})",
    sql_path=SQL_DIR / "sector_snapshot.sql",
    input_columns={"stg_wics_components": ("ticker", "date", "req_sec_cd", "idx_nm", "sec_cd", "sec_nm",
                                           "float_shares_shr", "float_mktcap_krw", "wgt_pct")},
    available_basis=("convention",),
    content_date_column="snapshot_date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_sector_snapshot,),
    # S19 `dataset_profile` 등재는 보류 — `rules_s19.SOURCE_TABLES` 가 코드 고정 목록이라 이 표를 넣으면
    # 이 표가 없는 equity 루트(테스트 절단본·과거 판)에서 S19 가 FileNotFoundError 로 죽는다(coverage_daily 와
    # 같은 사정, TECH_DEBT B-1). 선언(`FIELDS`)은 여기 두고 S19 가 선택 입력을 지원할 때 `field_profiles=FIELDS` 로 켠다.
    field_profiles=(),
))

TABLES: tuple[EquityTable, ...] = (SECTOR_SNAPSHOT,)

__all__ = ["FIELDS", "L1_GRID_SQL", "L2_GRID_SQL", "REJECT_REASONS", "SECTOR_SNAPSHOT", "TABLES",
           "WICS_L1_VOCAB"]
