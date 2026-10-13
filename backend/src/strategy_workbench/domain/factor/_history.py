"""노드가 읽는 세션 구간의 정본: 평가·최소 이력·가림 구간·trace가 같은 창을 쓴다."""

from __future__ import annotations

from dataclasses import dataclass

from ._nodes import ExpressionNode, TimeSeriesNode, UnaryNode, UnaryOperator


@dataclass(frozen=True)
class SessionReadWindow:
    """현재 관측에서 가까운/먼 쪽으로 몇 세션 앞까지 읽는가(양끝 포함)."""

    near: int = 0
    far: int = 0

    def bounds(self, position: int) -> slice:
        """종목별 정렬된 관측 배열에서 읽을 반열린 구간. 음수 시작은 이력 부족이다."""
        return slice(position - self.far, position - self.near + 1)


def node_read_window(node: ExpressionNode) -> SessionReadWindow:
    if isinstance(node, UnaryNode) and node.operator is UnaryOperator.LAG:
        periods = node.periods or 0
        return SessionReadWindow(periods, periods)
    if isinstance(node, TimeSeriesNode):
        return SessionReadWindow(node.lag, node.lag + node.window - 1)
    return SessionReadWindow()
