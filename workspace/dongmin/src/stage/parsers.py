"""blob 언네스트 파서 (§1 예외 c·e) — duckdb 로 못 하는 변환만 파이썬으로 한다.

ws_raw 본문 = zlib(`789C`) → 외부 JSON {chart1: <JSON 문자열>, chart2: <JSON 문자열>} → 내부 JSON.
양쪽 loads 모두 parse_float=Decimal (float 경유 금지). 출력은 전부 VARCHAR|None 이라 stage 캐스팅
규칙이 원장 컬럼과 똑같이 적용된다. 파서는 파이썬 루프지만 blob 이 1.3만 개라 수 초다 (§10 벤치).
"""
from __future__ import annotations

import json
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
        except Exception:  # noqa: BLE001  # reason: 원장 blob 손상은 도메인 실패 → parse_failed 계상
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
