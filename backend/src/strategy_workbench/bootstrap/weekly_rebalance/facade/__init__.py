"""주간 리밸런싱(S07) 명령줄의 별도 composition root."""

DEPENDS_ON: tuple[str, ...] = (
    "application.offline_execution",
    "application.weekly_rebalance",
    "adapters.inbound.weekly_rebalance_cli",
    "adapters.outbound.execution_sqlite",
    "adapters.outbound.kiwoom_mock",
    "adapters.outbound.ledger_prices",
    "adapters.outbound.rebalance_files",
    "adapters.outbound.scope_signal",
    "adapters.outbound.secrets_local",
    "adapters.outbound.telegram_notice",
    "bootstrap",
    "domain.order_execution",
)
