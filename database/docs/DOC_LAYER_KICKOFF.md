# 문서층(L1) 착수 노트 — DART 보고서 ZIP 처리 (2026-09-03, stage 세션 인계)

> stage 설계 §0 이 범위 밖으로 둔 "문서층 L1(ZIP 본문 파싱)" 트랙의 시작 문서. 읽는 순서: 이 문서 → `STAGE_HANDOFF.md` §4(공시 정정 신호 항목)
> → `STAGE_DESIGN.md` §0·§7(정정 체인)·§10(doc_index 실측) → `EQUITY_KICKOFF.md`(equity 4단계 `disclosure_version` 이 이 트랙의 첫 고객).

## 0. 실물 — 무엇을 받아 뒀나 (09-03 실측)
- 수집기 `src/backfill_docs.py`, 09-01 04:55 → 09-03 07:34 KST, 2,499분. 대상 174,309건 = P1 분할·합병 1,403 + P2 사업보고서 52,329 + P3 반기·분기 120,577.
  저장 171,179건(98.2%)·26.56GB, 미저장 3,130건 = `http_status=014`(DART "파일 없음") 전부 정정 공시([기재정정]분기 1,621·반기 729·[첨부정정]사업 426 등, 2015~2019 집중).
- 파일: `~/quant-ledger/data/raw/documents/{yyyy}/{rcept_no}.zip` (원장 원칙 — 바이트 그대로, 불변). 메타: `dart.db`의 `doc_store`(rcept_no·bytes·sha256·n_files·zip_ok·http_status·fetched_at).
  stage 인덱스: `stg_doc_index` 174,309행(`zip_ok=false` 3,130 포함) — `data/stage/stg_doc_index/MANIFEST.json`.
- ZIP 내용: DART 자체 XML. 본문 `접수번호.xml`(삼성전자 사업보고서 3~8MB) + 첨부 `_00760`·`_00761`(감사보고서류). 파일 수 분포 1개 129,227 · 3개 29,065 · 2개 12,887. 연도별 평균 압축 크기 0.08MB(2010)→0.22MB(2025).
- **스키마 세대**: `dart3.xsd`(≤2020, FORMULA-VERSION 2.8~3.9) → `dart4.xsd`(2025~, 6.x). 태그 구조가 바뀌므로 연대별 파서 확인 필수.
- **인코딩 함정**: 2020년 이전 XML 은 헤더가 `encoding="utf-8"` 인데 실제 바이트는 cp949. 헤더를 믿지 말고 바이트로 판정.
- 정정 규모(원장 실측 09-03): 정기보고서 그룹(회사×종류×기간) 181,106 중 정정 있음 20,759(11.5%). 정정 접수는 원본 후 7일 이내 32%, 90일 이후 30%, 1년 이후 10%. 원본 ZIP 153,223/153,228 확보, 정정 ZIP 18,013/21,138.

## 1. 왜 필요한가 — stage·API 가 못 주는 것
1. **정정 → 원본 접수번호 링크**(XML 첫 장 "정정신고(보고)" 표). DART 목록 API 에 없다. equity `disclosure_version`·`has_correction` 의 확정 근거.
2. **as-reported 재무 원본 판본**. `stg_fin`(DART API)은 정정 반영 최신값만(전량 restated_unknown). 원본 숫자는 XML 표에만 있다 → PIT 재무.
3. API 에 없는 표·텍스트: 주석, 주주현황, 임원·종업원, 사업의 개요 — 텍스트·이벤트 팩터 재료.
4. 비12월 결산·구양식 기간 라벨 해석.

## 2. 처리 방식 — 원칙과 단계
원칙은 stage 와 같다: 원문 불변 · 산출은 `~/quant-ledger/data/doc/` 에 접수번호 키 parquet · `stg_doc_index` 와 연결 · 게이트(파싱 성공률, 세대별 표본 일치, 검산축) · 재현성 · 문서 실측 기록. 표준 `xml.etree`(스트리밍 `iterparse`)면 충분 — 새 의존성 금지(코드 규칙). 압축 해제 총량이 수백 GB 라 서버에서 스트리밍, 단일 파일을 메모리에 다 올리지 않는다.

| 단계 | 산출 | 방법·게이트 |
|---|---|---|
| L1-0 표본 검토 | 연대별(2010·2013·2016·2020·2024) 각 5건의 태그 구조·인코딩·섹션 목차 실측 기록 | 파서 전 선행 조건(설계 §0). 서식 세대 수와 섹션 태그 어휘를 확정 |
| L1-1 문서 인덱스 | `doc_section`(rcept_no × 섹션 경로 × 표 위치·행수) + `correction_link`(정정 rcept_no → 원본 rcept_no, 정정 사유) | 목차만 뽑는 가벼운 패스. 게이트: `correction_link` 커버리지 = 정정 ZIP 18,013 전건, 원본이 doc_store 에 존재하는 비율 |
| L1-2 표 추출 | `fin_asreported`(손익·재무상태·현금흐름 핵심 계정, 원본 판본, 단위·통화) | 인덱스로 표 위치를 찾아 그 표만 파싱. 검산축: 정정 없는 보고서는 `stg_fin` 최신값과 100% 일치해야 함 |
| L1-3 텍스트 | 섹션 본문 parquet(사업의 개요·주석) | 팩터 요구 확정 후 |

## 3. 첫 세션에서 바로 할 일
1. `git fetch && git checkout -b doc/l1-sample origin/main`. 서버 ZIP 은 읽기 전용(`zipfile` + `iterparse`), 로컬엔 원장 사본 없음.
2. L1-0: 연대별 표본 25건을 열어 (세대, 인코딩, 최상위 섹션 태그·제목 목록, 정정신고 표 위치, 재무제표 표 위치)를 표로 기록 → `docs/DOC_DESIGN.md` v1 + 검수 1회.
3. 승인 후 L1-1(`correction_link` 먼저 — equity 4단계가 기다린다) TDD.

## 4. 참고 경로
- 코드 규칙: `.claude/rules/*.md`(ruff 100컬럼·한글 폭 2, pyright basic, 새 라이브러리 금지, 테스트는 손계산 픽스처)
- stage 빌더 재사용 후보: `src/stage/manifest.py`(원자 교체·keep=3), `snapshot.py`, `gates.py` 골격. 파서는 `src/stage/parsers.py` 의 blob 언네스트 계약(ParseResult·metrics·G8 등식) 참고.
- 서버: `ssh kael-server`, `~/quant-ledger`, `.venv/bin/python`(3.12, duckdb 1.5.5), 배포는 rsync. 크론은 daily_wise 06:00 KST 하나뿐.
