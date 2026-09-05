from __future__ import annotations

import re
from typing import Final

from pydantic import BeforeValidator

_CANONICAL_UNSIGNED_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _parse_canonical_page_integer(value: object) -> int:
    """Keep HTTP pagination lexical input aligned with generated clients.

    FastAPI/Pydantic normally coerce float-like, signed, padded, and underscored
    strings to ``int``. The wire contract is narrower: canonical unsigned decimal
    text only. Range ownership remains in ``PageRequest`` and each route's schema.
    """

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _CANONICAL_UNSIGNED_DECIMAL.fullmatch(value):
        return int(value)
    raise ValueError("pagination values must be canonical unsigned decimal integers")


CANONICAL_PAGE_INTEGER_VALIDATOR: Final = BeforeValidator(_parse_canonical_page_integer)
