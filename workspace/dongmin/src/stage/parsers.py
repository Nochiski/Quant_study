"""blob 언네스트 파서 (§1 예외 c·e) — duckdb 로 못 하는 변환만 파이썬으로 한다.

ws_raw 본문 = zlib(`789C`) → 외부 JSON {chart1: <JSON 문자열>, chart2: <JSON 문자열>} → 내부 JSON.
양쪽 loads 모두 parse_float=Decimal (float 경유 금지). 출력은 전부 VARCHAR|None 이라 stage 캐스팅
규칙이 원장 컬럼과 똑같이 적용된다. 파서는 파이썬 루프지만 blob 이 1.3만 개라 수 초다 (§10 벤치).
"""
from __future__ import annotations

import html
import json
import re
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class RawBlob:
    cmp_cd: str
    ep: str
    pkey: str            # 대상 연월 (target_period, 예 '202612')
    fetched_date: str    # 'YYYY-MM-DD' (수집일 KST)
    body: bytes
    fetched_at: str      # UTC 무표기 시각 → observed_date 원천


@dataclass
class ParseResult:
    rows: list[dict[str, str | None]]
    columns: tuple[str, ...]
    metrics: dict[str, object] = field(default_factory=dict)


CONSENSUS_COLUMNS: tuple[str, ...] = (
    "cmp_cd", "fetched_date", "pkey", "metric", "obs_label", "unit", "consensus", "consensus_min",
    "consensus_max", "close_price_krw", "target_price_krw", "in_5001", "in_5002", "fetched_at",
)
METRIC_BY_NAME = {"EPS": "eps", "매출액": "revenue"}
METRIC_UNKNOWN = "parse_failed"


def decode_ws_body(body: bytes | bytearray | memoryview) -> dict[str, dict[str, object]]:
    """zlib → 외부 JSON → chart 별 내부 JSON. 실패는 예외로 올린다(호출자가 parse_failed 계상)."""
    raw = zlib.decompress(bytes(body))
    outer = json.loads(raw.decode("utf-8"))
    if not isinstance(outer, dict):
        raise ValueError(f"ws body outer JSON is not an object: type={type(outer).__name__}")
    charts: dict[str, dict[str, object]] = {}
    for k, v in outer.items():
        inner = json.loads(v, parse_float=Decimal) if isinstance(v, str) else v
        if not isinstance(inner, dict):
            raise ValueError(f"ws body chart {k} is not an object: type={type(inner).__name__}")
        charts[str(k)] = inner
    return charts


def _s(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, float):        # parse_float=Decimal 이라 오지 않지만 방어
        return str(Decimal(str(v)))
    return str(v)


def _list(chart: dict[str, object], key: str) -> list[object]:
    v = chart.get(key)
    return list(v) if isinstance(v, list) else []


def _same(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return Decimal(a) == Decimal(b)


def parse_consensus_monthly(blobs: Iterable[RawBlob]) -> ParseResult:
    """cF5001(select_item·close_price·target_price) + cF5002(avg·min_max) 를 좌표
    (cmp_cd, fetched_date, pkey, metric, obs_label) 로 outer join — 날짜 라벨 기준, 인덱스 zip 금지.

    - 같은 blob 안 라벨 중복은 값이 같으면 접고(카운터), 다르면 value_mismatch 로 계상한다.
    - 5001 과 5002 가 같은 좌표에서 다른 값이면 value_mismatch(5001 값 유지).
    - 무커버 프로브(전부 None)도 행을 만든다 — close_price 실값 보존. 빈 categories 는 0행.
    """
    n_blobs: Counter[str] = Counter()
    n_cells: Counter[str] = Counter()
    n_parse_failed = n_metric_unknown = n_dup = n_mismatch = 0
    coords: dict[tuple[str, str, str, str, str], dict[str, str | None]] = {}
    for b in blobs:
        n_blobs[b.ep] += 1
        try:
            charts = decode_ws_body(b.body)
        except Exception:  # noqa: BLE001  # reason: blob 손상은 도메인 실패 → parse_failed 계상
            n_parse_failed += 1
            continue
        is_5001 = b.ep == "cF5001"
        for chart in charts.values():
            name = chart.get("select_item_name" if is_5001 else "item_name")
            unit = _s(chart.get("select_item_unit" if is_5001 else "item_unit"))
            metric = METRIC_BY_NAME.get(str(name))
            if metric is None:
                n_metric_unknown += 1
                metric = METRIC_UNKNOWN
            cats = _list(chart, "categories")
            vals = _list(chart, "select_item" if is_5001 else "avg")
            close = _list(chart, "close_price")
            tgt = _list(chart, "target_price")
            mm = _list(chart, "min_max")
            seen: dict[str, str | None] = {}
            for i, label in enumerate(cats):
                lab = str(label)
                val = _s(vals[i]) if i < len(vals) else None
                if lab in seen:
                    n_dup += 1
                    if not _same(seen[lab], val):
                        n_mismatch += 1
                    continue
                seen[lab] = val
                n_cells[b.ep] += 1
                key = (b.cmp_cd, b.fetched_date, b.pkey, metric, lab)
                row: dict[str, str | None] | None = coords.get(key)
                if row is None:
                    row = dict.fromkeys(CONSENSUS_COLUMNS)
                    row.update(cmp_cd=b.cmp_cd, fetched_date=b.fetched_date, pkey=b.pkey,
                               metric=metric, obs_label=lab, unit=unit, in_5001="false",
                               in_5002="false", fetched_at=b.fetched_at)
                    coords[key] = row
                elif b.fetched_at < str(row["fetched_at"]):
                    row["fetched_at"] = b.fetched_at
                if is_5001:
                    if row["in_5002"] == "true" and not _same(row["consensus"], val):
                        n_mismatch += 1
                    row["consensus"] = val                     # 5001 값이 정본
                    row["close_price_krw"] = _s(close[i]) if i < len(close) else None
                    row["target_price_krw"] = _s(tgt[i]) if i < len(tgt) else None
                    row["in_5001"] = "true"
                else:
                    if row["in_5001"] == "true":
                        if not _same(row["consensus"], val):
                            n_mismatch += 1
                    else:
                        row["consensus"] = val
                    pair_obj = mm[i] if i < len(mm) else None
                    pair: list[object] = list(pair_obj) if isinstance(pair_obj, list) else []
                    row["consensus_min"] = _s(pair[0]) if len(pair) > 0 else None
                    row["consensus_max"] = _s(pair[1]) if len(pair) > 1 else None
                    row["in_5002"] = "true"
    rows = [coords[k] for k in sorted(coords)]
    metrics: dict[str, object] = {
        "n_blobs": dict(n_blobs), "n_cells": dict(n_cells), "n_rows_emitted": len(rows),
        "n_parse_failed": n_parse_failed, "n_metric_unknown": n_metric_unknown,
        "n_dup_labels_folded": n_dup, "n_value_mismatch": n_mismatch,
    }
    return ParseResult(rows=rows, columns=CONSENSUS_COLUMNS, metrics=metrics)


PARSERS: dict[str, Callable[[Iterable[RawBlob]], ParseResult]] = {
    "parse_consensus_monthly": parse_consensus_monthly,
}


# ── c1050001_data · cF3002/cF4002 · c1010001 (2026-09-02 서버 실측 구조, DESIGN §10 S4) ─────────
def _decode_json(body: bytes | bytearray | memoryview) -> object:
    """zlib(선택) → JSON 한 겹. c1050001_data·cF3002/4002 는 이중 인코딩이 아니다(실측)."""
    raw = bytes(body)
    if raw[:2] == b"\x78\x9c":
        raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8"), parse_float=Decimal)


def _decode_text(body: bytes | bytearray | memoryview) -> str:
    raw = bytes(body)
    if raw[:2] == b"\x78\x9c":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _json_rows(body: bytes, key: str) -> tuple[dict[str, object] | None, list[object] | None]:
    """(최상위 dict, 행 리스트). 구조가 아니면 (None, None) — 호출자가 parse_failed 계상."""
    d = _decode_json(body)
    if not isinstance(d, dict) or key not in d:
        return None, None
    rows = d[key]
    if rows is None:                 # 키는 있고 값이 null — 데이터 없음(082640 실측) → 빈 blob
        return d, []
    if not isinstance(rows, list):
        return None, None
    return d, list(rows)


PERIODIC_COLUMNS: tuple[str, ...] = (
    "cmp_cd", "fetched_date", "period_label", "period", "period_kind", "fs_basis", "revenue", "yoy",
    "op", "ni", "eps", "bps", "per", "pbr", "roe", "ev_ebitda", "tot_row", "fetched_at",
)
_PERIOD_RE = re.compile(r"^(\d{4})\.(\d{2})\((A|E)\)$")     # '2022.12(A)' · '2026.12(E)' 만 실측
_PERIODIC_MAP = {"SALES": "revenue", "YOY": "yoy", "OP": "op", "NP": "ni", "EPS": "eps",
                 "BPS": "bps", "PER": "per", "PBR": "pbr", "ROE": "roe", "EV": "ev_ebitda",
                 "MAIN": "fs_basis", "TOT_ROW": "tot_row"}


def _parse_periodic(blobs: Iterable[RawBlob], pkey: str) -> ParseResult:
    """c1050001_data pkey=T2Y(연간)/T2Q(분기): JsonData 7행 → (cmp_cd, fetched_date, YYMM 라벨) 행.

    같은 ep 의 다른 pkey('', 반대편 T2*, T4:*)는 건너뛰고 세기만 한다(빌더는 ep 단위로 넘김).
    빈 JsonData(실측 2 blob)는 0행 + n_empty. 라벨 모양이 다르면 period/kind NULL + 카운터.
    """
    n_blobs = n_skipped = n_empty = n_failed = n_dup = n_mismatch = n_unparsed = 0
    coords: dict[tuple[str, str, str], dict[str, str | None]] = {}
    for b in blobs:
        if b.pkey != pkey:
            n_skipped += 1
            continue
        n_blobs += 1
        try:
            _, rows = _json_rows(b.body, "JsonData")
        except Exception:  # noqa: BLE001  # reason: blob 손상은 도메인 실패 → parse_failed 계상
            rows = None
        if rows is None:
            n_failed += 1
            continue
        if not rows:
            n_empty += 1
            continue
        for r in rows:
            if not isinstance(r, dict):
                n_failed += 1
                continue
            label = _s(r.get("YYMM")) or ""
            m = _PERIOD_RE.match(label)
            if m is None:
                n_unparsed += 1
            row: dict[str, str | None] = dict.fromkeys(PERIODIC_COLUMNS)
            row.update(cmp_cd=b.cmp_cd, fetched_date=b.fetched_date, period_label=label or None,
                       period=(m.group(1) + m.group(2)) if m else None,
                       period_kind=m.group(3) if m else None, fetched_at=b.fetched_at)
            for src, dst in _PERIODIC_MAP.items():
                row[dst] = _s(r.get(src))
            key = (b.cmp_cd, b.fetched_date, label)
            prev = coords.get(key)
            if prev is not None:
                n_dup += 1
                if any(prev[c] != row[c] for c in PERIODIC_COLUMNS if c != "fetched_at"):
                    n_mismatch += 1
                continue
            coords[key] = row
    out = [coords[k] for k in sorted(coords)]
    return ParseResult(rows=out, columns=PERIODIC_COLUMNS, metrics={
        "pkey": pkey, "n_blobs": n_blobs, "n_skipped_pkey": n_skipped, "n_empty": n_empty,
        "n_rows_emitted": len(out), "n_parse_failed": n_failed, "n_dup_labels_folded": n_dup,
        "n_value_mismatch": n_mismatch, "n_label_unparsed": n_unparsed})


def parse_consensus_annual(blobs: Iterable[RawBlob]) -> ParseResult:
    return _parse_periodic(blobs, "T2Y")


def parse_consensus_quarterly(blobs: Iterable[RawBlob]) -> ParseResult:
    return _parse_periodic(blobs, "T2Q")


MATRIX_COLUMNS: tuple[str, ...] = (
    "cmp_cd", "fetched_date", "target_period", "target_label", "seq", "acc_cd", "acc_nm",
    "base_date", "lookback_idx", "lookback", "value", "fetched_at",
)
# VAL1~5 의 의미 — 실측 2026-09-02: v3_consensus_revision_compare 의 1w/1m/3m/1y 와 VAL2~5 가
# 각 667·674·699·709건 대각 일치(1,844 쌍), VAL1 은 현재값. 원문 인덱스는 lookback_idx 에 보존.
LOOKBACK_LABELS: dict[int, str] = {1: "current", 2: "1w", 3: "1m", 4: "3m", 5: "1y"}
_T4_PREFIX = "T4:"


def parse_consensus_matrix(blobs: Iterable[RawBlob]) -> ParseResult:
    """c1050001_data pkey='T4:YYYYMM': JsonData 9계정 × VAL1~5 → 45행/blob. 빈 JsonData 는 0행."""
    n_blobs = n_skipped = n_empty = n_failed = n_dup = n_mismatch = n_extra = 0
    coords: dict[tuple[str, str, str, str, str], dict[str, str | None]] = {}
    for b in blobs:
        if not b.pkey.startswith(_T4_PREFIX):
            n_skipped += 1
            continue
        n_blobs += 1
        target_period = b.pkey[len(_T4_PREFIX):]
        try:
            top, rows = _json_rows(b.body, "JsonData")
        except Exception:  # noqa: BLE001  # reason: blob 손상은 도메인 실패 → parse_failed 계상
            top, rows = None, None
        if rows is None or top is None:
            n_failed += 1
            continue
        if not rows:
            n_empty += 1
            continue
        target_label = _s(top.get("YYMM"))
        for r in rows:
            if not isinstance(r, dict):
                n_failed += 1
                continue
            n_extra += sum(1 for k in r if k.startswith("VAL") and k[3:].isdigit()
                           and int(k[3:]) not in LOOKBACK_LABELS)
            acc = _s(r.get("ACC_CD")) or ""
            for i, lab in LOOKBACK_LABELS.items():
                row: dict[str, str | None] = dict.fromkeys(MATRIX_COLUMNS)
                row.update(cmp_cd=b.cmp_cd, fetched_date=b.fetched_date,
                           target_period=target_period, target_label=target_label,
                           seq=_s(r.get("SEQ")), acc_cd=acc or None, acc_nm=_s(r.get("ACC_NM")),
                           base_date=_s(r.get("DT")), lookback_idx=str(i), lookback=lab,
                           value=_s(r.get(f"VAL{i}")), fetched_at=b.fetched_at)
                key = (b.cmp_cd, b.fetched_date, target_period, acc, str(i))
                prev = coords.get(key)
                if prev is not None:
                    n_dup += 1
                    if prev["value"] != row["value"]:
                        n_mismatch += 1
                    continue
                coords[key] = row
    out = [coords[k] for k in sorted(coords)]
    return ParseResult(rows=out, columns=MATRIX_COLUMNS, metrics={
        "n_blobs": n_blobs, "n_skipped_pkey": n_skipped, "n_empty": n_empty,
        "n_rows_emitted": len(out), "n_parse_failed": n_failed, "n_dup_labels_folded": n_dup,
        "n_value_mismatch": n_mismatch, "n_extra_val_slots": n_extra})


_FIN_SLOTS = ("DATA1", "DATA2", "DATA3", "DATA4", "DATA5", "DATA6")
_FIN_QSLOTS = ("DATAQ1", "DATAQ2", "DATAQ4", "DATAQ5", "DATAQ6")      # 실측 키 집합 — Q3 부재
_FIN_SCALARS = {"YYOY": "yyoy", "YEYOY": "yeyoy", "QOQ": "qoq", "YOY": "yoy", "QOQ_E": "qoq_e",
                "YOY_E": "yoy_e", "QOQ_COMMENT": "qoq_comment", "YOY_COMMENT": "yoy_comment",
                "QOQ_E_COMMENT": "qoq_e_comment", "YOY_E_COMMENT": "yoy_e_comment",
                "POINT_CNT": "point_cnt"}
_FIN_ATTRS = {"ACKIND": "ackind", "ACCODE": "accode", "ACC_NM": "acc_nm", "LVL": "lvl",
              "GRP_TYP": "grp_typ", "UNT_TYP": "unt_typ", "P_ACCODE": "p_accode"}
FIN_WISE_COLUMNS: tuple[str, ...] = (
    ("cmp_cd", "fetched_date", "ep", "seq") + tuple(_FIN_ATTRS.values())
    + tuple(f"period_label_{i}" for i in range(1, 7)) + tuple(f"val_{i}" for i in range(1, 7))
    + tuple("val_q" + s[5:] for s in _FIN_QSLOTS) + tuple(_FIN_SCALARS.values())
    + ("fs_basis", "freq", "fetched_at"))
_FIN_LABEL_N = 8          # YYMM 라벨 8 = 기간 6 + '전년대비(YoY)' 2 (실측 1,592/1,614)


def parse_fin_wise(blobs: Iterable[RawBlob]) -> ParseResult:
    """cF3002(재무제표 244계정)·cF4002(재무비율 36) pkey='Y': DATA 배열 원소 1개 = 출력 1행(wide).

    기간 라벨(YYMM[0..5])은 행마다 period_label_1~6 으로 병기해 DATA1~6 과 짝을 보존한다. DATAQ* 는
    라벨이 blob 에 없어(QOQ/YOY 코멘트가 상대 위치만 말한다) 슬롯명 그대로 val_q* 컬럼에 둔다.
    키는 (cmp_cd, fetched_date, ep, seq=배열 위치) — cF4002 는 같은 ACCODE 가 여러 P_ACCODE 아래
    반복된다(실측 1,614/1,614 blob).
    """
    n_blobs: Counter[str] = Counter()
    n_empty = n_failed = n_label_other = 0
    out: list[dict[str, str | None]] = []
    for b in blobs:
        n_blobs[b.ep] += 1
        try:
            top, rows = _json_rows(b.body, "DATA")
        except Exception:  # noqa: BLE001  # reason: blob 손상은 도메인 실패 → parse_failed 계상
            top, rows = None, None
        if rows is None or top is None:
            n_failed += 1
            continue
        if not rows:
            n_empty += 1
            continue
        labels_obj = top.get("YYMM")
        labels = [_s(x) for x in labels_obj] if isinstance(labels_obj, list) else []
        if len(labels) != _FIN_LABEL_N:
            n_label_other += 1
        fs_basis, freq = _s(top.get("FIN")), _s(top.get("FRQ"))
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                n_failed += 1
                continue
            row: dict[str, str | None] = dict.fromkeys(FIN_WISE_COLUMNS)
            row.update(cmp_cd=b.cmp_cd, fetched_date=b.fetched_date, ep=b.ep, seq=str(i),
                       fs_basis=fs_basis, freq=freq, fetched_at=b.fetched_at)
            for src, dst in _FIN_ATTRS.items():
                row[dst] = _s(r.get(src))
            for k, slot in enumerate(_FIN_SLOTS, 1):
                row[f"val_{k}"] = _s(r.get(slot))
                row[f"period_label_{k}"] = labels[k - 1] if k - 1 < len(labels) else None
            for slot in _FIN_QSLOTS:
                row["val_q" + slot[5:]] = _s(r.get(slot))
            for src, dst in _FIN_SCALARS.items():
                row[dst] = _s(r.get(src))
            out.append(row)
    out.sort(key=lambda r: (str(r["cmp_cd"]), str(r["fetched_date"]), str(r["ep"]),
                            int(str(r["seq"]))))
    return ParseResult(rows=out, columns=FIN_WISE_COLUMNS, metrics={
        "n_blobs": dict(n_blobs), "n_empty": n_empty, "n_rows_emitted": len(out),
        "n_parse_failed": n_failed, "n_value_mismatch": 0, "n_label_shape_other": n_label_other})


ANALYST_COLUMNS: tuple[str, ...] = (
    "cmp_cd", "fetched_date", "base_date", "opinion_score", "target_price_krw", "eps_krw", "per",
    "analyst_count", "no_opinion_note", "fetched_at",
)
_TB15_RE = re.compile(r'id="cTB15".*?</table>', re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_BASE_RE = re.compile(r"\[기준:([^\]]*)\]")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


_NA_TOKENS = frozenset({"N/A", "n/a", "NA"})   # WISE 표기 결측 — PER 'N/A' 98 blob 실측


def _cell_text(fragment: str) -> str:
    """태그 제거 → 엔티티 해제(&nbsp; → 공백) → 공백 축약 → strip. 빈 셀·'N/A' = ''(blank 계상)."""
    t = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()
    return "" if t in _NA_TOKENS else t


def parse_analyst_summary(blobs: Iterable[RawBlob]) -> ParseResult:
    """c1010001 HTML: `id="cTB15"` 표 마지막 행 → (투자의견·목표주가·EPS·PER·추정기관수) 1행/blob.

    실측 모양 3종(1,612 blob): 5셀 숫자 · 5셀 중 빈칸(&nbsp;/'') · 단일 셀 '최근N개월 이내에 제시된
    의견이 없습니다'(346) → 값 NULL + no_opinion_note. `<script>alert(…)` 리다이렉트 본문(1)은
    데이터 없음(n_no_data, 실패 아님). 기준일은 '[기준:YYYY.MM.DD]'.
    """
    n_blobs = n_no_data = n_failed = n_no_opinion = n_dup = n_na = 0
    coords: dict[tuple[str, str], dict[str, str | None]] = {}
    for b in blobs:
        n_blobs += 1
        try:
            text = _decode_text(b.body)
        except Exception:  # noqa: BLE001  # reason: blob 손상은 도메인 실패 → parse_failed 계상
            n_failed += 1
            continue
        m = _TB15_RE.search(text)
        if m is None:
            if "alert(" in text and len(text) < 1000:
                n_no_data += 1
            else:
                n_failed += 1
            continue
        trs = _TR_RE.findall(m.group(0))
        cells = [_cell_text(td) for td in _TD_RE.findall(trs[-1])] if trs else []
        base = _BASE_RE.search(text)
        row: dict[str, str | None] = dict.fromkeys(ANALYST_COLUMNS)
        row.update(cmp_cd=b.cmp_cd, fetched_date=b.fetched_date,
                   base_date=base.group(1).strip() if base else None, fetched_at=b.fetched_at)
        if len(cells) == 1:
            n_no_opinion += 1
            row["no_opinion_note"] = cells[0]
        elif len(cells) == 5:
            n_na += sum(1 for td in _TD_RE.findall(trs[-1])
                        if _TAG_RE.sub("", td).strip() in _NA_TOKENS)
            row.update(opinion_score=cells[0], target_price_krw=cells[1], eps_krw=cells[2],
                       per=cells[3], analyst_count=cells[4])
        else:
            n_failed += 1
            continue
        key = (b.cmp_cd, b.fetched_date)
        if key in coords:
            n_dup += 1
            continue
        coords[key] = row
    out = [coords[k] for k in sorted(coords)]
    return ParseResult(rows=out, columns=ANALYST_COLUMNS, metrics={
        "n_blobs": n_blobs, "n_no_data": n_no_data, "n_no_opinion": n_no_opinion,
        "n_rows_emitted": len(out), "n_parse_failed": n_failed, "n_value_mismatch": 0,
        "n_dup_coords_folded": n_dup, "n_na_cells": n_na})


PARSERS.update({
    "parse_consensus_annual": parse_consensus_annual,
    "parse_consensus_quarterly": parse_consensus_quarterly,
    "parse_consensus_matrix": parse_consensus_matrix,
    "parse_fin_wise": parse_fin_wise,
    "parse_analyst_summary": parse_analyst_summary,
})
