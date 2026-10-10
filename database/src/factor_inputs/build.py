"""factor_inputs 판 빌드 — equity/stage 현재 판 → 8표 parquet → 게이트 → MANIFEST 교체.

흐름 (equity `build.py` 와 같은 모양):
  원천 판 해석(MANIFEST `current_build`, 또는 `--builds-from` 인계 이력이 가리킨 판 — 맨 glob 금지)
  → 판 가드(아침판에 저녁 equity 판 금지 · 가격·수정주가·계수 판이 같은 체인)
  → TEMP VIEW → `queries` 순서대로 임시 표
  → `_tmp/<build_id>/<표>/part0.parquet` → 게이트(FG0~FG4 · FG-fresh)
  → 통과: 8표 모두 `v=<build_id>` 로 옮기고 표마다 `stage.manifest.commit`(keep=KEEP_DEFAULT=60)
             + 판 manifest `_runs/<D>_<basis>.json` + `latest_<basis>.json`
  → 실패: 임시 폐기 + `_failed/<build_id>.json` + `_runs/<D>_<basis>.json`(status gate_failed).
            MANIFEST·latest 는 건드리지 않는다(마지막 성공 판 유지).
            같은 날 성공 기록이 있으면 `_runs` 는 덮지 않는다(실패는 `_failed/` 에만, D-09).

판 id 하나(`m_<UTC>`)를 8표가 공유한다. 표마다 포인터를 따로 바꾸므로 전환 순간에는 표끼리 판이
섞여 보일 수 있다 — 소비자는 `latest_<basis>.json` 의 `build_id` 로 읽는다(표마다 keep=KEEP_DEFAULT=60 이라
그 판은 다음 59번의 빌드 동안 남는다).

입력은 `_pinned/` 하드링크로 고정하지 않는다: 산출 자체가 창을 자른 사본이라 재현에 원천 판을
붙잡을 필요가 없고, equity 루트에 쓰지 않기 위해서다. 읽은 판 id 는 표별 BuildRecord.inputs 와
판 manifest 에 남는다(V2-8).

날짜로 고정(`--builds-from`, 컷오버 T-2): 인계 이력 `data/deliver/history/<D'>_morning.json` 이 적은
판 id 를 읽는다(읽기는 `equity.handoff`, compat 과 공유). 다음이면 `FactorInputsError`(rc 2) — 최신
판으로 대신하지 않는다(P1):
  이력이 없다 · health 가 stage=ok·equity=ok 가 아니다 · 이력 basis 가 morning 이 아니다 ·
  이력 date 가 없거나 --date 보다 뒤다 · 표가 이력에 없다 · 그 판이 MANIFEST 에서 사라졌다.
  예외: 선택 원천(`OPTIONAL_STAGE_SOURCES`, WISE 분기)은 이력에 키가 없으면 그날도 판이 없던 것이라
  current 모드와 같이 빈 표(`absent`)로 대신한다(이력에 키가 있는데 판이 없으면 rc 2).
이력이 D' = 직전 거래일인지는 여기서 보지 않는다(장 마감 체인 PR-8 이 경로를 고를 때 맞춘다).
이력 경로·날짜는 판 manifest `builds_from`·`builds_from_date` 에 남는다.

장 마감 판(`--basis evening`, 컷오버 T-2 · PR-4): --date 는 오늘 T, 원천은 직전 거래일 D' 아침
확정판을 `--builds-from` 으로 고정한 것뿐이다(고정 없으면 rc 2). 산출 루트는 연구 루트
(`research_root()`)와 달라야 한다(T-3 — 같으면 rc 2). 세션 = 연구 판 trading_calendar ∪
{T}, 유니버스 = D' 행 이월, 정보 시점(asof) = D' (`queries` 모듈 docstring '두 날짜'). 다음이면
`FactorInputsError`(rc 2) — 사유를 메시지에 남긴다:
  (a) T 가 `daily.calendar` 거래일이 아니다(휴장·주말) 또는 판정 달력을 못 읽는다
  (b) MD-SEAM — `daily.calendar` 의 T 직전 거래일 D' ≠ 연구 판 trading_calendar 의 마지막 날
      (연구 판이 D' 까지 오지 않았거나, 이미 T 를 담고 있다)
T 행(가격·수정주가·수급)은 아직 얹지 않는다 — 자리(`queries.t_prices_sql`)만 있고 채우는 것은
PR-5 다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from daily import calendar as daily_calendar
from equity import handoff, inputs
from equity.rules_s10 import FIELDS as CREDIT_FIELDS
from model.contracts import FI_TABLES, UniverseRule
from stage import manifest
from stage.gates import GateResult, GateStatus
from stage.model import basis_of_build_id, build_id_time, make_build_id

from . import gates, queries

# 1.1.0(2026-10-05): WISE 분기 원천(T-Q4) · 종목명 KRX 약명(N-8) · 추정치 유예 기본 0(N-14) ·
# WISE 수집 지연 허용 1거래일(N-12, 유예와 분리)
# 1.2.0(2026-10-08, 배포 묶음 4-2b): 금융업 연간 매출(영업수익·순영업이익) + 연간 revenue_basis ·
# fi_fin_summary.period_months 열(G-28 짧은 첫 사업연도, N-25 Q5)
# 1.3.0(2026-10-08, 배포 묶음 6-3 · N-33): fi_adj_prices.adj_ok 를 가격 축 미해결
# (`adj_factor.price_resolution = 'unresolved'`)로 좁히고 adj_factor 열 = cum_share ÷ cum_price_only
# (⑤ 가격 전용 계수). equity e1.26.0 판의 새 열을 읽는다 — 옛 판이면 멈춘다(`_check_columns`)
# 1.4.0(2026-10-13 예정, 배포 묶음 7 · N-37): fi_fin_summary WISE 연간 판을 (종목, ep) 단위 최신 ≤ D 로
# (stage 2.7.0 연속 판 접기 — D7-4), available_date 의 WISE 날짜가 '처음 본 날'로,
# FG1 손익(op·ni) 비율 기록(D7-8)
# 1.5.0(2026-10-10, 컷오버 PR-4 · T-2): 장 마감 판(evening) 세션·유니버스·시점 — 세션 = 연구 판
# 달력 ∪ {T}, 유니버스 = D' 행 이월 · 시총 = D' 주식수 × T 종가(`t1_shares_x_t_close`, T 행 자리
# `_t_prices`, 출처 어휘 'postclose'), WISE·DART·universe_daily·WICS·기업행위 입력 fetched/available
# ≤ D'(asof). 아침판 SQL 은 그대로
RULES_VERSION = "fi1.5.0"
LAYER = "factor_inputs"
BASES_KNOWN = ("evening", "morning")
# fi 가 읽는 equity 판 basis — 아침 확정판·수동 재빌드만(`_check_basis`)
EQUITY_BASES_READ = ("manual", "morning")
# eligible 하한 — 09-23 실측 580 · 09-28 619 의 절반. 빈 유니버스를 성공으로 쓰지 않는다
MIN_ELIGIBLE_DEFAULT = 300
# 모델 판과 같은 수 — 모델 판이 가리키는 fi 판이 지워지지 않게(E-01·N-17)
# fi 판 1개 ≈ 25MB(60판 ≈ 1.5GB), 모델과 1:1 로 지을 때만 맞물린다
KEEP_DEFAULT = 60
GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "golden.json"
# 가격·수정주가·계수 판이 같은 체인인지 보는 빌드 시각 차 한도(시간) — compat R9 와 같은 값.
BUILD_CHAIN_MAX_GAP_H = 3
DUCKDB_THREADS = 3
DUCKDB_MEMORY_LIMIT = "8GB"

EQUITY_SOURCES = ("trading_calendar", "universe_daily", "security", "price_daily",
                  "price_adj_daily", "adj_factor", "flow_daily", "credit_daily",
                  "sector_snapshot", "coverage_daily", "fin_std", "dividend_event",
                  "audit_opinion", "disclosure_version", "corp")
STAGE_SOURCES = ("stg_consensus_annual", "stg_consensus_matrix", "stg_fin_wise", "stg_fin_wise_q")
# 있으면 쓰는 stage 원천 — 판이 없으면 같은 열의 빈 표로 대신한다(판 id 'absent'). stg_fin_wise_q(WISE 분기,
# 10-01 T-Q4)는 수집 첫날 이전 날짜에 스냅샷이 없으므로 그 날짜의 분기는 DART(fin_std)로 돌아간다.
_EMPTY_FIN_WISE_Q = (
    "(SELECT CAST(NULL AS VARCHAR) AS ticker, CAST(NULL AS DATE) AS fetched_date, "
    "CAST(NULL AS VARCHAR) AS pkey, CAST(NULL AS VARCHAR) AS p_accode, CAST(NULL AS VARCHAR) AS acc_nm, "
    + ", ".join(f"CAST(NULL AS VARCHAR) AS period_{i}, CAST(NULL AS BOOLEAN) AS is_est_{i}, "
                f"CAST(NULL AS VARCHAR) AS basis_{i}, CAST(NULL AS DECIMAL(38, 6)) AS val_{i}"
                for i in range(1, 7))
    + " WHERE false)")
OPTIONAL_STAGE_SOURCES: dict[str, str] = {"stg_fin_wise_q": _EMPTY_FIN_WISE_Q}
_CHAIN_TABLES = ("price_daily", "price_adj_daily", "adj_factor")


class FactorInputsError(Exception):
    """입력·판·인자 오류 — 판을 만들지 않고 멈춘다(CLI rc 2)."""


def research_root() -> Path:
    """연구(아침 확정판) fi 루트 `<QL_HOME>/data/factor_inputs`(없으면 저장소 `database/` 아래) —
    CLI `--root` 기본값. 장 마감 판은 여기에 짓지 않는다(T-3, `build`)."""
    base = Path(os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    return base / "data" / "factor_inputs"


def _credit_lag_sessions() -> int:
    """KIS 신용잔고 실입수 랙 — equity FieldProfile 이 정본(09-19 감사 E01)."""
    fp = next(f for f in CREDIT_FIELDS if f.field_id == "credit.margin_balance")
    if fp.recommended_lag_sessions is None:
        raise FactorInputsError("equity credit.margin_balance 에 recommended_lag_sessions 가 없다")
    return int(fp.recommended_lag_sessions)


@dataclass(frozen=True)
class BuildResult:
    status: str                              # 'ok' | 'gate_failed'
    build_id: str
    date: str
    basis: str
    tables: dict[str, dict[str, object]]     # 표 → n_rows · content_hash · inputs
    gates: list[GateResult]
    run_manifest: Path                       # kept 면 그날 성공 판 기록(이 빌드: failed_report)
    failed_report: Path | None
    elapsed_s: float
    coverage: dict[str, object] = field(default_factory=dict)
    run_manifest_kept: bool = False          # FAIL 이지만 같은 날 성공 기록을 덮지 않았다(D-09)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def summary(self) -> str:
        rows = " ".join(f"{t}={v['n_rows']}" for t, v in self.tables.items())
        fails = [g.name for g in self.gates if g.status is GateStatus.FAIL]
        cov = self.coverage
        return (f"factor_inputs {self.status} date={self.date} basis={self.basis} "
                f"build={self.build_id} | {rows} | eligible={cov.get('n_eligible')} "
                f"states={cov.get('counts')} lapsed_dropped={cov.get('n_lapsed_dropped')}"
                + (f" | FAIL={','.join(fails)}" if fails else ""))


# ── 입력 판 ──────────────────────────────────────────────────────────────────
def _expr(globs: tuple[str, ...], stage: bool) -> str:
    """equity 산출은 hive 축이 없고 stage 는 `year=` 축이 있다(compat `_globs_expr` 와 같다)."""
    lit = ", ".join(f"'{Path(g).resolve()}'" for g in globs)
    if stage:
        return f"read_parquet([{lit}], hive_partitioning=true, union_by_name=true)"
    return f"read_parquet([{lit}], hive_partitioning=false)"


def _resolve(root: Path, tables: tuple[str, ...], stage: bool,
             pinned: dict[str, str] | None = None,
             origin: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """표 → (판 id, 읽기 식). `pinned`(인계 이력 `origin` 의 판 목록)를 주면 current 가 아니라
    그 판을 읽는다."""
    ids: dict[str, str] = {}
    exprs: dict[str, str] = {}
    for t in tables:
        want = None if pinned is None else pinned.get(t)
        if pinned is not None and want is None:
            # 그날도 판이 없던 선택 원천 — current 모드와 같이 빈 표로 대신한다
            if stage and t in OPTIONAL_STAGE_SOURCES:
                ids[t], exprs[t] = "absent", OPTIONAL_STAGE_SOURCES[t]
                continue
            raise FactorInputsError(f"인계 이력에 이 표의 판이 없다: 이력={origin} table={t} "
                                    "— 최신 판으로 대신하지 않는다")
        try:
            pb = inputs.resolve(root, t, want)
        except FileNotFoundError as e:
            if want is not None:
                raise FactorInputsError(
                    f"인계 이력이 가리킨 판이 MANIFEST 에 없다(GC 로 지워짐?): 이력={origin} "
                    f"table={t} build_id={want} — 최신 판으로 대신하지 않는다 ({e})") from e
            if stage and t in OPTIONAL_STAGE_SOURCES:
                ids[t], exprs[t] = "absent", OPTIONAL_STAGE_SOURCES[t]
                continue
            raise FactorInputsError(f"원천 판이 없다: {root}/{t} ({e})") from e
        ids[t] = pb.build_id
        exprs[t] = _expr(pb.globs, stage)
    return ids, exprs


def _load_handoff(path: Path, d: date) -> handoff.Handoff:
    """인계 이력을 읽고 다음을 본다.
    ① 그날 stage·equity 가 둘 다 ok — 실패한 날의 이력은 판 목록에 직전 판이 섞여 있다
    ② 아침 확정판(basis=morning) 이력
    ③ 이력 날짜 ≤ 판 기준일 D — 뒤면 D 에 미래 판을 읽는다"""
    try:
        h = handoff.load(path)
    except handoff.HandoffError as e:
        raise FactorInputsError(str(e)) from e
    if not h.health_ok:
        raise FactorInputsError(
            f"인계 이력의 health 가 ok 가 아니다(그날 확정판 실패 — 판 목록에 직전 판이 섞여 "
            f"있다): 이력={path} health={h.health} 기대={{'stage': 'ok', 'equity': 'ok'}}")
    if h.basis != "morning":
        raise FactorInputsError(f"인계 이력이 아침 확정판(basis=morning) 것이 아니다: 이력={path} "
                                f"basis={h.basis}")
    try:
        hd = datetime.strptime(h.date or "", "%Y%m%d").date()
    except ValueError as e:
        raise FactorInputsError(f"인계 이력 date 가 YYYYMMDD 가 아니다: 이력={path} "
                                f"date={h.date}") from e
    if hd > d:
        raise FactorInputsError(f"인계 이력 날짜가 판 기준일보다 뒤다(미래 판을 읽게 된다): "
                                f"이력={path} date={h.date} --date={d.strftime('%Y%m%d')}")
    return h


def _evening_dprime(t: date, calendar_dir: Path | None) -> date:
    """장 마감 판 조건 (a) — T 가 `daily.calendar` 거래일이어야 한다. T 의 직전 거래일 D' 를 준다.
    달력을 못 읽거나 그 해를 덮지 않으면 영업일을 가정하지 않고 멈춘다(K1-9 ⑦)."""
    try:
        cal = (daily_calendar.load() if calendar_dir is None
               else daily_calendar.load(calendar_dir))
        is_session = cal.is_trading_day(t)
        dprime = cal.prev_trading_day(t)
    except (daily_calendar.CalendarUnavailable, KeyError) as e:
        raise FactorInputsError(f"장 마감 판 T={t.isoformat()} 거래일 판정 불가 — "
                                f"daily.calendar 를 읽지 못했다(calendar_dir={calendar_dir}): "
                                f"{e}") from e
    if not is_session:
        raise FactorInputsError(f"장 마감 판 T={t.isoformat()} 는 daily.calendar 거래일이 아니다"
                                "(휴장·주말) — 잠정 세션을 만들지 않는다")
    return dprime


def _check_seam(con: duckdb.DuckDBPyConnection, t: date, dprime: date,
                origin: Path | None) -> None:
    """장 마감 판 조건 (b) MD-SEAM — 고정한 연구 판의 거래일 축이 D'(T 의 직전 거래일)에서 끝나야
    T 를 잠정 세션으로 붙일 수 있다. 앞이면 연구 판이 D' 까지 오지 않았고(낡은 판), 뒤면 판이 이미
    T 를 담고 있다(T 가 두 번 선다)."""
    last = _one(con, "SELECT max(date) FROM trading_calendar")
    if last != dprime:
        raise FactorInputsError(
            f"장 마감 판 이음매(MD-SEAM) 불일치: T={t.isoformat()} 의 직전 거래일 "
            f"D'={dprime.isoformat()}(daily.calendar) ≠ 연구 판 trading_calendar 마지막={last} "
            f"— 연구 판이 D' 에서 끝나야 한다(이력={origin})")


def _check_basis(equity_builds: dict[str, str], basis: str) -> None:
    """fi 는 아침 확정판(`m_`)과 수동 재빌드(`b_`) equity 판만 읽는다(compat R5 와 같다). 장 마감
    판도 직전 거래일 확정판을 `--builds-from` 으로 고정해 읽으므로(T-2 — 고정 없음은 `build` 가
    먼저 거절한다) 같은 규칙이다. 저녁 잠정판(`e_`)은 basis 와 상관없이 섞지 않는다."""
    for table, bid in sorted(equity_builds.items()):
        got = basis_of_build_id(bid)
        if got not in EQUITY_BASES_READ:
            raise FactorInputsError(f"equity 판 접두어가 허용 밖이다(아침 확정 m_·수동 b_ 만): "
                                    f"table={table} build_id={bid} 판={got} --basis={basis}")


def _check_chain(equity_builds: dict[str, str]) -> None:
    """가격·수정주가·계수 판이 같은 체인이어야 adj_close 와 adj_ok 가 같은 사건 집합을 본다."""
    times = {t: build_id_time(equity_builds[t]) for t in _CHAIN_TABLES}
    known = {t: ts for t, ts in times.items() if ts is not None}
    if len(known) < 2:
        return
    gap_h = (max(known.values()) - min(known.values())).total_seconds() / 3600
    if gap_h > BUILD_CHAIN_MAX_GAP_H:
        raise FactorInputsError(
            f"가격·수정주가·계수 판의 빌드 시각 차가 {gap_h:.1f}시간(한도 "
            f"{BUILD_CHAIN_MAX_GAP_H}): " + ", ".join(f"{t}={equity_builds[t]}"
                                                    for t in _CHAIN_TABLES))


def _check_columns(con: duckdb.DuckDBPyConnection, equity_builds: dict[str, str]) -> None:
    """fi 가 읽는 equity 새 열이 판에 있는지 — 옛 판(e1.26.0 이전)을 읽으면 SQL 바인딩 오류 대신
    판·열을 짚어 멈춘다(판 섞임을 조용히 넘기지 않는다, P1)."""
    for table, want in sorted(queries.ADJ_REQUIRED_COLUMNS.items()):
        rel = con.execute(f'SELECT * FROM "{table}" LIMIT 0')
        have = {d[0] for d in rel.description}
        missing = [c for c in want if c not in have]
        if missing:
            raise FactorInputsError(
                f"equity 판에 fi {RULES_VERSION} 가 읽는 열이 없다(e1.26.0 ⑤ 이전 판): "
                f"table={table} build_id={equity_builds[table]} 없는 열={missing} "
                f"— equity 를 e1.26.0 이상으로 다시 지은 뒤 돌린다")


# ── 쓰기 ─────────────────────────────────────────────────────────────────────
def _content_hash(con: duckdb.DuckDBPyConnection, path: Path) -> str:
    """equity `build._content_hash` 와 같은 식(행 struct 문자열 해시의 xor)."""
    row = con.execute("SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM "
                      f"read_parquet('{path}', hive_partitioning=false) t").fetchone()
    n, h = (0, None) if row is None else row
    return f"{int(str(n))}:{'0' if h is None else format(int(str(h)), 'x')}"


def _write_json(path: Path, obj: object) -> None:
    """임시 파일 → os.replace — 부분 기록이 읽히지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as e:
        raise FactorInputsError(f"--date 는 YYYYMMDD 여야 한다: {value!r}") from e


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


# ── 빌드 ─────────────────────────────────────────────────────────────────────
def build(date_s: str, basis: str, root: Path, stage_root: Path, equity_root: Path, *,
          grace_days: int | None = None, min_eligible: int = MIN_ELIGIBLE_DEFAULT,
          golden_path: Path | None = GOLDEN_PATH, keep: int = KEEP_DEFAULT,
          build_id: str | None = None, builds_from: Path | None = None,
          calendar_dir: Path | None = None) -> BuildResult:
    """판 기준일 D(YYYYMMDD)의 factor_inputs 8표를 굽는다. 게이트 FAIL 은 결과 status 로,
    입력·인자 오류는 `FactorInputsError` 로 낸다. `builds_from`(인계 이력 JSON)을 주면 원천 판을
    current 가 아니라 그 이력의 판으로 고정한다(모듈 docstring '날짜로 고정'). `calendar_dir` 는
    장 마감 판의 거래일 판정 달력(`daily.calendar` 연도 파일 폴더, 없으면 그 모듈 기본 경로)."""
    t0 = time.time()
    d = _parse_date(date_s)
    if basis not in BASES_KNOWN:
        raise FactorInputsError(f"--basis 는 {BASES_KNOWN} 중 하나: {basis!r}")
    evening = basis == "evening"
    if evening and builds_from is None:
        raise FactorInputsError(
            "--basis evening 은 --builds-from(직전 거래일 아침 확정판 인계 이력)으로 판을 "
            "고정해야만 짓는다(T-2) — 고정 없이 current 판을 읽으면 낡은 판을 조용히 쓰게 "
            "된다(P1)")
    if evening and Path(root).resolve() == research_root().resolve():
        raise FactorInputsError(
            f"장 마감 판을 연구 fi 루트({research_root()})에 짓지 않는다 — 같은 루트를 쓰면 "
            "keep 이 하루 2판씩 소모돼 모델 판이 가리키는 fi 판이 GC 된다. --root data/model_db/"
            "factor_inputs 로 분리한다(T-3)")
    dprime = _evening_dprime(d, calendar_dir) if evening else None
    rule = UniverseRule() if grace_days is None else UniverseRule(coverage_grace_days=grace_days)
    if rule.coverage_grace_days < 0:
        raise FactorInputsError(f"--grace-days 는 0 이상: {rule.coverage_grace_days}")
    bid = build_id or make_build_id(basis)
    if basis_of_build_id(bid) not in ("manual", basis):
        raise FactorInputsError(f"build_id 접두어가 --basis 와 다르다: {bid} vs {basis}")

    pin = None if builds_from is None else _load_handoff(Path(builds_from), d)
    equity_builds, eq_exprs = _resolve(Path(equity_root), EQUITY_SOURCES, stage=False,
                                       pinned=None if pin is None else pin.equity_builds,
                                       origin=builds_from)
    stage_builds, st_exprs = _resolve(Path(stage_root), STAGE_SOURCES, stage=True,
                                      pinned=None if pin is None else pin.stage_builds,
                                      origin=builds_from)
    _check_basis(equity_builds, basis)
    _check_chain(equity_builds)

    root = Path(root)
    tmp_root = root / "_tmp" / bid
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True)
    d_iso = d.isoformat()
    con = duckdb.connect()
    try:
        con.execute(f"SET threads = {DUCKDB_THREADS}")
        con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
        con.execute("SET enable_progress_bar = false")
        for name, expr in {**eq_exprs, **st_exprs}.items():
            con.execute(f'CREATE OR REPLACE TEMP VIEW "{name}" AS SELECT * FROM {expr}')
        _check_columns(con, equity_builds)
        if dprime is not None:
            _check_seam(con, d, dprime, builds_from)
        asof = d_iso if dprime is None else dprime.isoformat()
        con.execute(queries.calendar_sql(d_iso if evening else None))
        if not _one(con, f"SELECT count(*) FROM _calx WHERE date = DATE '{d_iso}'"):
            raise FactorInputsError(
                f"D={d_iso} 는 trading_calendar 의 거래일이 아니다(휴장일 또는 달력 밖 — 달력 "
                f"마지막 {_one(con, 'SELECT max(date) FROM _calx')})")
        flow_from = _one(con, f"SELECT min(date) FROM (SELECT date FROM _calx WHERE date <= "
                              f"DATE '{d_iso}' ORDER BY date DESC LIMIT {queries.FLOW_SESSIONS})")
        p = queries.Params(
            d=d_iso, fy=f"{d.year}12",
            price_from=(d - timedelta(days=queries.PRICE_WINDOW_DAYS)).isoformat(),
            flow_from=str(flow_from), grace_days=rule.coverage_grace_days,
            credit_lag=_credit_lag_sessions(), basis=basis, asof=asof)
        for sql in queries.coverage_sqls(p):
            con.execute(sql)
        dstar = _one(con, "SELECT dstar FROM _dstar")
        # N-12 수집 지연 = (D*, 예상 수집일] 거래일 수. 예상 수집일 = asof — 장 마감 판은 D'(T 저녁
        # 수집은 아직 없다). T 로 재면 정상 상태가 lag 1 이 되어 하루만 빠져도 FAIL 이다.
        lag = None if dstar is None else int(str(_one(
            con, f"SELECT count(*) FROM _calx WHERE date > DATE '{dstar}' "
                 f"AND date <= DATE '{asof}'")))
        for _, sql in queries.table_sqls(p, rule):
            con.execute(sql)

        tables: dict[str, dict[str, object]] = {}
        table_inputs: dict[str, dict[str, str]] = {}
        for name in FI_TABLES:
            out = tmp_root / name / "part0.parquet"
            out.parent.mkdir(parents=True)
            con.execute(f"COPY _{name} TO '{out}' (FORMAT PARQUET)")
            con.execute(f'CREATE OR REPLACE TEMP VIEW "g_{name}" AS '
                        f"SELECT * FROM read_parquet('{out}', hive_partitioning=false)")
            n = int(str(_one(con, f'SELECT count(*) FROM "g_{name}"')))
            table_inputs[name] = {s: (equity_builds.get(s) or stage_builds[s])
                                  for s in queries.TABLE_SOURCES[name]}
            tables[name] = {"n_rows": n, "content_hash": _content_hash(con, out),
                            "inputs": table_inputs[name], "window": FI_TABLES[name].window}

        ctx = gates.GateContext(
            con=con, date=d_iso, basis=basis, price_from=p.price_from, flow_from=p.flow_from,
            rule=rule, min_eligible=min_eligible, dstar=None if dstar is None else str(dstar),
            collection_lag_sessions=lag, golden=gates.load_golden(golden_path), asof=asof)
        results = gates.run_all(ctx)
    except BaseException:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    finally:
        con.close()

    fresh = next((g for g in results if g.name == "FG-fresh"), None)
    fg1 = next((g for g in results if g.name == "FG1"), None)
    coverage: dict[str, object] = {
        "last_collection_date": None if dstar is None else str(dstar),
        "collection_lag_sessions": lag, "grace_days": rule.coverage_grace_days,
        "fy": p.fy,
        "counts": (fresh.metrics.get("counts") if fresh else None),
        "counts_eligible": (fresh.metrics.get("counts_eligible") if fresh else None),
        "n_lapsed_dropped": (fresh.metrics.get("n_lapsed_dropped") if fresh else None),
        "n_eligible": (fg1.metrics.get("n_eligible") if fg1 else None)}
    failed = [g for g in results if g.status is GateStatus.FAIL]
    elapsed = round(time.time() - t0, 1)
    status = "gate_failed" if failed else "ok"
    run_manifest = root / "_runs" / f"{d.strftime('%Y%m%d')}_{basis}.json"
    payload: dict[str, object] = {
        "layer": LAYER, "status": status, "build_id": bid, "date": d_iso, "basis": basis,
        "asof": asof,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules_version": RULES_VERSION,
        "equity_root": str(Path(equity_root).resolve()),
        "stage_root": str(Path(stage_root).resolve()),
        "builds_from": None if builds_from is None else str(Path(builds_from).resolve()),
        "builds_from_date": None if pin is None else pin.date,
        "equity_builds": equity_builds, "stage_builds": stage_builds,
        "window": {"price_from": p.price_from, "flow_from": p.flow_from, "to": d_iso,
                   "credit_lag_sessions": p.credit_lag},
        "universe_rule": asdict(rule),
        "min_eligible": min_eligible,
        "coverage": coverage,
        "n_lapsed_dropped": coverage["n_lapsed_dropped"],
        "tables": tables,
        "gaps": list(queries.GAPS),
        "gates": [g.as_dict() for g in results],
        "elapsed_s": elapsed,
    }

    if failed:
        report = root / "_failed" / f"{bid}.json"
        _write_json(report, payload)
        shutil.rmtree(tmp_root, ignore_errors=True)
        # D-09: 같은 날 성공 기록(status ok)은 FAIL 재실행이 덮지 않는다(덮으면 엑셀 메타 시트의
        # fi 판 기록이 빈다). 기록이 없거나 ok 가 아니거나 내용이 깨졌으면(JSON·UTF-8) 덮는다.
        # 있는데 못 읽으면(권한 등) 덮지 않고 오류로 낸다 — 보고서는 이미 썼고 임시 판도 지웠다.
        try:
            prev = json.loads(run_manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            prev = None
        kept = isinstance(prev, dict) and prev.get("status") == "ok"
        if not kept:
            _write_json(run_manifest, payload)
        return BuildResult(status, bid, d_iso, basis, tables, results, run_manifest, report,
                           elapsed, coverage, run_manifest_kept=kept)

    gate_dicts = [g.as_dict() for g in results]
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    for name, info in tables.items():
        table_root = root / name
        table_root.mkdir(parents=True, exist_ok=True)
        final_dir = table_root / f"v={bid}"
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(tmp_root / name), str(final_dir))
        _write_json(final_dir / "_meta.json", {
            "table": name, "build_id": bid, "basis": basis, "date": d_iso,
            "partition": "whole", "n_rows": info["n_rows"],
            "content_hash": info["content_hash"], "inputs": info["inputs"],
            "rules_version": RULES_VERSION, "window": info["window"], "gates": gate_dicts})
        manifest.commit(table_root, manifest.BuildRecord(
            build_id=bid, snapshot_id="", rules_version=RULES_VERSION, basis=basis,
            built_at_utc=built_at, n_rows=int(str(info["n_rows"])),
            content_hash=str(info["content_hash"]),
            partitions=[{"path": f"v={bid}", "n_rows": info["n_rows"],
                         "content_hash": info["content_hash"]}],
            gates=gate_dicts, inputs=table_inputs[name]), keep=keep)
    shutil.rmtree(tmp_root, ignore_errors=True)
    _write_json(run_manifest, payload)
    _write_json(root / f"latest_{basis}.json", payload)
    return BuildResult(status, bid, d_iso, basis, tables, results, run_manifest, None, elapsed,
                       coverage)
