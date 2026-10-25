"""조용한 손실 검사 — 아침 확정판의 모델 폐포 표를 직전 거래일 아침 확정판과 대조한다(컷오버 트랙 K1-4a).

정본: `docs/plans/2026-10-10-cutover-track.md` §3 K1-4a · DECISIONS N-42 Q4('조용한 손실 검사 2주 기록 뒤 차단 전환') ·
로드맵 `docs/plans/2026-10-05-roadmap.md` K1-4('매 아침 직전 아침 확정판과 비교해, 설명 안 되는 값→NULL·행 삭제 0').
비교 엔진은 새로 만들지 않는다 — `scripts/equity_diff.py` 의 `diff_core`(두 판 전 컬럼 FULL OUTER JOIN, 한 번의 집계)를
그대로 부르고, 여기서는 대상·판·갈래만 정한다.

대상 = 모델 폐포. fi 가 직접 읽는 원천(`factor_inputs.queries.TABLE_SOURCES`)에서 출발해 equity 표 선언의 입력
(`RULES[t].inputs`)을 거꾸로 따라 닫은 집합이다. stage(`stg_*`)에서 멈춘다 — 그 아래 원장(raw)은 넣지 않는다.
목록을 여기 박지 않는다(`closure_tables`) — fi 원천이나 equity 입력이 늘면 대상도 따라 는다.

판 = 오늘 아침 인계 이력 `data/deliver/history/<D>_morning.json` 의 stage_builds·equity_builds(읽기는 `equity.handoff`
한 곳) 대 직전 거래일(판정 달력 `daily.calendar`) 아침 인계 이력의 판. 판이 MANIFEST 나 디스크에서 사라졌으면(GC —
stage keep 3 · equity keep 10) 그 표는 판정 불가다(P1 — 조용히 통과시키지 않는다).

비교 단위(grain) — equity 는 선언 grain. stage 는 키가 유일하면(`key_unique`) 자연키, 판본 표면 자연키 + observed_date
(G6 이 유일성을 보장하는 축), 판본 없는 로그(`versioned=False`)는 선언 열 전부 + observed_date(그 로그는 같은 키가
여러 번이라 payload 열까지 묶어야 행이 갈린다). grain 의 NULL 은 허용하고 중복만 판정 불가로 본다(조인이 NULL 안전 —
equity 격자의 not_collected 셀은 src 가 NULL 이 설계다). 같은 판 id 면 읽지 않고(같은 판),
표 content_hash·행 수가 같으면 읽지 않는다(같은 내용). equity 는 파티션 content_hash 가 같은 파티션을 양쪽에서
함께 빼고 바뀐 파티션만 조인한다 — 같은 해시 = 같은 행 집합이고 grain 이 표 전체에서 유일하므로 빼도 숫자가 같다.
stage 판 기록에는 파티션 해시가 없어 표 전체를 조인한다.

갈래(기록형 첫 판 — 보수적으로, 설명 못 하면 미설명):
  · 새 행(rows_added)·NULL→값 — 정상(수만 남긴다).
  · 재수집 창 — 날짜 열이 있는 표(date_axis 파티션의 날짜형 키 열)에서 D 와 그 앞 `KRX_RECHECK_SESSIONS`(10)세션
    (`daily.ledger_health` — 08:10 이 다시 받는 창) 안 행의 값 변경·값→NULL. 설명됨으로 보되 수는 남긴다.
  · 규칙 변경 — 두 판의 rules_version(equity `e*` · stage 판본)이 다른 표. 아래 미설명 후보 전부가 이 갈래로 간다.
  · 미설명 — 그 밖의 값→NULL(창 밖) · 행 삭제(창 안 포함) · available_date 계열 변경(창 안 포함 — 공개시점 축은 창으로
    설명하지 않는다). 창 밖 값 변경은 보고만 한다(equity_diff 게이트와 같은 세 축).
  날짜 열이 없는 표(마스터·접수 축)는 grain 으로 비교하고 재수집 창 없이 같은 규칙이다.

모드 — 설정 파일 한 줄 `config/silent_loss.env` 의 `SILENT_LOSS_BLOCK`(켜는 쪽만 정확한 값 `1`, 그 밖·파일 없음은
기록형). 저장소 값은 0(기록형)이고 그림자 시작 10-14 부터 2주 기록 뒤 켠다(N-42 Q4).
  기록형: 체인을 막지 않는다. 결과 파일 + 런 로그 + 미설명 > 0 또는 판정 불가면 notify warn 한 줄.
  차단형: 미설명 > 0 또는 판정 불가(N-42 Q4 '필수 검사 SKIP = 실패')면 crit 한 줄(`TITLE_BLOCK` — X-2 세는 목록) +
          `gate` 가 막는다. 다음 모델 단계 = 이 D 의 아침 확정판을 고정해 읽는 15:41 장 마감 체인 close(T-2)가
          빌드 락을 잡기 전에 `gate --date D'` 를 본다(`scripts/postclose_chain.sh`). 체인은 스위치를 셸에서 먼저 읽어
          (`scripts/postclose_conf.sh` silent_loss_block_on — 이 모듈 `block_enabled` 와 같은 규칙, 테스트가 대조) 꺼져
          있으면 관문을 부르지 않는다 — 결과 파일·관문 예외·import 실패가 기록형 기간의 장 마감 체인을 막지 못한다.

쓰는 것: `logs/silent_loss/<D>.json`(표별 갈래 수·미설명 표본 상위 N) · 런 로그 `data/raw/daily_run.db` source
`silent_loss`(상태 ok·unexplained·undecidable·blocked — 앞의 둘이 아니면 일일 리포트 crit) · `scripts/notify.sh`
(logs/notify.log 기록만 — 텔레그램 금지 10-01). 원장·판은 읽기만 한다.
rc(check): 0 미설명 0 · 1 미설명 > 0(기록형) · 2 판정 불가(기록형) · 3 차단(차단형). rc(gate): 0 통과 · 1 막음.

사용(서버, `$QL_HOME` 에서):
  PYTHONPATH=src .venv/bin/python -m daily.silent_loss check --date YYYYMMDD [--prev-date YYYYMMDD]
                                   [--dry-run] [--out PATH] [--threads 3] [--memory-limit 6GB] [--samples 5]
  PYTHONPATH=src .venv/bin/python -m daily.silent_loss gate --date YYYYMMDD
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from daily import calendar as cal_mod
from daily import calendar_refresh as cr
from daily import runlog

TOOL = "daily.silent_loss"
SCHEMA = 1                      # <D>.json 모양 판본 — 키를 바꾸면 올린다(gate 가 읽는다)
SOURCE = runlog.SILENT_LOSS
OUT_DIR = Path("logs/silent_loss")
HIST_DIR = Path("data/deliver/history")
CAL_DIR = Path("data/calendar")
RUN_DB = Path("data/raw/daily_run.db")
NOTIFY = Path("scripts/notify.sh")
CONF = Path("config/silent_loss.env")
SWITCH = "SILENT_LOSS_BLOCK"
STAGE, EQUITY = "stage", "equity"
ROOTS = {STAGE: Path("data/stage"), EQUITY: Path("data/equity")}
# notify 제목 — 차단 crit 은 X-2 `window_judge.COUNTED_CRIT_PREFIXES` 의 '조용한 손실 차단' 으로 센다(테스트가 대조한다)
TITLE_RECORD = "조용한 손실 기록"
TITLE_BLOCK = "조용한 손실 차단"
SAMPLE_N = 5                    # 표마다 미설명 표본 상위 N
THREADS, MEMORY_LIMIT = 3, "6GB"   # equity_diff CLI 기본과 같다(서버 4코어 규약)
OK, UNEXPLAINED, UNDECIDABLE, BLOCKED = "ok", "unexplained", "undecidable", "blocked"
RC = {OK: 0, UNEXPLAINED: 1, UNDECIDABLE: 2, BLOCKED: 3}
# 표 상태
SAME_BUILD, SAME_CONTENT, COMPARED, TABLE_UNDECIDABLE = "same_build", "same_content", "compared", "undecidable"
_YMD_RE = re.compile(r"^\d{8}$")
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _ed() -> ModuleType:
    """비교 엔진 정본 `scripts/equity_diff.py` — scripts/ 는 패키지가 아니라 경로를 뒤에 붙여 읽는다."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.append(str(_SCRIPTS))
    return importlib.import_module("equity_diff")


def _ymd(s: str) -> dt.date:
    if not _YMD_RE.match(s):
        raise ValueError(f"날짜는 YYYYMMDD 여야 한다: {s!r}")
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))


# ── 대상 · 설정 ─────────────────────────────────────────────────────────────
def closure_tables() -> list[tuple[str, str]]:
    """모델 폐포 [(층, 표)] — fi 직접 원천에서 equity 선언 입력을 따라 닫는다. stage 에서 멈춘다(원장 제외)."""
    from equity.inputs import STAGE_PREFIX
    from factor_inputs import queries

    rules = _ed().load_equity_rules()
    todo = sorted({s for srcs in queries.TABLE_SOURCES.values() for s in srcs})
    seen: set[str] = set()
    while todo:
        t = todo.pop()
        if t in seen:
            continue
        seen.add(t)
        if t.startswith(STAGE_PREFIX):
            continue
        if t not in rules:
            raise LookupError(f"fi 원천·equity 입력이 선언 없는 표를 가리킨다 — {t} (equity RULES 에 없음)")
        todo.extend(rules[t].inputs)
    return sorted((STAGE if t.startswith(STAGE_PREFIX) else EQUITY, t) for t in seen)


def block_enabled(home: Path) -> tuple[bool, str]:
    """차단 스위치 — `config/silent_loss.env` 의 `SILENT_LOSS_BLOCK=1` 일 때만 켜짐(마지막 대입이 이긴다). 그 밖은 기록형."""
    path = home / CONF
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, f"설정 {CONF} 없음 — 기록형"
    except OSError as e:
        return False, f"설정 {CONF} 읽기 오류({type(e).__name__}) — 기록형"
    value = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, val = line.partition("=")
        if sep and name.strip() == SWITCH:
            value = val.strip().strip("'\"")
    on = value == "1"
    return on, f"설정 {CONF} {SWITCH}={value or '(없음)'} — {'차단형' if on else '기록형'}"


def recheck_window(cal: cal_mod.Calendar, d: dt.date) -> tuple[dt.date, dt.date]:
    """08:10 재수집 창 — D 와 그 앞 `KRX_RECHECK_SESSIONS` 세션(daily_build.sh krx_step 과 같은 폭)."""
    from daily.ledger_health import KRX_RECHECK_SESSIONS

    return cal.prev_trading_day(d, n=KRX_RECHECK_SESSIONS), d


@dataclass(frozen=True)
class Spec:
    """표 하나의 비교 축 — grain · 재수집 창 날짜 열 · 선언 열 이름(하이브 키 판정용)."""

    key: tuple[str, ...]
    window_column: str | None
    rule_cols: frozenset[str]


def spec_of(layer: str, table: str) -> Spec:
    """선언에서 비교 축을 읽는다. 날짜 열은 date_axis 표의 날짜형 grain(키) 열 중 첫째다."""
    if layer == EQUITY:
        rules = _ed().load_equity_rules()
        if table not in rules:
            raise LookupError(f"equity 선언에 없는 표 — {table}")
        r = rules[table]
        win = (next((c for c in r.grain if str(r.columns.get(c, "")).upper() == "DATE"), None)
               if r.partition_class == "date_axis" else None)
        return Spec(tuple(r.grain), win, frozenset(r.columns))
    from stage.model import DATE_FORMATS
    from stage.rules import RULES as STAGE_RULES

    if table not in STAGE_RULES:
        raise LookupError(f"stage 선언에 없는 표 — {table}")
    s = STAGE_RULES[table]
    nk = tuple(s.natural_key)
    kinds = {c.name: c.kind for c in s.columns}
    names = frozenset({*kinds, *nk, *(e.name for e in s.extras)})
    if s.key_unique:
        key: tuple[str, ...] = nk
    elif s.versioned:
        key = (*nk, "observed_date")
    else:
        key = (*nk, *(c.name for c in s.columns if c.name not in nk), "observed_date")
    win = (next((k for k in nk if kinds.get(k) in DATE_FORMATS), None)
           if s.partition_class == "date_axis" else None)
    return Spec(key, win, names)


# ── 표 하나 ─────────────────────────────────────────────────────────────────
def _part_hashes(rec: dict) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for p in rec.get("partitions") or []:
        path = str(p.get("path", ""))
        h = p.get("content_hash")
        out[path.split("/", 1)[1] if "/" in path else "whole"] = str(h) if h else None
    return out


def _label(table_root: Path, build_id: str, f: Path) -> str:
    rel = f.relative_to(table_root / f"v={build_id}")
    return rel.parts[0] if len(rel.parts) > 1 else "whole"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _empty_counts() -> dict[str, int]:
    return {k: 0 for k in ("rows_added", "rows_removed", "value_to_null", "null_to_value",
                           "value_changed", "window", "rules_change", "unexplained")}


def compare_table(home: Path, layer: str, table: str, before: str | None, after: str | None, *,
                  window: tuple[dt.date, dt.date], threads: int = THREADS,
                  memory_limit: str = MEMORY_LIMIT, samples: int = SAMPLE_N) -> dict:
    """표 하나의 두 판을 대조해 갈래 수를 낸다. 판정할 수 없으면 status undecidable + 사유(예외를 올리지 않는다)."""
    t0 = time.monotonic()
    out: dict = {"table": table, "layer": layer, "before": before, "after": after, "status": None,
                 "reason": "", "rules_before": None, "rules_after": None, "rules_changed": False,
                 "key": [], "window_column": None, "partitions": {"compared": [], "skipped": []},
                 "counts": _empty_counts(),
                 "unexplained_by_kind": {"value_to_null": 0, "rows_removed": 0, "available_date": 0},
                 "samples": [], "elapsed_s": 0.0}

    def done(status: str, reason: str = "") -> dict:
        out["status"], out["reason"] = status, reason
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        return out

    if before is None or after is None:
        return done(TABLE_UNDECIDABLE, f"인계 이력에 판이 없다 — 직전 {before} · 오늘 {after}")
    if before == after:
        return done(SAME_BUILD)
    ed = _ed()
    troot = home / ROOTS[layer] / table
    try:
        mf = ed.load_manifest(troot)
    except ed.UsageError as e:
        return done(TABLE_UNDECIDABLE, str(e))
    recs = {str(b.get("build_id")): b for b in mf.get("builds") or []}
    for side, bid in (("직전", before), ("오늘", after)):
        if bid not in recs:
            return done(TABLE_UNDECIDABLE, f"{side} 판 {bid} 이 MANIFEST 에 없다(GC 됐거나 지어진 적 없음) — "
                                           f"{troot / 'MANIFEST.json'}")
        if not (troot / f"v={bid}").is_dir():
            return done(TABLE_UNDECIDABLE, f"{side} 판 {bid} 이 디스크에 없다(GC) — {troot / f'v={bid}'}")
    rb, ra = recs[before], recs[after]
    out["rules_before"], out["rules_after"] = rb.get("rules_version"), ra.get("rules_version")
    out["rules_changed"] = out["rules_before"] != out["rules_after"]
    if rb.get("content_hash") and rb.get("content_hash") == ra.get("content_hash") \
            and rb.get("n_rows") == ra.get("n_rows"):
        return done(SAME_CONTENT)
    try:
        spec = spec_of(layer, table)
    except LookupError as e:
        return done(TABLE_UNDECIDABLE, str(e))
    out["key"], out["window_column"] = list(spec.key), spec.window_column

    import duckdb

    try:
        b_all, a_all = ed.build_files(troot, before), ed.build_files(troot, after)
    except ed.UsageError as e:
        return done(TABLE_UNDECIDABLE, str(e))
    hb, ha = _part_hashes(rb), _part_hashes(ra)
    skip = sorted(lab for lab in hb.keys() & ha.keys() if hb[lab] and hb[lab] == ha[lab])
    b_files = [f for f in b_all if _label(troot, before, f) not in skip]
    a_files = [f for f in a_all if _label(troot, after, f) not in skip]
    if not b_files or not a_files:          # 한쪽이 비면 조인할 뷰가 없다 — 같은 파티션도 다시 넣는다(같은 결과)
        b_files, a_files, skip = b_all, a_all, []
    out["partitions"] = {
        "compared": sorted({_label(troot, before, f) for f in b_files}
                           | {_label(troot, after, f) for f in a_files}),
        "skipped": skip}
    lo, hi = window
    win = spec.window_column
    split = (f"TRY_CAST(coalesce({_q(win)}, {_q(ed.BEFORE_PREFIX + win)}) AS DATE) "
             f"BETWEEN DATE '{lo.isoformat()}' AND DATE '{hi.isoformat()}'") if win else None
    con = duckdb.connect()
    try:
        con.execute(f"SET threads = {int(threads)}")
        con.execute(f"SET memory_limit = '{memory_limit}'")
        try:
            # grain 의 NULL 은 막지 않는다 — 조인이 IS NOT DISTINCT FROM 이라 팬아웃은 중복 키만 만든다. equity 격자는
            # NULL 키가 설계다(flow_daily 의 not_collected 셀 src NULL — 로컬 10-03 판 25,685행)
            core = ed.diff_core(con, b_files, a_files, spec.key, table_root=troot,
                                rule_cols=set(spec.rule_cols), split=split, nullable=frozenset(spec.key))
        except (ed.UsageError, duckdb.Error) as e:
            return done(TABLE_UNDECIDABLE, f"비교 실패 — {type(e).__name__}: {e}")
        if core.counters is None:
            issues = {s: {k: v[k] for k in ("duplicate_keys", "null_keys")}
                      for s, v in core.key_issues.items()}
            return done(TABLE_UNDECIDABLE, f"grain {list(spec.key)} 에 중복 키가 있다 — {issues}")
        _classify(out, ed, con, core, split, samples=samples)
    finally:
        con.close()
    return done(COMPARED)


def _classify(out: dict, ed: ModuleType, con: Any, core: Any, split: str | None, *,
              samples: int) -> None:
    """diff_core 카운터(`equity_diff.DiffCore`)를 갈래로 나누고 미설명 표본을 뽑는다(조인 뷰 j 가 살아 있는 연결 위에서)."""
    counters = core.counters
    compared: list[str] = core.compared
    a_types: dict[str, str] = core.a_types
    key: tuple[str, ...] = core.key
    avail = [c for c in compared if ed.group_of(c) == "available_date"]
    plain = [c for c in compared if c not in avail]

    def tot(c: str, k: str) -> int:
        return int(counters[(c, k)]["total"])

    def ins(c: str, k: str) -> int:
        return int(counters[(c, k)].get("in_split", 0))

    added = tot(ed.ROW_COLUMN, "rows_added")
    removed = tot(ed.ROW_COLUMN, "rows_removed")
    v2n_out = {c: tot(c, "value_to_null") - ins(c, "value_to_null") for c in plain}
    avail_n = {(c, k): tot(c, k) for c in avail for k in ed.KINDS_COLUMN}
    by_kind = {"value_to_null": sum(v2n_out.values()), "rows_removed": removed,
               "available_date": sum(avail_n.values())}
    candidate = sum(by_kind.values())
    c = out["counts"]
    c.update(rows_added=added, rows_removed=removed,
             value_to_null=sum(tot(x, "value_to_null") for x in compared),
             null_to_value=sum(tot(x, "null_to_value") for x in compared),
             value_changed=sum(tot(x, "value_changed") for x in compared),
             window=sum(ins(x, "value_changed") + ins(x, "value_to_null") for x in plain))
    if out["rules_changed"]:
        c["rules_change"] = candidate
        return
    c["unexplained"] = candidate
    out["unexplained_by_kind"] = by_kind
    if not candidate or samples <= 0:
        return
    # 표본 — 미설명이 큰 카운터부터. 창 밖 값→NULL 은 창 술어를 뺀 행만 보여 준다
    picks: list[tuple[int, str, str, str]] = []
    for col, n in v2n_out.items():
        if n:
            cond = ed.change_cond(col, "value_to_null", numeric=ed.is_numeric(a_types.get(col, "VARCHAR")),
                                  tol=ed.DEFAULT_TOL)
            picks.append((n, col, "value_to_null",
                          f"({cond}) AND NOT coalesce({split}, FALSE)" if split else cond))
    if removed:
        picks.append((removed, ed.ROW_COLUMN, "rows_removed",
                      ed.change_cond(ed.ROW_COLUMN, "rows_removed", numeric=False, tol=0)))
    for (col, k), n in avail_n.items():
        if n:
            picks.append((n, col, k, ed.change_cond(col, k, numeric=ed.is_numeric(a_types.get(col, "VARCHAR")),
                                                    tol=ed.DEFAULT_TOL)))
    left = samples
    for _n, col, k, pred in sorted(picks, key=lambda p: -p[0]):
        if left <= 0:
            break
        for ex in ed.examples(con, key, col, k, pred, left):
            label = k if col == ed.ROW_COLUMN or col not in avail else f"available_date:{k}"
            out["samples"].append({"kind": label, "column": None if col == ed.ROW_COLUMN else col, **ex})
            left -= 1


# ── 하루 ────────────────────────────────────────────────────────────────────
@dataclass
class Result:
    date: str
    mode: str                                   # record · block
    conf_note: str
    prev: str | None = None
    window: tuple[dt.date, dt.date] | None = None
    handoffs: dict[str, object] = field(default_factory=dict)
    closure: list[tuple[str, str]] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    error: str | None = None
    verdict: str = OK
    rc: int = 0
    totals: dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0
    notified: bool | None = None

    def finalize(self) -> None:
        keys = tuple(_empty_counts())
        self.totals = {k: sum(int(t["counts"][k]) for t in self.tables) for k in keys}
        self.totals["undecidable"] = sum(t["status"] == TABLE_UNDECIDABLE for t in self.tables)
        self.totals["tables"] = len(self.tables)
        bad = self.totals["unexplained"] > 0
        unsure = self.totals["undecidable"] > 0 or self.error is not None
        if self.mode == "block" and (bad or unsure):
            self.verdict = BLOCKED
        elif bad:
            self.verdict = UNEXPLAINED
        elif unsure:
            self.verdict = UNDECIDABLE
        else:
            self.verdict = OK
        self.rc = RC[self.verdict]

    def headline(self) -> str:
        if self.error is not None:
            return f"판정 불가 — {self.error[:160]}"
        return (f"미설명 {self.totals['unexplained']:,} · 판정 불가 표 {self.totals['undecidable']} · "
                f"재수집 창 {self.totals['window']:,} · 규칙 변경 {self.totals['rules_change']:,}")

    def to_dict(self) -> dict:
        lo_hi = None if self.window is None else {"lo": self.window[0].isoformat(),
                                                  "hi": self.window[1].isoformat()}
        return {"schema": SCHEMA, "tool": TOOL,
                "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date": self.date, "prev_date": self.prev, "mode": self.mode, "conf": self.conf_note,
                "verdict": self.verdict, "rc": self.rc, "blocked": self.verdict == BLOCKED,
                "window": lo_hi, "handoffs": self.handoffs, "error": self.error,
                "closure": {"n": len(self.closure), "tables": [f"{la}:{t}" for la, t in self.closure]},
                "totals": self.totals, "elapsed_s": self.elapsed_s, "tables": self.tables}


def check(home: Path, d: str, *, prev: str | None = None, tables: Sequence[tuple[str, str]] | None = None,
          block: bool | None = None, threads: int = THREADS, memory_limit: str = MEMORY_LIMIT,
          samples: int = SAMPLE_N) -> Result:
    """D 아침 확정판 대 직전 거래일 아침 확정판. 입력을 못 읽으면 `error`(판정 불가)로 돌려준다 — 예외를 올리지 않는다."""
    from equity import handoff

    t0 = time.monotonic()
    on, note = block_enabled(home) if block is None else (block, "인자")
    res = Result(date=d, mode="block" if on else "record", conf_note=note)
    try:
        dd = _ymd(d)
        cal = cal_mod.load(home / CAL_DIR)
        res.prev = prev or cal.prev_trading_day(dd).strftime("%Y%m%d")
        _ymd(res.prev)
        res.window = recheck_window(cal, dd)
        today = handoff.load(home / HIST_DIR / f"{d}_morning.json")
        before = handoff.load(home / HIST_DIR / f"{res.prev}_morning.json")
        res.handoffs = {"today": str(today.path), "today_health": today.health,
                        "prev": str(before.path), "prev_health": before.health}
        if today.date != d or not today.health_ok:
            raise handoff.HandoffError(f"오늘 인계 이력 {today.path} 이 쓸 수 있는 판이 아니다 — "
                                       f"date={today.date} health={today.health}")
        res.closure = list(tables) if tables is not None else closure_tables()
        for layer, table in res.closure:
            b_builds, a_builds = ((before.equity_builds, today.equity_builds) if layer == EQUITY
                                  else (before.stage_builds, today.stage_builds))
            try:
                res.tables.append(compare_table(
                    home, layer, table, b_builds.get(table), a_builds.get(table),
                    window=res.window, threads=threads, memory_limit=memory_limit, samples=samples))
            except Exception as e:  # noqa: BLE001  # reason: 표 하나의 예상 밖 예외가 나머지 표 판정을 막지 않게(그 표는 판정 불가)
                traceback.print_exc()
                res.tables.append({**compare_table(home, layer, table, None, None, window=res.window),
                                   "reason": f"예상 밖 예외 {type(e).__name__}: {e}"})
    except (handoff.HandoffError, cal_mod.CalendarUnavailable, KeyError, ValueError, LookupError,
            OSError) as e:
        res.error = f"{type(e).__name__}: {e}"
    res.elapsed_s = round(time.monotonic() - t0, 1)
    res.finalize()
    return res


def gate(home: Path, d: str) -> tuple[int, str]:
    """다음 모델 단계의 관문 — 차단형일 때만 `logs/silent_loss/<d>.json` 으로 막는다. (rc, 사유). 0 통과 · 1 막음."""
    on, note = block_enabled(home)
    if not on:
        return 0, f"조용한 손실 차단 스위치 off — {note}"
    path = home / OUT_DIR / f"{d}.json"
    try:
        rep = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 1, f"조용한 손실 검사 결과 {path} 이 없다 — D={d} 08:10 체인의 검사가 돌지 않았다(차단형, P1)"
    except (OSError, ValueError) as e:
        return 1, f"조용한 손실 검사 결과 {path} 을 읽지 못했다({type(e).__name__}) — 차단형, P1"
    tot = rep.get("totals") if isinstance(rep, dict) else None
    if not isinstance(rep, dict) or rep.get("tool") != TOOL or rep.get("schema") != SCHEMA \
            or rep.get("date") != d or not isinstance(tot, dict):
        return 1, f"조용한 손실 검사 결과 {path} 의 모양·날짜가 다르다 — 차단형, P1"
    u, n, err = tot.get("unexplained"), tot.get("undecidable"), rep.get("error")
    if not isinstance(u, int) or not isinstance(n, int) or u > 0 or n > 0 or err:
        return 1, (f"조용한 손실 D={d}: 미설명 {u} · 판정 불가 표 {n}{' · ' + str(err)[:120] if err else ''} "
                   f"— {path}")
    return 0, f"조용한 손실 D={d}: 미설명 0 · 판정 불가 0 — 통과({path})"


# ── 출력 · 알림 ─────────────────────────────────────────────────────────────
def _table_line(t: dict) -> str:
    c, k = t["counts"], t["unexplained_by_kind"]
    if t["status"] == TABLE_UNDECIDABLE:
        return f"{t['table']} 판정 불가({t['reason'][:120]})"
    return (f"{t['table']} 미설명 {c['unexplained']:,}(값→NULL {k['value_to_null']:,} · 행 삭제 "
            f"{k['rows_removed']:,} · 공개일 {k['available_date']:,})")


def render(res: Result, out: Path | None) -> str:
    lines = [(f"조용한 손실 D={res.date} 대 {res.prev or '?'} — {res.mode} · {res.conf_note} · verdict {res.verdict} "
              f"rc {res.rc} · {res.elapsed_s}초")]
    if res.window is not None:
        lines.append(f"  재수집 창 {res.window[0]} ~ {res.window[1]} · 대상 {len(res.closure)}표")
    lines.append(f"  {res.headline()}")
    t = res.totals
    if t:
        lines.append(f"  새 행 {t['rows_added']:,} · NULL→값 {t['null_to_value']:,} · 값 변경 {t['value_changed']:,} "
                     f"· 행 삭제 {t['rows_removed']:,} · 값→NULL {t['value_to_null']:,}")
    for row in res.tables:
        status, c = row["status"], row["counts"]
        if status == COMPARED:
            lines.append(f"  - {row['layer']}:{row['table']} {row['before']} → {row['after']} "
                         f"조인 {','.join(row['partitions']['compared'])}"
                         f"{' (건너뜀 ' + str(len(row['partitions']['skipped'])) + ')' if row['partitions']['skipped'] else ''}"
                         f" · 새 행 {c['rows_added']:,} · 창 {c['window']:,} · 규칙 {c['rules_change']:,} "
                         f"· 미설명 {c['unexplained']:,} · {row['elapsed_s']}초")
        elif status == TABLE_UNDECIDABLE:
            lines.append(f"  - {row['layer']}:{row['table']} 판정 불가 — {row['reason']}")
    same = sum(row["status"] in (SAME_BUILD, SAME_CONTENT) for row in res.tables)
    if same:
        lines.append(f"  (같은 판·같은 내용 {same}표는 읽지 않았다)")
    lines.append(f"→ {out}" if out else "→ (결과 파일 없음 — dry-run)")
    return "\n".join(lines)


def _notify(home: Path, level: str, title: str, body: str) -> bool:
    """`scripts/notify.sh`(logs/notify.log 기록만 — 텔레그램 금지 10-01). 실패하면 False."""
    try:
        proc = subprocess.run([str(home / NOTIFY), level, title, body], check=False,
                              capture_output=True, text=True)
    except OSError as e:
        print(f"{TOOL} notify 실행 실패 — {home / NOTIFY} ({type(e).__name__}: {e})", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"{TOOL} notify 실패 rc={proc.returncode} {proc.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


def _alert(home: Path, res: Result, out: Path | None) -> bool | None:
    if res.verdict == OK:
        return None
    worst = sorted((t for t in res.tables if t["counts"]["unexplained"] or t["status"] == TABLE_UNDECIDABLE),
                   key=lambda t: (-t["counts"]["unexplained"], t["table"]))[:6]
    body = " · ".join(_table_line(t) for t in worst) or (res.error or "")
    body += f" | 결과 {out or '(dry-run)'} · {res.conf_note}"
    if res.verdict == BLOCKED:
        body += " | 다음 모델 단계(15:41 장 마감 체인)는 이 D 의 판을 쓰지 않는다(gate)"
        return _notify(home, "crit", f"{TITLE_BLOCK} D={res.date}: {res.headline()}", body)
    return _notify(home, "warn", f"{TITLE_RECORD} D={res.date}: {res.headline()}", body)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="조용한 손실 검사(K1-4a) — 아침 확정판 대 직전 아침 확정판")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="D 의 모델 폐포 표를 직전 거래일 아침 확정판과 대조한다")
    c.add_argument("--date", required=True, help="확정판 거래일 D(YYYYMMDD)")
    c.add_argument("--prev-date", default=None, help="비교할 직전 판의 날짜(기본: 판정 달력의 직전 거래일)")
    c.add_argument("--dry-run", action="store_true", help="런 로그·알림·기본 결과 파일 없이 요약만(--out 은 쓴다)")
    c.add_argument("--out", default=None, help="결과 JSON 경로(기본 logs/silent_loss/<D>.json)")
    c.add_argument("--threads", type=int, default=THREADS)
    c.add_argument("--memory-limit", default=MEMORY_LIMIT)
    c.add_argument("--samples", type=int, default=SAMPLE_N, help="표마다 미설명 표본 수")
    g = sub.add_parser("gate", help="차단형일 때 D 결과로 다음 모델 단계를 막는다(rc 1)")
    g.add_argument("--date", required=True)
    for p in (c, g):
        p.add_argument("--home", default=None, help="quant-ledger 루트(기본 $QL_HOME)")
    a = ap.parse_args(argv)
    home = Path(a.home or os.environ.get("QL_HOME") or Path(__file__).resolve().parents[2])
    if a.cmd == "gate":
        try:
            rc, why = gate(home, a.date)
        except Exception as e:  # noqa: BLE001  # reason: 관문은 차단형에서만 불린다 — 예상 밖 예외도 막음(rc 1, P1)
            traceback.print_exc()
            rc, why = 1, f"조용한 손실 관문 예외 {type(e).__name__}: {e} — 차단형, P1"
        print(why)
        return rc
    rid: int | None = None
    if not a.dry_run:
        try:
            rid = runlog.start(home / RUN_DB, date=a.date, source=SOURCE)
        except Exception as e:  # noqa: BLE001  # reason: 런 로그를 못 남겨도 검사는 한다(사유는 stderr)
            print(f"{TOOL} 런 로그 시작 기록 실패 — {type(e).__name__}: {e}", file=sys.stderr)
    try:
        res = check(home, a.date, prev=a.prev_date, threads=a.threads, memory_limit=a.memory_limit,
                    samples=a.samples)
    except Exception as e:  # noqa: BLE001  # reason: 예상 밖 예외도 판정 불가로 남긴다 — 통과(rc 0)로 읽히지 않게
        traceback.print_exc()
        on, note = block_enabled(home)
        res = Result(date=a.date, mode="block" if on else "record", conf_note=note,
                     error=f"예상 밖 예외 {type(e).__name__}: {e}")
        res.finalize()
    out = Path(a.out) if a.out else (None if a.dry_run else home / OUT_DIR / f"{a.date}.json")
    if out is not None:
        try:
            cr._atomic_write_json(out, res.to_dict())
        except OSError as e:
            print(f"{TOOL} 결과 파일 쓰기 실패: {out} {type(e).__name__}: {e}", file=sys.stderr)
            if res.mode == "block" and res.verdict != BLOCKED:
                res.verdict, res.rc = BLOCKED, RC[BLOCKED]     # 차단형은 결과를 못 남기면 gate 가 막는다(P1)
    if not a.dry_run:
        res.notified = _alert(home, res, out)
        if rid is not None:
            try:
                runlog.finish(home / RUN_DB, rid, status=res.verdict, n_rows=res.totals.get("unexplained"),
                              detail=f"{res.mode} · {res.headline()}"[:500])
            except Exception as e:  # noqa: BLE001  # reason: 기록 실패가 판정을 바꾸지 않는다(사유는 stderr)
                print(f"{TOOL} 런 로그 종료 기록 실패 — {type(e).__name__}: {e}", file=sys.stderr)
    print(render(res, out))
    return res.rc


if __name__ == "__main__":
    sys.exit(main())
