from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from strategy_workbench.application.signal_research.facade.ports import SnapshotProof


class ResearchSnapshotGuard:
    """검증 규칙을 복제하지 않고 S01의 코드·환경·보존 파일 전체 검증을 실행한다."""

    def __init__(self, preserved_equity_root: Path, repo_root: Path) -> None:
        self._root = preserved_equity_root.resolve()
        self._repo = repo_root.resolve()

    def verify(self) -> SnapshotProof:
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONPATH"] = str(self._repo / "database" / "src")
        checked = subprocess.run(
            [
                sys.executable,
                "-m",
                "equity.research_snapshot",
                "verify",
                "--root",
                str(self._root),
                "--repo-root",
                str(self._repo),
            ],
            cwd=self._repo,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if checked.returncode != 0:
            raise ValueError(f"S01 snapshot verification failed: {checked.stderr.strip()}")
        raw = json.loads(checked.stdout)
        if not isinstance(raw, dict) or raw.get("root") != str(self._root):
            raise ValueError("S01 verifier returned an unexpected root")
        ledger_id = raw.get("ledger_snapshot_id")
        contract_hash = raw.get("contract_sources_sha256")
        if (
            not isinstance(ledger_id, str)
            or not ledger_id
            or not isinstance(contract_hash, str)
            or not contract_hash
        ):
            raise ValueError("S01 verifier did not return both snapshot identities")
        with (self._root / "_research_snapshot.json").open("rb") as descriptor:
            descriptor_hash = hashlib.file_digest(descriptor, "sha256").hexdigest()
        return SnapshotProof(
            os.path.relpath(self._root, self._repo), ledger_id, contract_hash, descriptor_hash
        )
