"""텔레그램 문서 발송 — 모델 채널(`CHAT_ID_AIPLAYGROUND`, 결정 D-12).

비밀은 `scripts/notify.sh` 와 같은 규약으로 읽는다: env 파일 = 인자 → `QL_ENV` →
`~/kael-system-v3/.env`, 그 안에서 `BOT_TOKEN` 과 채팅 키 **두 줄만**. 토큰·채팅 ID 는 로그·예외·
반환값 어디에도 싣지 않는다(오류 문자열은 가림 처리). 전송은 표준 라이브러리 `urllib`(curl 없음).
`transport` 를 주입하면 네트워크 대신 그것을 부른다(테스트는 가짜 전송만 쓴다).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
TOKEN_KEY = "BOT_TOKEN"
DEFAULT_CHAT_KEY = "CHAT_ID_AIPLAYGROUND"
CAPTION_MAX = 1024          # 텔레그램 문서 caption 한도
TIMEOUT_S = 60
REDACTED = "***"

# (url, 폼 필드, 파일 이름, 파일 바이트) → 텔레그램 응답 JSON
Transport = Callable[[str, Mapping[str, str], str, bytes], Mapping[str, object]]


def env_path(env_file: str | Path | None = None) -> Path:
    if env_file is not None:
        return Path(env_file)
    if os.environ.get("QL_ENV"):
        return Path(os.environ["QL_ENV"])
    return Path.home() / "kael-system-v3" / ".env"


def read_secrets(path: Path, keys: Sequence[str]) -> dict[str, str]:
    """`KEY=VALUE`(앞 `export ` · 따옴표 허용) 중 keys 만. 없는 파일은 빈 dict."""
    if not path.is_file():
        return {}
    want = set(keys)
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        if k in want:
            out[k] = v.strip().strip("'\"")
    return out


def redact(text: object, secrets: Sequence[str]) -> str:
    """문자열에서 비밀 값을 가린다(긴 것부터 — 토큰 안에 채팅 ID 가 끼어 있어도 안전)."""
    s = str(text)
    for sec in sorted((x for x in secrets if x), key=len, reverse=True):
        s = s.replace(sec, REDACTED)
    return s


def _multipart(fields: Mapping[str, str], file_name: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                     .encode() + v.encode("utf-8") + b"\r\n")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                 f'filename="{file_name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                 .encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def urllib_transport(url: str, fields: Mapping[str, str], file_name: str,
                     data: bytes) -> Mapping[str, object]:
    """기본 전송 — sendDocument multipart POST. HTTP 오류도 본문 JSON(description)을 돌려준다."""
    body, ctype = _multipart(fields, file_name, data)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — 고정 https
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "description": f"HTTP {e.code}"}


def send_document(path: str | Path, caption: str, *, env_file: str | Path | None = None,
                  chat_key: str = DEFAULT_CHAT_KEY, transport: Transport | None = None,
                  dry_run: bool = False) -> dict[str, object]:
    """문서 하나를 보낸다. 반환 {"ok": bool, "description": str} — 비밀은 담지 않는다.

    dry_run 은 네트워크를 쓰지 않고 파일·비밀 키의 **존재만** 확인한다.
    """
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "description": f"파일 없음: {p}"}
    envp = env_path(env_file)
    secrets = read_secrets(envp, (TOKEN_KEY, chat_key))
    token, chat = secrets.get(TOKEN_KEY, ""), secrets.get(chat_key, "")
    caption = caption[:CAPTION_MAX]
    if dry_run:
        ok = bool(token and chat)
        desc = (f"dry-run — 발송 안 함 · 파일 {p.name}({p.stat().st_size:,} B) · env {envp} · "
                f"{TOKEN_KEY} {'있음' if token else '없음'} · "
                f"{chat_key} {'있음' if chat else '없음'}"
                f" · caption {len(caption)}자")
        log.info("telegram %s", desc)
        return {"ok": ok, "description": desc}
    if not token or not chat:
        missing = [k for k, v in ((TOKEN_KEY, token), (chat_key, chat)) if not v]
        desc = f"비밀 키 없음 {missing} — env {envp}"
        log.warning("telegram %s", desc)
        return {"ok": False, "description": desc}
    url = f"{API}/bot{token}/sendDocument"
    send = transport or urllib_transport
    try:
        resp = send(url, {"chat_id": chat, "caption": caption}, p.name, p.read_bytes())
    except Exception as e:  # noqa: BLE001 — 어떤 예외든 비밀을 가려 반환한다(경로·URL 에 토큰)
        desc = redact(f"전송 예외 {type(e).__name__}: {e}", (token, chat))
        log.warning("telegram %s", desc)
        return {"ok": False, "description": desc}
    ok = bool(resp.get("ok"))
    desc = redact(resp.get("description") or ("sent" if ok else "응답에 ok 없음"), (token, chat))
    log.info("telegram sendDocument %s → %s: %s", p.name, chat_key, "ok" if ok else desc)
    return {"ok": ok, "description": desc}


# ── caption ─────────────────────────────────────────────────────────────────
def _names(top: Sequence[tuple[int, str, str]]) -> str:
    return " · ".join(f"{i}.{name or code}" for i, code, name in top[:5]) or "-"


def caption_daily(date: str, basis: str, spec_id: str, top: Sequence[tuple[int, str, str]],
                  n_ranked: int, n_rows: int) -> str:
    """[모델 점수] 날짜 basis · spec / 상위 5 / 순위·제외·모집단."""
    text = (f"[모델 점수] {date} {basis} · {spec_id}\n"
            f"상위5: {_names(top)}\n"
            f"순위 {n_ranked} · 제외 {n_rows - n_ranked} · 모집단 {n_rows}")
    return text[:CAPTION_MAX]


def caption_weekly(week: str, base_date: str, spec_id: str, top: Sequence[tuple[int, str, str]],
                   n_candidates: int, n_new: int, n_out: int, n_days: int) -> str:
    """[주간 후보] 주 (기준일) · spec / 상위 5 / 후보·신규·이탈·거래일."""
    text = (f"[주간 후보] {week} (기준 {base_date}) · {spec_id}\n"
            f"상위5: {_names(top)}\n"
            f"후보 {n_candidates} · 신규 {n_new} · 이탈 {n_out} · 거래일 판 {n_days}/5")
    return text[:CAPTION_MAX]


__all__ = ["DEFAULT_CHAT_KEY", "Transport", "caption_daily", "caption_weekly", "env_path",
           "read_secrets", "redact", "send_document", "urllib_transport"]
