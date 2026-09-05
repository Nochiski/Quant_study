"""ingest_log 를 새 스키마로 옮긴다. reprt_code · fs_div 축 추가.

구 스키마 PK (name, corp_code, bsns_year) 는 사업/반기/1분기/3분기를 구분하지 못해
첫 보고서를 받은 시점에 그 corp-year 가 완료로 잠긴다. 분기재무를 받으려면 축이 필요하다.

기존 행은 전부 사업보고서(11011)로 수집됐으므로 그 값으로 승계한다.
corp 축(bsns_year 없음) 행은 '' 로 채운다 — NULL 이면 PK 안에서 서로 다른 값이 되어
INSERT OR REPLACE 가 동작하지 않는다.

멱등하다. 이미 새 스키마면 아무것도 하지 않는다.
"""
import os, sys, sqlite3

BASE = os.environ.get("QL_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = f"{BASE}/data/raw/dart.db"

NEW_DDL = """CREATE TABLE ingest_log(
  name TEXT NOT NULL, corp_code TEXT NOT NULL,
  bsns_year TEXT NOT NULL DEFAULT '', reprt_code TEXT NOT NULL DEFAULT '',
  fs_div TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, n_rows INTEGER NOT NULL, note TEXT, ts TEXT NOT NULL,
  PRIMARY KEY (name, corp_code, bsns_year, reprt_code, fs_div))"""


def main() -> None:
    if not os.path.exists(DB):
        print(f"  DB 없음: {DB}"); return
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")

    have = {r[1] for r in con.execute("PRAGMA table_info(ingest_log)")}
    if not have:
        print("  ingest_log 가 없다 — 백필이 만들 것이다"); con.close(); return
    if "reprt_code" in have and "fs_div" in have:
        n = con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
        print(f"  이미 새 스키마다 ({n:,}행) — 변경 없음"); con.close(); return

    before = con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
    print(f"  구 스키마 {before:,}행 → 마이그레이션")

    # corp 축인지 corp_year 축인지는 bsns_year 유무로 갈린다.
    # 기존 수집은 전부 사업보고서였으므로 corp_year 행만 11011 을 채운다.
    con.executescript(f"""
      ALTER TABLE ingest_log RENAME TO ingest_log_old;
      {NEW_DDL};
      INSERT INTO ingest_log (name, corp_code, bsns_year, reprt_code, fs_div,
                              status, n_rows, note, ts)
      SELECT name, corp_code,
             COALESCE(bsns_year, ''),
             CASE WHEN bsns_year IS NULL OR bsns_year = '' THEN '' ELSE '11011' END,
             '',
             status, n_rows, note, ts
      FROM ingest_log_old;
    """)
    after = con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]

    if after != before:
        con.execute("DROP TABLE ingest_log")
        con.execute("ALTER TABLE ingest_log_old RENAME TO ingest_log")
        con.commit(); con.close()
        raise RuntimeError(
            f"행수 불일치로 롤백했다 — before={before} after={after} db={DB}")

    con.execute("DROP TABLE ingest_log_old")
    con.commit()
    dist = con.execute(
        "SELECT reprt_code, COUNT(*) FROM ingest_log GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print(f"  완료 {after:,}행 · reprt_code 분포 " +
          " · ".join(f"{rc or '(corp축)'}={n:,}" for rc, n in dist))
    con.close()


if __name__ == "__main__":
    main()
