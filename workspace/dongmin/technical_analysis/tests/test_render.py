"""텔레그램 캡션 이스케이프와 차트 날짜 축 눈금 글자."""

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from technical_analysis.tool import render

PAGE = Path(render.__file__).parent / "page.html"


def _payload() -> dict[str, object]:
    return {
        "meta": {"name": "S&T모티브", "digits": 0, "close": 52300.0, "change_pct": 1.2, "date": "2026-10-23", "scorecard": True},
        "panel": {
            "checklist_summary": "근거 있는 지표 없음 · 습관 지표 양호 1/10",
            "combo": {
                "panic": {"fraction": 0.112, "is_panic": False},
                "pullback": {"score": 0.94, "label": "눌림목 상위 20%"},
                "high52": {"score": 0.67, "label": "중간"},
                "composite": {"score": 0.84, "label": "상위 20%"},
            },
            "fired": [{"text": "스토캐스틱 %K>%D 교차(20 아래)"}],
            "positions": [{"label": "지정 채널", "ratio": 4.97, "below": "4.5선 5,257", "above": "5선 5,569"}],
            "near_above": [{"value": "5,713", "pct": 2.9, "labels": "일 R2"}],
            "near_below": [{"value": "5,387", "pct": -2.9, "labels": "일 S1 <대>"}],
        },
    }


def test_caption_is_valid_telegram_html() -> None:
    """텔레그램 parse_mode=HTML 은 모르는 태그를 거절한다 — <b> 말고 < > & 는 엔티티여야 한다(10-27 발송 거절 재현)."""
    text = render.caption(_payload())
    assert "<b>S&amp;T모티브</b>" in text
    rest = text.replace("<b>", "").replace("</b>", "")
    assert "<" not in rest and ">" not in rest
    assert re.search(r"&(?!amp;|lt;|gt;)", rest) is None
    plain = html.unescape(rest)
    assert "RSI<30" in plain and "%K>%D" in plain and "S&T모티브" in plain


def _tick_label_js() -> str:
    m = re.search(r"^function tickLabel\(.*?^\}", PAGE.read_text(), re.S | re.M)
    assert m is not None, "page.html 에 tickLabel 함수가 없다"
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음 — 페이지 스크립트 함수를 돌릴 수 없다")
def test_tick_label_names_month_on_day_marks() -> None:
    """연휴로 짧은 달 다음엔 월 눈금 자리에 일 눈금이 온다 — '4일'만 쓰면 어느 달인지 모른다(10-27 코스피 PNG 재현)."""
    cases = [("2026-01-02", 0), ("2025-11-03", 1), ("2025-11-04", 2), ("2026-06-02", 2)]
    script = _tick_label_js() + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(([t, k]) => tickLabel(t, k))));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    assert json.loads(out) == ["2026년", "11월", "11/4", "6/2"]
