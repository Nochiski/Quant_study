"""SSE 스트림의 공통 규칙 — 어시스턴트 턴 스트림과 백테스트 run 스트림이 같은 프레임·리듬을 쓴다."""

from __future__ import annotations

import json

from fastapi.encoders import jsonable_encoder

__all__ = ["SSE_KEEPALIVE_FRAME", "SSE_KEEPALIVE_SECONDS", "SSE_POLL_SECONDS", "sse_frame"]

# 열린 스트림이 조용해도 15초마다 주석 한 줄을 보낸다(어시스턴트 spec D6). 중간의 프록시·
# 브라우저가 아무 바이트도 오지 않는 연결을 끊어 버리면, 클라이언트는 스트림이 끝난 것으로
# 착각하지 않고 재연결을 반복하게 된다. 주석 프레임은 SSE 파서가 무시하므로 이벤트 번호를
# 건드리지 않는다.
SSE_KEEPALIVE_SECONDS = 15.0
SSE_KEEPALIVE_FRAME = ": keepalive\n\n"
# 이벤트 저장소를 다시 읽는 간격. 두 스트림 모두 러너가 쌓은 이벤트를 폴링한다(spec D3: 러너는
# 저장소가 정본).
SSE_POLL_SECONDS = 0.05


def sse_frame(*, sequence: int, event: str, data: object) -> str:
    """저장된 이벤트 하나를 프레임 하나로 — `id` 는 재개 기준 sequence, `data` 는 한 줄 JSON."""
    body = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event}\ndata: {body}\n\n"
