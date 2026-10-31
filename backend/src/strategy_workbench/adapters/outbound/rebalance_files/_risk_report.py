"""S07 일일 위험 보고 파일. 계좌 사실이 담겨 600으로 만든다.

- `<폴더>/risk/report-YYYY-MM-DD.json`: 그날 장 마감 기록으로 만든 보고(다시 만들면 갈아 끼운다).

판단에 쓰지 않는 보고라 다른 단계는 이 파일을 읽지 않는다. 읽지 못하면 `RiskUnavailableError`다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from strategy_workbench.adapters.outbound.rebalance_files._records import replace_file
from strategy_workbench.adapters.outbound.rebalance_files._risk import (
    _day,
    _decode_state,
    _decoded,
    _encode_state,
    _int,
    _mapping,
    _sequence,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    RiskUnavailableError,
)
from strategy_workbench.domain.risk.facade.risk import (
    AccountPeriod,
    Concentration,
    DroppedReturn,
    RiskLevel,
    RiskReport,
    ScenarioLine,
    ScenarioResult,
    TailStat,
    WindowStat,
)

REPORT_SCHEMA = "s07-risk-report/1"


class JsonRiskReportStore:
    def __init__(self, folder: Path) -> None:
        self._folder = folder / "risk"

    def save_report(self, report: RiskReport) -> None:
        path = self._path(report.day)
        try:
            replace_file(path, _encode(report))
        except OSError as error:
            raise RiskUnavailableError(
                f"risk report not written — file={path.name} error={error.strerror}"
            ) from error

    def report(self, day: date) -> RiskReport | None:
        path = self._path(day)
        return _decoded(path, REPORT_SCHEMA, _decode) if path.exists() else None

    def _path(self, day: date) -> Path:
        return self._folder / f"report-{day.isoformat()}.json"


def _stat(stat: WindowStat) -> dict[str, object]:
    return {"sessions": stat.sessions, "used": stat.used, "value": stat.value}


def _encode(report: RiskReport) -> dict[str, object]:
    tail, concentration, period = report.tail, report.concentration, report.account_period
    return {
        "schema": REPORT_SCHEMA,
        "day": report.day.isoformat(),
        "history_end": None if report.history_end is None else report.history_end.isoformat(),
        "equity_krw": report.equity_krw,
        "cash_krw": report.cash_krw,
        "weights": [[symbol, weight] for symbol, weight in report.weights],
        "names": [[symbol, name] for symbol, name in report.names],
        "volatility": [_stat(stat) for stat in report.volatility],
        "beta": [_stat(stat) for stat in report.beta],
        "tail": {
            "sessions": tail.sessions,
            "used": tail.used,
            "tail": tail.tail,
            "expected_shortfall": tail.expected_shortfall,
            "value_at_risk": tail.value_at_risk,
        },
        "account_volatility": [_stat(stat) for stat in report.account_volatility],
        "account_period": None
        if period is None
        else {
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "account_return": period.account_return,
            "kospi_return": period.kospi_return,
        },
        "concentration": {
            "top1": concentration.top1,
            "top3": concentration.top3,
            "top5": concentration.top5,
            "cash": concentration.cash,
            "count": concentration.count,
        },
        "state": _encode_state(report.state),
        "scenarios": [
            {
                "key": item.key,
                "start": None if item.start is None else item.start.isoformat(),
                "end": None if item.end is None else item.end.isoformat(),
                "portfolio_return": item.portfolio_return,
                "loss_krw": item.loss_krw,
                "kospi_return": item.kospi_return,
                "level_after": item.level_after.value,
                "lines": [
                    {
                        "symbol": line.symbol,
                        "weight": line.weight,
                        "stock_return": line.stock_return,
                        "proxy": line.proxy,
                    }
                    for line in item.lines
                ],
                "missing": list(item.missing),
            }
            for item in report.scenarios
        ],
        "dropped": [
            {"symbol": item.symbol, "day": item.day.isoformat(), "stock_return": item.stock_return}
            for item in report.dropped
        ],
        "missing_pairs": report.missing_pairs,
    }


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"record field must be a number — value={value!r}")
    return float(value)


def _optional(value: object) -> float | None:
    return None if value is None else _float(value)


def _decode_stat(value: object) -> WindowStat:
    item = _mapping(value, "stat")
    return WindowStat(_int(item["sessions"]), _int(item["used"]), _optional(item["value"]))


def _pairs(value: object) -> list[tuple[object, object]]:
    out: list[tuple[object, object]] = []
    for entry in _sequence(value):
        pair = _sequence(entry)
        if len(pair) != 2:
            raise ValueError(f"record pair must have two items — value={pair!r}")
        out.append((pair[0], pair[1]))
    return out


def _decode(value: dict[str, object]) -> RiskReport:
    tail = _mapping(value["tail"], "tail")
    concentration = _mapping(value["concentration"], "concentration")
    period = value["account_period"]
    decoded_period = None
    if period is not None:
        item = _mapping(period, "account_period")
        decoded_period = AccountPeriod(
            date.fromisoformat(str(item["start"])),
            date.fromisoformat(str(item["end"])),
            _float(item["account_return"]),
            _float(item["kospi_return"]),
        )
    scenarios: list[ScenarioResult] = []
    for entry in _sequence(value["scenarios"]):
        item = _mapping(entry, "scenario")
        scenarios.append(
            ScenarioResult(
                str(item["key"]),
                _day(item["start"]),
                _day(item["end"]),
                _float(item["portfolio_return"]),
                _int(item["loss_krw"]),
                _optional(item["kospi_return"]),
                RiskLevel(item["level_after"]),
                tuple(
                    ScenarioLine(
                        str(line["symbol"]),
                        _float(line["weight"]),
                        _float(line["stock_return"]),
                        bool(line["proxy"]),
                    )
                    for line in (_mapping(raw, "line") for raw in _sequence(item["lines"]))
                ),
                tuple(str(symbol) for symbol in _sequence(item["missing"])),
            )
        )
    return RiskReport(
        day=date.fromisoformat(str(value["day"])),
        history_end=_day(value["history_end"]),
        equity_krw=_int(value["equity_krw"]),
        cash_krw=_int(value["cash_krw"]),
        weights=tuple((str(s), _float(w)) for s, w in _pairs(value["weights"])),
        names=tuple((str(s), str(n)) for s, n in _pairs(value["names"])),
        volatility=tuple(_decode_stat(stat) for stat in _sequence(value["volatility"])),
        beta=tuple(_decode_stat(stat) for stat in _sequence(value["beta"])),
        tail=TailStat(
            _int(tail["sessions"]),
            _int(tail["used"]),
            _int(tail["tail"]),
            _optional(tail["expected_shortfall"]),
            _optional(tail["value_at_risk"]),
        ),
        account_volatility=tuple(
            _decode_stat(stat) for stat in _sequence(value["account_volatility"])
        ),
        account_period=decoded_period,
        concentration=Concentration(
            _float(concentration["top1"]),
            _float(concentration["top3"]),
            _float(concentration["top5"]),
            _float(concentration["cash"]),
            _int(concentration["count"]),
        ),
        state=_decode_state(value["state"]),
        scenarios=tuple(scenarios),
        dropped=tuple(
            DroppedReturn(
                str(item["symbol"]),
                date.fromisoformat(str(item["day"])),
                _float(item["stock_return"]),
            )
            for item in (_mapping(raw, "dropped") for raw in _sequence(value["dropped"]))
        ),
        missing_pairs=_int(value["missing_pairs"]),
    )
