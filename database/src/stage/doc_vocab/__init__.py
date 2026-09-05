"""문서층 어휘 사전 로더 (DOC_DESIGN v1.1 §2.4) — 사전은 JSON 데이터, 코드는 조회만."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).parent
_UNIT_RE = re.compile(r"단위\s*[:：]?\s*([^)）,\s]+)")
_XBRL_RE = re.compile(r"^\{XBRL\}([A-Z]{2})(?:_([SC]))?(?:\d|_|$)")


@dataclass(frozen=True)
class DocVocab:
    tags: frozenset[str]
    xbrl_prefix: dict[str, str]
    units: dict[str, str | None]
    section_prefix: tuple[tuple[str, str], ...]     # 긴 접두 우선 정렬

    def xbrl_stmt(self, aclass: str) -> tuple[str, str | None]:
        """`{XBRL}IS_S1` → ("IS", "S"). 접미 없으면 scope None, 모르는 접두는 ("?", None)."""
        m = _XBRL_RE.match(aclass)
        if m is None:
            return "?", None
        return self.xbrl_prefix.get(m.group(1), "?"), m.group(2)

    def unit_scale(self, text: str) -> tuple[str, str | None] | None:
        """'(단위 : 천원)' → ("천원", "1000"). 표기가 없으면 None, 모르는 단위는 (원문, None)."""
        m = _UNIT_RE.search(text)
        if m is None:
            return None
        u = m.group(1)
        return u, self.units.get(u)

    def section_kind(self, code: str) -> str | None:
        for prefix, kind in self.section_prefix:
            if code.startswith(prefix):
                return kind
        return None


def load_vocab(root: Path = _HERE) -> DocVocab:
    tags = json.loads((root / "tags.json").read_text(encoding="utf-8"))
    xbrl = json.loads((root / "xbrl_aclass.json").read_text(encoding="utf-8"))
    units = json.loads((root / "units.json").read_text(encoding="utf-8"))
    kinds = json.loads((root / "section_kind.json").read_text(encoding="utf-8"))
    ordered = tuple(sorted(kinds.items(), key=lambda kv: -len(kv[0])))
    return DocVocab(frozenset(tags), dict(xbrl), dict(units), ordered)
