"""현재 writer로 만들지 않는 고정 v1 입력으로 저장 codec 호환성을 검증한다."""

import hashlib
from pathlib import Path

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore

# 합성 기록을 고정한 역사 입력이다. 현재 모델/registry 직렬화로 재생성하지 않는다.
_V1_RESULT = r"""
{
  "artifacts": {
    "costs": [
      {
        "amount": 0.1,
        "kind": "fee",
        "security_id": "KRX:005930",
        "session": "2026-01-05"
      },
      {
        "amount": 0.0,
        "kind": "tax",
        "security_id": null,
        "session": "2026-01-05"
      }
    ],
    "fills": [
      {
        "fee": 0.1,
        "fill_id": "f-1",
        "order_id": "o-1",
        "price": 30.0,
        "quantity": "3",
        "security_id": "KRX:005930",
        "session": "2026-01-05",
        "side": "buy",
        "slippage_per_share": 0.03
      }
    ],
    "orders": [
      {
        "decision_id": "d-1",
        "order_id": "o-1",
        "order_type": "market",
        "quantity": "3",
        "security_id": "KRX:005930",
        "session": "2026-01-02",
        "side": "buy",
        "time_in_force": "day"
      }
    ],
    "positions": [
      {
        "average_price": 30.0,
        "market_price": 31.0,
        "market_value": 93.0,
        "quantity": "3",
        "security_id": "KRX:005930",
        "session": "2026-01-05",
        "unrealized_pnl": 3.0
      }
    ],
    "schema_version": "backtest-artifacts-v1",
    "snapshots": [
      {
        "cash": 100.0,
        "equity": 100.0,
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "session": "2026-01-02"
      }
    ],
    "trades": [
      {
        "closed_on": "2026-01-06",
        "entry_price": 30.0,
        "exit_price": 31.0,
        "fees": 0.2,
        "opened_on": "2026-01-05",
        "pnl": 2.8,
        "quantity": "3",
        "security_id": "KRX:005930",
        "side": "long",
        "slippage_cost": 0.09
      }
    ]
  },
  "manifest": {
    "annualization_days": 252,
    "completed_at": "2026-09-03T00:00:00+00:00",
    "created_at": "2026-09-03T00:00:00+00:00",
    "data_snapshot_id": "snapshot",
    "engine_core": "rust",
    "engine_version": "test",
    "environment": {
      "end": "2026-08-31",
      "fee_bps": 15.0,
      "frequency": "daily",
      "impact_coefficient": 1.0,
      "impact_model": "fixed_bps",
      "market": "KRX",
      "missing": "drop",
      "participation_basis": "adv20",
      "participation_rate": 0.1,
      "sell_tax": "krx_statutory",
      "sell_tax_bps": null,
      "slippage_bps": 10.0,
      "start": "2021-01-01",
      "timing": "next_open",
      "universe_id": "krx.common-stock"
    },
    "environment_hash": "97a3ceec06f2e6590cddbec207a93bed49c281c4fb6229270b7993a942db8c59",
    "fee_bps": 15.0,
    "initial_cash": 100.0,
    "metric_registry_version": "metric-registry-v5",
    "participation_rate": 0.1,
    "run_fingerprint": "fingerprint",
    "run_id": "run-safe-001",
    "run_spec": {
      "annualization_days": 252,
      "benchmark_security_id": "KRX:069500",
      "core": "rust",
      "environment": {
        "end": "2026-08-31",
        "fee_bps": 15.0,
        "frequency": "daily",
        "impact_coefficient": 1.0,
        "impact_model": "fixed_bps",
        "market": "KRX",
        "missing": "drop",
        "participation_basis": "adv20",
        "participation_rate": 0.1,
        "sell_tax": "krx_statutory",
        "sell_tax_bps": null,
        "slippage_bps": 10.0,
        "start": "2021-01-01",
        "timing": "next_open",
        "universe_id": "krx.common-stock"
      },
      "initial_cash": 100000000.0,
      "lineage_strategy_id": null,
      "metric_windows": [
        {
          "end": "2022-12-29",
          "label": "2022",
          "scope": "window",
          "start": "2022-01-03"
        }
      ],
      "parameter_values": {
        "count": 25,
        "mode": true,
        "weight": 1.0
      },
      "strategy": {
        "description": "",
        "eligibility": {
          "rules": []
        },
        "factors": [
          {
            "direction": "high",
            "factor_id": "price.close",
            "graph": {
              "nodes": [
                {
                  "field_id": "price.close",
                  "kind": "field",
                  "node_id": "close"
                }
              ],
              "output_node_id": "close"
            },
            "label": "종가",
            "weight": 1.0
          }
        ],
        "identity": {
          "revision": 0,
          "schema_version": "1.2",
          "strategy_id": "draft"
        },
        "parameters": [
          {
            "default": 0.5,
            "kind": "float",
            "maximum": 1.0,
            "minimum": 0.0,
            "parameter_id": "weight",
            "step": 0.1
          },
          {
            "default": 20,
            "kind": "integer",
            "maximum": 40,
            "minimum": 5,
            "parameter_id": "count",
            "step": 5
          },
          {
            "choices": [
              "fast",
              2,
              true
            ],
            "default": "fast",
            "kind": "choice",
            "parameter_id": "mode"
          }
        ],
        "portfolio": {
          "liquidity_field_id": null,
          "minimum_liquidity": null,
          "minimum_trade_weight": 0.0,
          "rebalance": "monthly",
          "rebalance_every_n_sessions": 21,
          "selection_count": 20,
          "selection_method": "top_n",
          "selection_percentile": 0.1,
          "short_selection_count": 20,
          "side": "long_only",
          "tie_breaker_direction": "high",
          "tie_breaker_factor_id": null,
          "turnover_buffer_count": 0,
          "weighting": "equal"
        },
        "risk": {
          "gross_exposure": 1.0,
          "max_name_weight": 0.1,
          "max_sector_weight": 0.3,
          "net_exposure": 1.0,
          "risk_factor_id": null,
          "risk_field_id": null,
          "sector_neutral": false
        },
        "signal": {
          "normalization": "rank",
          "regime_field_id": null,
          "regime_minimum": null,
          "score_threshold": null
        },
        "title": "새 팩터 전략"
      },
      "strategy_source": null
    },
    "schema_version": "backtest-run-v2",
    "slippage_bps": 10.0,
    "strategy_hash": "strategy",
    "strategy_provenance": {
      "kind": "inline_draft",
      "revision": null,
      "schema_version": "1.1",
      "source_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "spec_hash": "strategy",
      "strategy_id": null
    },
    "target_tape_hash": "tape",
    "warnings": [
      {
        "code": "portfolio.raw_observation",
        "message": "late filing",
        "severity": "info"
      },
      {
        "code": "backtest.data.gap",
        "message": "missing bar",
        "severity": "warning"
      }
    ]
  },
  "metric_definitions": [
    {
      "category": "return",
      "higher_is_better": true,
      "label": "Total return",
      "metric_id": "total_return",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "return",
      "higher_is_better": true,
      "label": "CAGR",
      "metric_id": "cagr",
      "nullable": true,
      "precision": 4,
      "unit": "percent",
      "version": 2
    },
    {
      "category": "risk",
      "higher_is_better": false,
      "label": "Volatility",
      "metric_id": "volatility",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "risk_adjusted",
      "higher_is_better": true,
      "label": "Sharpe ratio",
      "metric_id": "sharpe",
      "nullable": true,
      "precision": 4,
      "unit": "ratio",
      "version": 2
    },
    {
      "category": "risk_adjusted",
      "higher_is_better": null,
      "label": "Sharpe standard error",
      "metric_id": "sharpe_standard_error",
      "nullable": true,
      "precision": 4,
      "unit": "ratio",
      "version": 1
    },
    {
      "category": "risk_adjusted",
      "higher_is_better": true,
      "label": "Probabilistic Sharpe ratio",
      "metric_id": "probabilistic_sharpe",
      "nullable": true,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "risk_adjusted",
      "higher_is_better": true,
      "label": "Sortino ratio",
      "metric_id": "sortino",
      "nullable": true,
      "precision": 4,
      "unit": "ratio",
      "version": 2
    },
    {
      "category": "risk",
      "higher_is_better": true,
      "label": "Maximum drawdown",
      "metric_id": "max_drawdown",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "risk_adjusted",
      "higher_is_better": true,
      "label": "Calmar ratio",
      "metric_id": "calmar",
      "nullable": true,
      "precision": 4,
      "unit": "ratio",
      "version": 2
    },
    {
      "category": "trade",
      "higher_is_better": false,
      "label": "Turnover",
      "metric_id": "turnover",
      "nullable": false,
      "precision": 4,
      "unit": "ratio",
      "version": 1
    },
    {
      "category": "risk",
      "higher_is_better": false,
      "label": "Maximum drawdown duration",
      "metric_id": "max_drawdown_duration_sessions",
      "nullable": false,
      "precision": 0,
      "unit": "sessions",
      "version": 1
    },
    {
      "category": "risk",
      "higher_is_better": false,
      "label": "Maximum drawdown recovery",
      "metric_id": "max_drawdown_recovery_sessions",
      "nullable": true,
      "precision": 0,
      "unit": "sessions",
      "version": 1
    },
    {
      "category": "benchmark",
      "higher_is_better": true,
      "label": "Benchmark return",
      "metric_id": "benchmark_return",
      "nullable": true,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "benchmark",
      "higher_is_better": true,
      "label": "Excess return",
      "metric_id": "excess_return",
      "nullable": true,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "trade",
      "higher_is_better": null,
      "label": "Closed trades",
      "metric_id": "trade_count",
      "nullable": false,
      "precision": 0,
      "unit": "count",
      "version": 1
    },
    {
      "category": "trade",
      "higher_is_better": true,
      "label": "Win rate",
      "metric_id": "win_rate",
      "nullable": true,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "trade",
      "higher_is_better": true,
      "label": "Profit factor",
      "metric_id": "profit_factor",
      "nullable": true,
      "precision": 4,
      "unit": "ratio",
      "version": 1
    },
    {
      "category": "exposure",
      "higher_is_better": null,
      "label": "Average gross exposure",
      "metric_id": "average_gross_exposure",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "exposure",
      "higher_is_better": false,
      "label": "Maximum gross exposure",
      "metric_id": "maximum_gross_exposure",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "exposure",
      "higher_is_better": null,
      "label": "Average net exposure",
      "metric_id": "average_net_exposure",
      "nullable": false,
      "precision": 4,
      "unit": "percent",
      "version": 1
    },
    {
      "category": "cost",
      "higher_is_better": false,
      "label": "Total fees",
      "metric_id": "total_fees",
      "nullable": false,
      "precision": 2,
      "unit": "currency",
      "version": 1
    },
    {
      "category": "cost",
      "higher_is_better": false,
      "label": "Sell taxes",
      "metric_id": "total_taxes",
      "nullable": false,
      "precision": 2,
      "unit": "currency",
      "version": 1
    },
    {
      "category": "cost",
      "higher_is_better": false,
      "label": "Slippage cost",
      "metric_id": "total_slippage_cost",
      "nullable": false,
      "precision": 2,
      "unit": "currency",
      "version": 1
    },
    {
      "category": "cost",
      "higher_is_better": false,
      "label": "Borrow and margin cost",
      "metric_id": "total_carry_cost",
      "nullable": false,
      "precision": 2,
      "unit": "currency",
      "version": 1
    }
  ],
  "metrics": [
    {
      "metric_id": "total_return",
      "sample_count": 1,
      "scope": "full",
      "scope_label": null,
      "unavailable_reason": null,
      "value": 0.0
    },
    {
      "metric_id": "sharpe",
      "sample_count": 1,
      "scope": "full",
      "scope_label": null,
      "unavailable_reason": "zero_return_variance",
      "value": null
    },
    {
      "metric_id": "cagr",
      "sample_count": 250,
      "scope": "window",
      "scope_label": "2022",
      "unavailable_reason": null,
      "value": 0.125
    }
  ],
  "series": {
    "drawdown": [
      {
        "drawdown": -0.015,
        "session": "2026-01-05"
      }
    ],
    "equity": [
      {
        "benchmark_equity": null,
        "equity": 100.0,
        "session": "2026-01-02"
      },
      {
        "benchmark_equity": 100.25,
        "equity": 101.5,
        "session": "2026-01-05"
      }
    ],
    "monthly_returns": [
      {
        "month": 1,
        "value": 0.015,
        "year": 2026
      }
    ],
    "rolling_sharpe": []
  }
}
"""


def test_historical_v1_result_decodes_without_inventing_later_fields(tmp_path: Path) -> None:
    payload = _V1_RESULT.encode("utf-8")
    run_id = "run-safe-001"
    directory = tmp_path / run_id
    directory.mkdir()
    (directory / "result.json").write_bytes(payload)
    result = LocalArtifactStore(tmp_path).load(run_id, sha256=hashlib.sha256(payload).hexdigest())
    assert result.manifest.run_id == run_id
    assert result.manifest.run_spec.parameter_values == {"weight": 1.0, "count": 25, "mode": True}
    assert type(result.manifest.run_spec.parameter_values["weight"]) is float
    assert type(result.manifest.run_spec.parameter_values["count"]) is int
    assert type(result.manifest.run_spec.parameter_values["mode"]) is bool
    assert result.artifacts.schema_version == "backtest-artifacts-v1"
    assert result.artifacts.roundings == ()
    assert result.artifacts.fills[0].sell_tax is None
    assert result.artifacts.fills[0].cap_volume is None
    assert result.artifacts.fills[0].quantity == "3"
    assert result.artifacts.fills[0].price == 30.0
    assert result.series.rolling_sharpe_window_sessions is None
    assert result.metrics[2].value == 0.125


# 요청 codec도 현재 writer를 호출하지 않는 고정 입력을 사용한다.
_HISTORICAL_REQUEST = (
    '{"inline_draft": null, "run_spec": {"annualization'
    '_days": 252, "benchmark_security_id": "KRX:069500"'
    ', "core": "rust", "environment": {"end": "2026-08-'
    '31", "fee_bps": 15.0, "frequency": "daily", "impac'
    't_coefficient": 1.0, "impact_model": "fixed_bps", '
    '"market": "KRX", "missing": "drop", "participation'
    '_basis": "adv20", "participation_rate": 0.1, "sell'
    '_tax": "krx_statutory", "sell_tax_bps": null, "sli'
    'ppage_bps": 10.0, "start": "2021-01-01", "timing":'
    ' "next_open", "universe_id": "krx.common-stock"}, '
    '"initial_cash": 100000000.0, "lineage_strategy_id"'
    ': null, "metric_windows": [{"end": "2022-12-29", "'
    'label": "2022", "scope": "window", "start": "2022-'
    '01-03"}], "parameter_values": {"count": 25, "mode"'
    ': true, "weight": 1.0}, "strategy": null, "strateg'
    'y_source": null}, "strategy": {"canonical": "{\\"de'
    'scription\\":\\"\\",\\"eligibility\\":{\\"rules\\":[]},\\"'
    'factors\\":[{\\"direction\\":\\"high\\",\\"factor_id\\":\\'
    '"price.close\\",\\"graph\\":{\\"nodes\\":[{\\"field_id\\"'
    ':\\"price.close\\",\\"kind\\":\\"field\\",\\"node_id\\":\\"'
    'close\\"}],\\"output_node_id\\":\\"close\\"},\\"label\\":'
    '\\"종가\\",\\"weight\\":1.0}],\\"parameters\\":[{\\"default'
    '\\":0.5,\\"kind\\":\\"float\\",\\"maximum\\":1.0,\\"minimu'
    'm\\":0.0,\\"parameter_id\\":\\"weight\\",\\"step\\":0.1},'
    '{\\"default\\":20,\\"kind\\":\\"integer\\",\\"maximum\\":4'
    '0,\\"minimum\\":5,\\"parameter_id\\":\\"count\\",\\"step\\'
    '":5},{\\"choices\\":[\\"fast\\",2,true],\\"default\\":\\"'
    'fast\\",\\"kind\\":\\"choice\\",\\"parameter_id\\":\\"mode'
    '\\"}],\\"portfolio\\":{\\"liquidity_field_id\\":null,\\"'
    'minimum_liquidity\\":null,\\"minimum_trade_weight\\":'
    '0.0,\\"rebalance\\":\\"monthly\\",\\"rebalance_every_n_'
    'sessions\\":21,\\"selection_count\\":20,\\"selection_m'
    'ethod\\":\\"top_n\\",\\"selection_percentile\\":0.1,\\"s'
    'hort_selection_count\\":20,\\"side\\":\\"long_only\\",\\'
    '"turnover_buffer_count\\":0,\\"weighting\\":\\"equal\\"'
    '},\\"risk\\":{\\"gross_exposure\\":1.0,\\"max_name_weig'
    'ht\\":0.1,\\"max_sector_weight\\":0.3,\\"net_exposure\\'
    '":1.0,\\"risk_factor_id\\":null,\\"risk_field_id\\":nu'
    'll,\\"sector_neutral\\":false},\\"schema_version\\":\\"'
    '1.2\\",\\"signal\\":{\\"normalization\\":\\"rank\\",\\"reg'
    'ime_field_id\\":null,\\"regime_minimum\\":null,\\"scor'
    'e_threshold\\":null},\\"title\\":\\"새 팩터 전략\\"}", "revi'
    'sion": 0, "strategy_id": "draft"}}'
)


def test_fixed_stored_request_preserves_strategy_identity_and_parameters() -> None:
    from strategy_workbench.adapters.outbound.research_sqlite._request_codec import decode_request

    request = decode_request(_HISTORICAL_REQUEST, run_id="historical-request")
    assert request.strategy is not None
    assert (request.strategy.identity.strategy_id, request.strategy.identity.revision) == (
        "draft",
        0,
    )
    assert request.parameter_values == {"weight": 1.0, "count": 25, "mode": True}
    assert request.environment is not None
    assert request.environment.start.isoformat() == "2021-01-01"


def test_historical_taxed_sell_decodes_and_reconstructs_without_current_writer(
    tmp_path: Path,
) -> None:
    import json

    from strategy_workbench.domain.backtest.facade.trade_analytics import closed_trades

    # 기존 고정 envelope에 구판 형태의 매도·세금 기록을 추가한다. 현재 writer는 호출하지 않는다.
    document = json.loads(_V1_RESULT)
    document["artifacts"]["fills"][0]["fee"] = 0.125
    document["artifacts"]["fills"].append(
        {
            "fee": 0.25,
            "fill_id": "f-2",
            "order_id": "o-2",
            "price": 40.0,
            "quantity": "3",
            "security_id": "KRX:005930",
            "session": "2026-01-06",
            "side": "sell",
            "slippage_per_share": 0.0,
        }
    )
    document["artifacts"]["costs"].append(
        {
            "amount": 2.0,
            "kind": "sell_tax",
            "security_id": "KRX:005930",
            "session": "2026-01-06",
        }
    )
    payload = json.dumps(document).encode()
    directory = tmp_path / "run-safe-001"
    directory.mkdir()
    (directory / "result.json").write_bytes(payload)
    result = LocalArtifactStore(tmp_path).load(
        "run-safe-001", sha256=hashlib.sha256(payload).hexdigest()
    )
    assert [fill.sell_tax for fill in result.artifacts.fills] == [None, None]
    (trade,) = closed_trades(result.artifacts.fills, result.artifacts.costs)
    assert trade.fees == 2.375
    assert trade.pnl == 27.625
