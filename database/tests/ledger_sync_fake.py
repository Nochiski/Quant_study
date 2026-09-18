"""ledger_sync 테스트용 가짜 원격 — 메모리 dict 위의 `RemoteFS` 와 서버 디렉토리 규약 생성기.

서버 데이터 없이 돈다(`.claude/rules/testing.md`). MANIFEST·파티션 규약은 `EQUITY_DESIGN §2` 를
손으로 재현한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ledger_sync.remote import Progress, RemoteEntry, RemoteTransferError


@dataclass
class FakeRemote:
    """`files[절대경로] = bytes`. 디렉토리는 경로 접두로 유도한다."""

    files: dict[str, bytes] = field(default_factory=dict)
    fail_once: set[str] = field(default_factory=set)   # 이 경로의 첫 download 만 실패
    calls: list[tuple[str, str]] = field(default_factory=list)
    mtime: int = 1_700_000_000

    # ── RemoteFS ──────────────────────────────────────────────────────────
    def listdir(self, path: str) -> list[RemoteEntry]:
        self.calls.append(("listdir", path))
        prefix = path.rstrip("/") + "/"
        names: dict[str, RemoteEntry] = {}
        found = False
        for full, data in self.files.items():
            if not full.startswith(prefix):
                continue
            found = True
            rest = full[len(prefix):]
            head, _, tail = rest.partition("/")
            if tail:
                names.setdefault(head, RemoteEntry(head, 4096, self.mtime, True))
            else:
                names[head] = RemoteEntry(head, len(data), self.mtime, False)
        if not found:
            raise FileNotFoundError(f"fake remote dir not found — path={path}")
        return sorted(names.values(), key=lambda e: e.name)

    def read_bytes(self, path: str) -> bytes:
        self.calls.append(("read", path))
        try:
            return self.files[path]
        except KeyError as error:
            raise FileNotFoundError(f"fake remote file not found — path={path}") from error

    def download(self, path: str, local: Path, progress: Progress | None = None) -> int:
        self.calls.append(("download", path))
        if path in self.fail_once:
            self.fail_once.discard(path)
            raise RemoteTransferError(f"injected failure — path={path}")
        data = self.read_bytes(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        if progress is not None:
            progress(len(data), len(data))
        return len(data)

    def close(self) -> None:
        return None

    # ── 서버 규약 생성기 ─────────────────────────────────────────────────
    def put_table(self, layer_root: str, table: str, build_id: str,
                  partitions: dict[str, dict[str, bytes]], hashes: dict[str, str] | None = None,
                  *, keep_builds: list[dict[str, object]] | None = None) -> dict[str, object]:
        """`<layer_root>/<table>/v=<build>/<tail>/<file>` 을 올리고 MANIFEST 를 current 로 바꾼다.

        partitions: {tail(""=whole 또는 "year=2010"): {파일명: bytes}}.
        hashes: tail → content_hash (없으면 파일 바이트 길이로 만든 가짜 해시).
        """
        record_parts: list[dict[str, object]] = []
        for tail, files in partitions.items():
            rel = f"v={build_id}" + (f"/{tail}" if tail else "")
            for name, data in files.items():
                self.files[f"{layer_root}/{table}/{rel}/{name}"] = data
            h = (hashes or {}).get(tail) or f"{len(files)}:{sum(len(d) for d in files.values()):x}"
            record_parts.append({"path": rel, "n_rows": len(files), "content_hash": h})
        record: dict[str, object] = {
            "build_id": build_id, "snapshot_id": "", "rules_version": "e1.15.0",
            "built_at_utc": "2026-09-18T13:30:50+00:00", "n_rows": len(record_parts),
            "content_hash": "x", "partitions": record_parts, "gates": [], "inputs": {},
        }
        manifest_path = f"{layer_root}/{table}/MANIFEST.json"
        existing = json.loads(self.files[manifest_path]) if manifest_path in self.files else {}
        builds = [b for b in (keep_builds if keep_builds is not None
                              else existing.get("builds", []))
                  if b.get("build_id") != build_id] + [record]
        manifest = {"table": table, "current_build": build_id, "keep": 3, "builds": builds[-3:]}
        self.files[manifest_path] = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
        return manifest

    def put_meta(self, layer_root: str, name: str, data: bytes) -> None:
        self.files[f"{layer_root}/{name}"] = data

    def paths_under(self, prefix: str) -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix.rstrip("/") + "/"))


def rel(path: str) -> str:
    return PurePosixPath(path).name
