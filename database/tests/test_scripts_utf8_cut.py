"""database/scripts 의 요약 자르기 `cut -c1-N` 은 끝에서 잘린 글자 조각을 버린다(#630 CI).

GNU coreutils `cut -c` 는 멀티바이트를 모르고 바이트로 자른다(서버·CI). 요약 끝이 한글·선 문자 중간에서
끊기면 알림 본문(notify.log)이 깨진 UTF-8 이 된다. 그래서 자르기마다 `| iconv -c -f UTF-8 -t UTF-8` 을 붙인다.
동작은 `test_daily_ledger_sh.py`·`test_notify_sh.py` 의 재현 테스트가 보고, 이 파일은 새 스크립트가 같은 꼴을
iconv 없이 다시 들이지 않는지만 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_CUT = re.compile(r"cut -c1-\d+")
_SAFE_TAIL = "| iconv -c -f UTF-8 -t UTF-8"


def test_요약_cut_은_모두_iconv_로_끝_글자_조각을_버린다() -> None:
    unsafe = []
    for path in sorted(SCRIPTS.glob("*.sh")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or "md5sum" in line:   # 주석 · 16진수 지문(ASCII) 자르기
                continue
            for m in _CUT.finditer(line):
                if not line[m.end():].lstrip().startswith(_SAFE_TAIL):
                    unsafe.append(f"{path.name}:{n}")
    assert not unsafe, f"iconv 없이 바이트로 자르는 요약 — {unsafe}"
