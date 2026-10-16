from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from strategy_workbench.application.signal_research.facade.ports import SnapshotProof


def _require_runtime_checkout(repo_root: Path) -> None:
    """실행 중인 모듈과 이후 하위 import의 출처를 S01이 검증할 소스에 묶는다."""
    source_root = repo_root / "backend" / "src"
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] not in {"strategy_workbench", "backtest_engine"}:
            continue
        expected = source_root.joinpath(*name.split("."))
        spec = getattr(module, "__spec__", None)
        locations = getattr(spec, "submodule_search_locations", None)
        package_paths = getattr(module, "__path__", None)
        if locations is not None:
            expected_file = expected / "__init__.py"
            # __path__는 후속 import, spec은 이미 선택된 loader의 출처를 각각 확인한다.
            for paths in (locations, package_paths):
                if paths is None or tuple(Path(path).resolve() for path in paths) != (
                    expected.resolve(),
                ):
                    raise ValueError(
                        "연구 실행 패키지 경로가 검증 저장소와 다릅니다. "
                        f"module={name} expected={expected} actual={paths} repo_root={repo_root}; "
                        "해당 소스 checkout의 editable 설치로 실행하세요."
                    )
        else:
            expected_file = expected.with_suffix(".py")
        for origin in (getattr(spec, "origin", None), getattr(module, "__file__", None)):
            if not isinstance(origin, str) or Path(origin).resolve() != expected_file.resolve():
                raise ValueError(
                    "연구 실행 코드 출처가 검증 저장소와 다릅니다. "
                    f"module={name} expected={expected_file} actual={origin} "
                    f"repo_root={repo_root}; "
                    "해당 소스 checkout의 editable 설치로 실행하세요."
                )


class ResearchSnapshotGuard:
    """검증 규칙을 복제하지 않고 S01의 코드·환경·보존 파일 전체 검증을 실행한다."""

    def __init__(self, preserved_equity_root: Path, repo_root: Path) -> None:
        self._root = preserved_equity_root.resolve()
        self._repo = repo_root.resolve()

    def verify(self) -> SnapshotProof:
        _require_runtime_checkout(self._repo)
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
