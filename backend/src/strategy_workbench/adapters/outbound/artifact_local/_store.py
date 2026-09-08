from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from strategy_workbench.application.backtest_run.facade.ports import ArtifactCommit
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        target = (self._root / result.manifest.run_id).resolve()
        if target.parent != self._root:
            raise ValueError("run id escapes artifact root")
        if target.exists():
            raise FileExistsError(f"run artifact already exists: {result.manifest.run_id}")
        staging = (self._root / f".{result.manifest.run_id}.{uuid4().hex}.tmp").resolve()
        if staging.parent != self._root:
            raise ValueError("staging path escapes artifact root")
        staging.mkdir()
        try:
            payload = json.dumps(
                _json_value(result),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            result_path = staging / "result.json"
            with result_path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
            manifest_payload = json.dumps(
                _json_value(result.manifest),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with (staging / "manifest.json").open("xb") as stream:
                stream.write(manifest_payload)
                stream.flush()
            staging.replace(target)
        except BaseException:
            if staging.exists() and staging.parent == self._root:
                shutil.rmtree(staging)
            raise
        return ArtifactCommit(
            uri=(target / "result.json").as_uri(),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    def discard(self, run_id: str) -> None:
        """Delete one exact committed run after application-level cancellation wins."""

        target = (self._root / run_id).resolve()
        if target.parent != self._root:
            raise ValueError("run id escapes artifact root")
        if target.exists():
            shutil.rmtree(target)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("artifact values must be finite or null")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported artifact value: {type(value).__name__}")
