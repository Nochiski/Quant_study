"""선 저장 구조(설계 문서 4.7)의 어휘와 알고리즘 버전의 owner.

이 노드 안에서는 `LineKind`만 쓰지만, 나머지도 저장 칸의 값이라 PR2(저장·HTTP)가 이 이름 그대로
읽는다.
"""

from __future__ import annotations

from enum import Enum

# 자동 선의 `algorithm@version` 칸에 그대로 저장하는 값. 판정 규칙·B*·피벗·채널·수평 구간·중첩
# 규칙(기본 파라미터 포함)을 바꾸면 올린다. tests/domain/test_chart_analysis_version.py가 규칙
# 상수와 함께 묶는다.
ALGORITHM_VERSION = "chart_analysis@1"


class LineKind(Enum):
    HLINE = "hline"
    FIB_RETRACEMENT = "fib_retracement"
    FIB_CHANNEL = "fib_channel"


class LineSource(Enum):
    """선의 출처: 손으로 그었으면 manual, 자동 후보를 그대로 저장했으면 auto, 자동 후보를 내 선으로
    옮겨 고칠 수 있게 했으면 auto_copied다. auto·auto_copied는 자동 기록(알고리즘 버전·파라미터·판정
    구간)을 함께 저장한다."""

    MANUAL = "manual"
    AUTO = "auto"
    AUTO_COPIED = "auto_copied"


class WindowSource(Enum):
    """판정 구간의 출처. 자동 제안 칩 길이 그대로면 preset, 테두리를 끌거나 날짜를 고치면 user다."""

    PRESET = "preset"
    USER = "user"


class ChosenBy(Enum):
    """채널 방향을 누가 정했나: 국면 판정 추천을 따랐으면 recommendation, 바꿨으면 user_override."""

    RECOMMENDATION = "recommendation"
    USER_OVERRIDE = "user_override"
