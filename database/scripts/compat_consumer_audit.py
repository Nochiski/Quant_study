#!/usr/bin/env python3
"""v3 트리를 정적으로 훑어 quant.db 표를 읽는 소비자(표·열·파일:줄·읽기 방식)를 뽑는다.

플랜 `docs/plans/2026-09-24-v3-merge.md` T1.4(GAP-1·GAP-2)의 1회성 도구다.
완전한 SQL 파서가 아니라 정규식 근사다 — SQL 은 파이썬 문자열 리터럴 안에만 있다고 보고,
`FROM|JOIN|UPDATE|INTO <표>` 를 찾은 뒤 같은 리터럴의 SELECT 절·WHERE 절에서 열 이름을
긁는다. 결과는 사람이 원문을 확인한 뒤 쓴다.

사용:
    uv run --project backend python database/scripts/compat_consumer_audit.py \
        --v3-root <스냅샷 경로> --out -
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# 호환 계층이 채워야 하는 v3 quant.db 표 (플랜 §1-5·§4)
TARGET_TABLES = (
    "daily_prices",
    "stocks",
    "investor_detail_flows",
    "consensus_revision_daily",
    "consensus_revision_compare",
    "consensus_annual",
    "financial_summary",
    "score_history",
    "score_history_v2",
    "stock_info",
    "analyst_opinions",
    "major_shareholders",
)

# 기본 제외: 테스트·백업본·1회성 분석 스크립트 (`**` = 경로 아무데나)
DEFAULT_EXCLUDES = (
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*.bak*",
    "**/insight.bak*/**",
    "/scripts/_analysis_*",
    "/scripts/_bt_*",
    "/scripts/_spike_*",
)
# --include-excluded 에서도 빼는 것(노이즈)
ALWAYS_EXCLUDES = (
    "**/.venv*/**",
    "**/node_modules/**",
    "**/.git/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
)

# 긴 이름부터 — score_history 가 score_history_v2 를 가려서는 안 된다
_TABLE_ALT = "|".join(sorted(TARGET_TABLES, key=len, reverse=True))
_ALIAS_STOP = (
    r"ON|WHERE|GROUP|ORDER|LIMIT|LEFT|RIGHT|INNER|OUTER|CROSS|JOIN|USING"
    r"|SET|VALUES|SELECT|UNION|HAVING|WINDOW|AND|OR"
)
RE_TABLE = re.compile(
    r"\b(?P<kw>FROM|JOIN|UPDATE|INTO)\s+[\"'`\[]?(?P<table>" + _TABLE_ALT + r")[\"'`\]]?\b"
    r"(?:\s+(?:AS\s+)?(?P<alias>(?!(?:" + _ALIAS_STOP + r")\b)[A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)
# 파이썬 문자열 리터럴(삼중 우선). SQL 은 이 안에만 있다고 본다
RE_STRING = re.compile(
    r"(?P<q>\"\"\"|''')(?P<body3>.*?)(?P=q)"
    r"|\"(?P<body1>(?:[^\"\\\n]|\\.)*)\""
    r"|'(?P<body2>(?:[^'\\\n]|\\.)*)'",
    re.DOTALL,
)
RE_GLUE = re.compile(r"^[\s+frbuFRBU]*$")
RE_SELECT = re.compile(r"\bSELECT\b", re.IGNORECASE)
RE_CLAUSE = re.compile(
    r"\b(?:WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|ON|USING|SET)\b", re.IGNORECASE
)
RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RE_QUALIFIED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
RE_RO = re.compile(r"mode=ro")
RE_OPENS = re.compile(r"sqlite3\.connect|get_connection\(")

# 열 후보에서 걸러낼 SQL 키워드·함수명
SQL_NOISE = {
    "select", "distinct", "from", "join", "left", "right", "inner", "outer", "cross",
    "on", "using", "where", "group", "order", "by", "having", "limit", "offset", "as",
    "and", "or", "not", "null", "is", "in", "like", "between", "case", "when", "then",
    "else", "end", "asc", "desc", "union", "all", "with", "over", "partition", "set",
    "values", "into", "update", "insert", "replace", "conflict", "do", "nothing",
    "count", "sum", "avg", "min", "max", "abs", "round", "coalesce", "ifnull", "nullif",
    "cast", "date", "julianday", "strftime", "substr", "length", "upper", "lower",
    "printf", "row_number", "rank", "dense_rank", "lag", "lead", "json_extract",
    "group_concat", "real", "integer", "text", "float", "int", "exists", "desc_",
}


@dataclass
class Hit:
    table: str
    path: str
    line: int
    keyword: str
    columns: list[str] = field(default_factory=list)
    access: str = "-"

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _literals(text: str) -> Iterator[tuple[int, str]]:
    """(본문 시작 오프셋, 본문). 인접 리터럴(암묵 연결·`+`)은 하나로 합친다."""
    cur_off: int | None = None
    cur_body = ""
    prev_end = -1
    for m in RE_STRING.finditer(text):
        for name in ("body3", "body1", "body2"):
            body = m.group(name)
            if body is None:
                continue
            off = m.start(name)
            if cur_off is not None and RE_GLUE.match(text[prev_end : m.start()]):
                cur_body += " " + body
            else:
                if cur_off is not None and cur_body.strip():
                    yield cur_off, cur_body
                cur_off, cur_body = off, body
            prev_end = m.end()
            break
    if cur_off is not None and cur_body.strip():
        yield cur_off, cur_body


def _sql_names(chunk: str, alias: str | None, foreign_aliases: set[str]) -> list[str]:
    """열 후보. 우리 별칭 한정자는 항상, 무한정 이름은 다른 별칭이 없을 때만."""
    names: list[str] = []
    if "*" in re.sub(r"/\*.*?\*/", "", chunk):
        names.append("*")
    if alias:
        names += [
            b for a, b in RE_QUALIFIED.findall(chunk) if a.lower() == alias.lower()
        ]
    stripped = RE_QUALIFIED.sub(" ", chunk)
    stripped = re.sub(r"'[^']*'", " ", stripped)
    stripped = re.sub(r"\?|:[A-Za-z_][A-Za-z0-9_]*|\{[^}]*\}", " ", stripped)
    if not foreign_aliases:
        names += [n for n in RE_IDENT.findall(stripped) if n.lower() not in SQL_NOISE]
    out: dict[str, None] = {}
    lowered_tables = {t.lower() for t in TARGET_TABLES}
    for n in names:
        if n.lower() in SQL_NOISE or n.lower() in lowered_tables:
            continue
        out.setdefault(n, None)
    return list(out)


def _columns_in(sql: str, m: re.Match[str]) -> list[str]:
    alias = m.group("alias")
    kw = m.group("kw").upper()
    aliases = {a.lower() for a, _ in RE_QUALIFIED.findall(sql)}
    aliases -= {t.lower() for t in TARGET_TABLES}
    foreign = aliases - ({alias.lower()} if alias else set())
    cols: list[str] = []

    if kw in {"FROM", "JOIN"}:
        sels = list(RE_SELECT.finditer(sql, 0, m.start()))
        if sels:
            cols += _sql_names(sql[sels[-1].end() : m.start()], alias, foreign)
        tail = sql[m.end() :]
        parts = RE_CLAUSE.split(tail)
        for part in parts[1:]:
            cols += _sql_names(part, alias, foreign)
    else:  # INSERT INTO t (cols…) / UPDATE t SET col = …
        tail = sql[m.end() :]
        paren = re.match(r"\s*\(([^)]*)\)", tail)
        if paren:
            cols += _sql_names(paren.group(1), alias, foreign)
        else:
            cols += _sql_names(tail[:600], alias, foreign)

    out: dict[str, None] = {}
    for c in cols:
        out.setdefault(c, None)
    return list(out)


def _glob_to_re(pattern: str) -> str:
    """`**` = 경로 구분자 포함 아무거나, `*` = 한 경로 조각 안에서 아무거나."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "^" + "".join(out) + "$"


def _excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(re.match(_glob_to_re(p), "/" + rel) for p in patterns)


def scan(root: Path, patterns: tuple[str, ...], suffixes: tuple[str, ...]) -> list[Hit]:
    hits: list[Hit] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel = str(path.relative_to(root))
        if _excluded(rel, patterns):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not any(t in text for t in TARGET_TABLES):
            continue
        if RE_RO.search(text):
            access = "ro"
        elif RE_OPENS.search(text):
            access = "rw"
        else:
            access = "-"  # 연결을 인자로 받아 쓴다
        for off, body in _literals(text):
            for m in RE_TABLE.finditer(body):
                line = text.count("\n", 0, off + m.start()) + 1
                hits.append(
                    Hit(
                        table=m.group("table"),
                        path=rel,
                        line=line,
                        keyword=m.group("kw").upper(),
                        columns=_columns_in(body, m),
                        access=access,
                    )
                )
    return hits


def render(hits: list[Hit]) -> str:
    lines: list[str] = []
    lines.append("# v3 quant.db 소비자 컬럼 감사 (정적 스캔)\n")
    lines.append(
        f"- 참조 {len(hits)}건 · 표 {len({h.table for h in hits})}개 · "
        f"파일 {len({h.path for h in hits})}개"
    )
    lines.append("- 읽기 방식: `ro` = 파일에 `mode=ro` 있음, `rw` = 쓰기 가능 연결, "
                 "`-` = 연결을 인자로 받음\n")

    lines.append("\n## 표별 참조 열 합집합\n")
    lines.append("| 표 | 참조 | 파일 | 참조 열(합집합, 근사) |")
    lines.append("|---|---|---|---|")
    for table in TARGET_TABLES:
        rows = [h for h in hits if h.table == table]
        if not rows:
            lines.append(f"| {table} | 0 | 0 | (참조 없음) |")
            continue
        cols: dict[str, None] = {}
        for h in rows:
            for c in h.columns:
                cols.setdefault(c, None)
        lines.append(
            f"| {table} | {len(rows)} | {len({h.path for h in rows})} | "
            f"{', '.join(sorted(cols)) or '(없음)'} |"
        )

    lines.append("\n## 참조 위치\n")
    lines.append("| 표 | 파일:줄 | 구문 | 방식 | 참조 열(근사) |")
    lines.append("|---|---|---|---|---|")
    for h in sorted(hits, key=lambda x: (x.table, x.path, x.line)):
        cols = ", ".join(h.columns[:18]) or "(없음)"
        lines.append(f"| {h.table} | `{h.location}` | {h.keyword} | {h.access} | {cols} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v3 quant.db 소비자 컬럼 감사")
    ap.add_argument("--v3-root", required=True, type=Path, help="v3 코드 트리(읽기 전용)")
    ap.add_argument("--out", default="-", help="'-' 이면 stdout, 아니면 파일 경로")
    ap.add_argument(
        "--include-excluded",
        action="store_true",
        help="기본 제외(테스트·.bak·_analysis_/_bt_/_spike_ 스크립트)도 훑는다",
    )
    ap.add_argument("--suffix", action="append", default=None, help="훑을 확장자(기본 .py)")
    args = ap.parse_args(argv)

    root = args.v3_root.expanduser().resolve()
    if not root.is_dir():
        print(f"v3-root 없음: {root}", file=sys.stderr)
        return 2

    patterns = ALWAYS_EXCLUDES if args.include_excluded else ALWAYS_EXCLUDES + DEFAULT_EXCLUDES
    suffixes = tuple(args.suffix) if args.suffix else (".py",)

    hits = scan(root, patterns, suffixes)
    text = render(hits)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(hits)} refs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
