"""모델 빌드에서 scope 순위와 신호일 종가를 읽는다(S07).

모델 빌드 루트 배치. 장 마감 빌드는 서버 quant-ledger 의 모델 DB(`data/model_db`), 주말 보강판은
연구 모델 루트(`data`)에 있고 배치는 같다:

- `model/latest_<basis>.json`: 그 종류의 마지막 성공 빌드
  (`build_id`·`date`·`fi_build_id`·모델별 게이트). 장 마감 빌드는 `latest_evening.json`,
  주말 보강판은 `latest_supplement.json`이다.
- `model/<모델>/MANIFEST.json`: 모델별 커밋된 빌드 목록(`builds`)과 마지막 커밋(`current_build`)
- `model/<모델>/v=<build_id>/scores.parquet`: `stock_code`·`rank`·`score_date`
- `factor_inputs/fi_prices/v=<fi_build_id>/*.parquet`: 점수를 계산한 가격 판
  (`ticker`·`date`·`close`·`volume`). 신호일 행이 없는 종목(거래정지 등)은 같은 판의 그 전 마지막
  종가를 따로 싣는다(보유 종목 평가용).
- `factor_inputs/fi_universe/v=<fi_build_id>/*.parquet`: 같은 판의 신호일 유니버스. 이름·빠진 이유와
  리스크 가드가 읽는 20일 평균 거래대금(`adv20`, 억 원)·관리종목(`is_admin`)·거래정지(`is_halted`).

종류별 포인터(`latest_<basis>.json`)가 가리키는 판만 읽고, 그 판은 MANIFEST 빌드 목록에 있어야 한다.
폴더에 남은 옛 판을 훑지 않는다. `current_build`는 종류와 상관없이 마지막 커밋이라 보지 않는다 —
연구 모델 루트는 아침 확정판과 주말 보강판이 MANIFEST 하나를 써서, 보강판 뒤에 아침판만 다시 지어도
`current_build`가 아침판으로 바뀐다(2026-10-24 사용자 결정 D2).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    EarlierClose,
    SignalBasis,
    SignalRead,
    SignalReadStatus,
    SignalSnapshot,
)
from strategy_workbench.domain.risk.facade.risk import SignalFacts

# 메인 모델 scope_v2.0(2026-10-11 사용자 결정 N-47, 엑셀 주 모델과 같다). scope@1.0은 비교용이다.
DEFAULT_MODEL_ID = "scope@2.0"
# 게이트 기록 상태. warn은 통과에 경고 표시가 붙은 것이고 skip은 그 게이트가 판정하지 않은 것이다.
_ACCEPTED_GATES = frozenset({"pass", "warn", "skip"})


class _Unreadable(Exception):
    pass


class ScopeSignalSource:
    def __init__(self, model_db_root: Path, *, model_id: str = DEFAULT_MODEL_ID) -> None:
        self._root = model_db_root
        self._model_id = model_id

    def read(self, signal_date: date, basis: SignalBasis) -> SignalRead:
        try:
            return self._read(signal_date, basis)
        except _Unreadable as error:
            return SignalRead(SignalReadStatus.UNREADABLE, detail=str(error))

    def _read(self, signal_date: date, basis: SignalBasis) -> SignalRead:
        latest = _json(self._root / "model" / f"latest_{basis.value}.json")
        identity = f"model_id={self._model_id} signal_date={signal_date.isoformat()}"
        if latest.get("status") != "ok" or latest.get("basis") != basis.value:
            return SignalRead(
                SignalReadStatus.NOT_READY,
                detail=f"latest {basis.value} build is not ok — {identity} "
                f"status={latest.get('status')!r} basis={latest.get('basis')!r}",
            )
        if latest.get("date") != signal_date.isoformat():
            return SignalRead(
                SignalReadStatus.NOT_READY,
                detail=f"latest {basis.value} build is for another date — {identity} "
                f"latest_date={latest.get('date')!r}",
            )
        build_id, prices_build_id = _text(latest, "build_id"), _text(latest, "fi_build_id")
        excluded = latest.get("excluded_specs")
        specs = latest.get("specs")
        spec = specs.get(self._model_id) if isinstance(specs, dict) else None
        if (isinstance(excluded, dict) and self._model_id in excluded) or not isinstance(
            spec, dict
        ):
            return SignalRead(
                SignalReadStatus.GATE_FAILED,
                detail=f"model missing or excluded from the build — {identity} build_id={build_id}",
            )
        gates = spec.get("gates") or {}
        failed = {
            name: gate.get("status")
            for name, gate in gates.items()
            if not isinstance(gate, dict) or gate.get("status") not in _ACCEPTED_GATES
        }
        if not gates or failed:
            return SignalRead(
                SignalReadStatus.GATE_FAILED,
                detail=f"model gates did not pass — {identity} build_id={build_id} "
                f"failed={failed or 'no gates'}",
            )
        model_dir = self._root / "model" / self._model_id
        manifest = _json(model_dir / "MANIFEST.json")
        if build_id not in _build_ids(manifest):
            raise _Unreadable(
                f"build is not committed in the model manifest — {identity} latest={build_id} "
                f"current_build={manifest.get('current_build')!r}"
            )
        scores_path = model_dir / f"v={build_id}" / "scores.parquet"
        ranks = _ranks(scores_path, signal_date)
        prices_dir = self._root / "factor_inputs" / "fi_prices" / f"v={prices_build_id}"
        closes, untraded = _closes(prices_dir, signal_date)
        earlier = _earlier_closes(prices_dir, signal_date, closes)
        previous_id, previous_ranks = _previous(model_dir, manifest, build_id, signal_date)
        universe = self._root / "factor_inputs" / "fi_universe" / f"v={prices_build_id}"
        names, reasons = _universe_notes(universe, ranks, closes)
        return SignalRead(
            SignalReadStatus.OK,
            SignalSnapshot(
                self._model_id,
                build_id,
                signal_date,
                ranks,
                closes,
                untraded,
                hashlib.sha256(scores_path.read_bytes()).hexdigest(),
                prices_build_id,
                previous_id,
                previous_ranks,
                names,
                reasons,
                earlier,
                _facts(universe),
            ),
        )


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise _Unreadable(f"cannot read json — file={path.name} error={error}") from error
    if not isinstance(value, dict):
        raise _Unreadable(f"json is not an object — file={path.name}")
    return value


def _text(value: dict[str, object], key: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text:
        raise _Unreadable(f"missing field — key={key}")
    return text


def _build_ids(manifest: dict[str, object]) -> list[object]:
    """MANIFEST 의 커밋된 빌드 id, 커밋 순서대로."""
    builds = manifest.get("builds")
    if not isinstance(builds, list):
        return []
    return [b.get("build_id") for b in builds if isinstance(b, dict)]


def _ranks(path: Path, signal_date: date) -> dict[str, int]:
    try:
        table = pq.read_table(path, columns=["stock_code", "rank", "score_date"])
    except (OSError, ValueError, KeyError) as error:
        raise _Unreadable(f"cannot read scores — build={path.parent.name} error={error}") from error
    dates = set(table.column("score_date").to_pylist())
    if dates != {signal_date.isoformat()}:
        raise _Unreadable(
            f"scores are not for the signal date — build={path.parent.name} dates={sorted(dates)}"
        )
    ranks: dict[str, int] = {}
    for code, rank in zip(
        table.column("stock_code").to_pylist(), table.column("rank").to_pylist(), strict=True
    ):
        if not isinstance(code, str) or len(code) != 6 or not isinstance(rank, int) or rank < 1:
            raise _Unreadable(f"invalid score row — code={code!r} rank={rank!r}")
        if code in ranks:
            raise _Unreadable(f"duplicate score row — code={code}")
        ranks[code] = rank
    if len(set(ranks.values())) != len(ranks):
        raise _Unreadable(f"ranks are not unique — build={path.parent.name} n={len(ranks)}")
    return ranks


def _previous(
    model_dir: Path, manifest: dict[str, object], build_id: str, signal_date: date
) -> tuple[str | None, dict[str, int] | None]:
    """MANIFEST 빌드 목록에서 현재 빌드 앞, 신호일보다 앞 날짜의 가장 최근 판 순위.

    같은 신호일을 다시 지은 판(같은 날 재빌드, 장 마감 판 뒤의 주말 보강판)은 건너뛴다 — 하루 사이
    누락을 보는 점검이라 같은 날 판끼리 견주지 않는다. 그런 판이 없으면 점검을 건너뛰고, 읽지 못한
    판에서는 그 판 id 와 함께 건너뛴다.
    """
    ids = _build_ids(manifest)
    if build_id not in ids:
        return None, None
    for previous in reversed(ids[: ids.index(build_id)]):
        if not isinstance(previous, str):
            return None, None
        try:
            table = pq.read_table(
                model_dir / f"v={previous}" / "scores.parquet",
                columns=["stock_code", "rank", "score_date"],
            )
        except (OSError, ValueError, KeyError):
            return previous, None
        dates = set(table.column("score_date").to_pylist())
        if len(dates) != 1:
            return previous, None
        if str(next(iter(dates))) < signal_date.isoformat():
            return previous, dict(
                zip(
                    table.column("stock_code").to_pylist(),
                    table.column("rank").to_pylist(),
                    strict=True,
                )
            )
    return None, None


# 유니버스 제외 사유 → 사람이 읽는 이유(factor_inputs `EXCLUDE_REASONS`)
_EXCLUDE_TEXT = {
    "estimates_lapsed": "WISE 추정치 공백",
    "estimates_none": "추정치 없음",
    "no_price": "가격 없음(거래정지 등)",
    "corp_action_pending": "기업행위 처리 대기",
    "sec_type": "대상 종목 유형 아님",
    "market": "대상 시장 아님",
}


def _universe_notes(
    folder: Path, ranks: dict[str, int], closes: dict[str, int]
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """점수를 계산한 판의 `fi_universe`에서 종목 이름과, 순위가 없는 종목이 빠진 이유를 읽는다.

    엔진 유니버스는 그날 가격 행 · eligible · 추정치 유예(1.0은 0, 2.0은 5거래일) · 시총 하한이다.
    eligible 이고 신선하며 가격이 있는데 순위가 없으면 남은 규칙은 시총 하한이다. 읽지 못하면
    이름·이유 없이 진행한다.
    """
    files = sorted(folder.glob("*.parquet"))
    columns = ["ticker", "name", "market_cap", "eligible", "exclude_reason", "coverage_state"]
    try:
        table = pa.concat_tables([pq.read_table(file, columns=columns) for file in files])
    except (OSError, ValueError, KeyError, pa.ArrowInvalid):
        return None, None
    names: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for row in table.to_pylist():
        ticker = row["ticker"]
        if not isinstance(ticker, str):
            continue
        if isinstance(row["name"], str):
            names[ticker] = row["name"]
        if ticker in ranks:
            continue
        reason = row["exclude_reason"]
        if not row["eligible"]:
            reasons[ticker] = _EXCLUDE_TEXT.get(str(reason), f"유니버스 제외({reason})")
        elif row["coverage_state"] == "grace":
            reasons[ticker] = "WISE 추정치 공백(유예 중)"
        elif ticker not in closes:
            reasons[ticker] = "가격 없음(거래정지 등)"
        else:
            cap = row["market_cap"]
            reasons[ticker] = (
                f"시총 하한 미만(시총 {cap:,.0f}억)"
                if isinstance(cap, int | float)
                else "시총 하한 미만"
            )
    return names, reasons


_EOK_KRW = 100_000_000


def _facts(folder: Path) -> dict[str, SignalFacts] | None:
    """리스크 한도가 읽는 신호일 종목 자료. 판이나 칸이 없으면 None — 리스크는 그 종목을 사지
    않는다."""
    files = sorted(folder.glob("*.parquet"))
    if not files:
        return None
    try:
        table = pa.concat_tables(
            [
                pq.read_table(file, columns=["ticker", "adv20", "is_admin", "is_halted"])
                for file in files
            ]
        )
    except (OSError, ValueError, KeyError, pa.ArrowInvalid):
        return None
    facts: dict[str, SignalFacts] = {}
    for row in table.to_pylist():
        ticker, adv20 = row["ticker"], row["adv20"]
        if not isinstance(ticker, str):
            continue
        known = isinstance(adv20, int | float) and math.isfinite(adv20) and adv20 >= 0
        facts[ticker] = SignalFacts(
            round(adv20 * _EOK_KRW) if known else None,
            admin=row["is_admin"] is not False,  # 모르면 막는 쪽
            halted=row["is_halted"] is not False,
        )
    return facts


def _closes(folder: Path, signal_date: date) -> tuple[dict[str, int], frozenset[str]]:
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise _Unreadable(f"price build has no parquet files — build={folder.name}")
    try:
        table = pa.concat_tables(
            [
                pq.read_table(
                    file,
                    columns=["ticker", "date", "close", "volume"],
                    filters=[("date", "=", signal_date)],
                )
                for file in files
            ]
        )
    except (OSError, ValueError, KeyError) as error:
        raise _Unreadable(f"cannot read prices — build={folder.name} error={error}") from error
    closes: dict[str, int] = {}
    untraded: set[str] = set()
    for ticker, close, volume in zip(
        table.column("ticker").to_pylist(),
        table.column("close").to_pylist(),
        table.column("volume").to_pylist(),
        strict=True,
    ):
        if not isinstance(close, int) or close <= 0:
            continue
        closes[ticker] = close
        if not volume:
            untraded.add(ticker)
    if not closes:
        raise _Unreadable(
            f"no closes on the signal date — build={folder.name} date={signal_date.isoformat()}"
        )
    return closes, frozenset(untraded)


def _earlier_closes(
    folder: Path, signal_date: date, closes: dict[str, int]
) -> dict[str, EarlierClose]:
    """신호일 행이 없는 종목의 그 전 마지막 양수 종가. 읽지 못하면 비운다(그런 보유 종목이 있으면
    주문안이 막힌다)."""
    try:
        table = pa.concat_tables(
            [
                pq.read_table(
                    file,
                    columns=["ticker", "date", "close"],
                    filters=[("date", "<", signal_date), ("ticker", "not in", sorted(closes))],
                )
                for file in sorted(folder.glob("*.parquet"))
            ]
        )
    except (OSError, ValueError, KeyError, pa.ArrowInvalid):
        return {}
    latest: dict[str, EarlierClose] = {}
    for ticker, day, close in zip(
        table.column("ticker").to_pylist(),
        table.column("date").to_pylist(),
        table.column("close").to_pylist(),
        strict=True,
    ):
        if not isinstance(ticker, str) or not isinstance(close, int) or close <= 0:
            continue
        if ticker not in latest or day > latest[ticker].day:
            latest[ticker] = EarlierClose(day, close)
    return latest
