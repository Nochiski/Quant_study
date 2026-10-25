"""compat — v3 `quant.db` 호환 계층 (플랜 `docs/plans/2026-09-24-v3-merge.md` §3 ·§5 M1).

우리 equity/stage 판에서 v3 후단(스코어링·엑셀·브리핑·리서치센터·가설·unitelegram)이 읽는
9표를 채운다. **한시 계층**이다 — 표마다 `mappings.TableMapping.retire_when` 이 만료 조건을
적어 두고, 소비자가 model/equity 직독으로 옮겨지면 exporter 에서 빠진다. 전부 빠지면
`quant.db` 는 플랜 v2 D.2 규약대로 동결한다.

M1~M3 대상은 별도 파일 `data/compat/quant.db`, M4 컷오버부터 v3 `~/kael-system-v3/data/quant.db`
제자리 upsert(결정 D-2).
"""
from __future__ import annotations

from .quant_db import CompatEmptyError, CompatError, CompatSchemaError, ExportResult, export

__all__ = ["CompatEmptyError", "CompatError", "CompatSchemaError", "ExportResult", "export"]
