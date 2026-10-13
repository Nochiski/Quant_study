# ERD와 Workbench 소비 경계 감사 (2026-10-13)

## 범위와 정본

검토 기준은 LANG2 #431–436 병합 후 main `f5e41d9837efd2afa346f22f7416eb2431992468`이다.
main의 stage 규칙은 `2.3.0`, equity 규칙은 `e1.19.0`이다. 운영 브랜치
`track/cutover`는 `aebc8a095f72623d208800877e5db32c740edc58`을 읽기 전용으로 비교했다.
이 브랜치의 `model.py`는 `e1.29.0`이며 README의 `e1.27.0`보다 앞선다.
동일한 과거 규칙 버전 문자열이 다른 브랜치에서 다른 코드를 가리킬 수 있으므로 SHA도 함께 고정한다.

- `EQUITY_DESIGN.md` v1.2, `EQUITY_GATES.md`, `EQUITY_FIELD_MAP.md`가 테이블·뷰·필드 의미 계약이다.
- stage 규칙은 `STAGE_DESIGN.md` v2.2와 `src/stage/rules_*.py`의 `ColumnRule.name`을 대조한다.
  `STAGE_SPEC.md`의 취소된 문장과 8월 실측치는 현행 규칙을 대체하지 않는다.
- `START_HERE.md`의 9월 8일 e1.14 통계는 과거 스냅샷이다. 운영 상태는 각 SHA의
  README 및 `plans/2026-09-11-daily-incremental-v2.md` 상태 블록을 기준으로 읽었다.
- `EQUITY_KICKOFF.md`는 최초 ERD v0.1을 재상장 구간, WISE 단위/PIT,
  공시일과 결산일 분리로 대체한 근거다. 최초 ERD 그림 자체는 이 체크아웃에서 찾지 못했다.
- 별도 quant-data의 보존 스냅샷·미확정 WORKBOOK 계약을 현재 소비자 명세로 사용하지 않았다.
  스코어 가중치, horizon, sector 계약을 임의 확정하지 않았다.

운영 배포·공급자 호출·실원장 재생성·비밀키·외부 발송은 수행하지 않았다.
track 전체 병합은 이 감사에 포함하지 않는다.

## ERD → 코드 → 소비 대응표

| 축 | 선언·변환 owner | 조회·소비 owner | 대조 결과와 검증 근거 |
|---|---|---|---|
| 원천과 자연키 | 원천별 수집기 SQLite DDL, stage `TableRule.natural_key` | stage build/manifest | 원문 수집과 타입·단위 변환 분리. 실제 운영 DDL/데이터 동등성은 미검증 |
| 타입·단위·NULL | `stage/rules_krx.py`, `rules_kiwoom.py`, `rules_kis.py`, `rules_dart.py`, `rules_wise.py`, `build.py` | equity SQL | 가격 부호와 변화량 부호, 0 결측, WISE 행 단위를 구분. stage 원천별 fixture 스위트로 검증 |
| 종목·재상장 | S01/S02, `security_span.sql`, `universe_daily.sql` | `_Row.security_id`, 두 조회 포트 | `(ticker, span_seq)`가 신원. GRID 랙에서 구간 누출을 발견·수정 |
| 법인↔종목 | `corp_ticker.sql`, S01/S02 | corp LATEST 원천 | 재무/임원지분 fan-out 유지. 현재 스냅샷 매핑·우선주 DPS 한계는 FIELD_MAP에 이미 명시 |
| 가격·주식수·거래대금 | S04 `price_daily.sql` | `FIELD_SPECS`, raw bar feed | raw KRW와 거래량 주 단위 유지. 시총·주식수 랙 1, 가격 랙 0은 profile owner |
| 수정주가·사건 | S06/S23, `price_adj_daily.sql`, `views.py` | `v_adj_close`, `v_unfolded_event`, backtest data | 수정가 팩터 경로와 원주가+보유수량 사건 경로 분리. track 가격 마스킹 쟁점은 아래 별도 기록 |
| 유니버스·정책 | S03/S21 `universe_daily`, `universe_policy` | raw `universe_member`, portfolio pipeline | 과거 세션의 구간/상태를 조회. 데이터층에 팩터 선정 정책을 추가하지 않음 |
| 수급·공매도·대차 | S08–S10 격자, `fill_kind`와 로그 증거 | `_cell_kind`, RawFieldValue | Kiwoom short의 `src_omitted`만 0. 다른 결측/미수집은 유지; 기존 #371 회귀 확인 |
| 신용잔고 | S10 `v_credit_balance` | profile 랙 3, MASKED | 원장 마스크를 어댑터가 전달. 소비자의 결측 보간으로 되살리지 않음 |
| 재무 PIT·정정·TTM | S11/S12, `v_fin_latest` | LATEST `period_frontier`, `ttm_*_available_date` | 내용일과 공개일 분리, 늦은 정정 이전 TTM 결측 유지. 원본 없는 restated 선택편향은 기존 #310 한계 |
| WISE 컨센서스 | S17 `consensus_daily.sql`, `v_consensus` | FY1 LATEST, 필드별 단위 | earliest fetched vintage·공개일 컷오프·FY1·억원/원 구분. 최신 수집본으로 과거를 덮지 않음 |
| 의견·배당·지분 | S15/S16/S18 | LATEST 원천 축소 | 최근 관측 지속은 계약. 배당 종류주 제한·이벤트 감쇠를 임의 변경하지 않음 |
| 필드 카탈로그·랙 | S19 `dataset_profile`, `rules_s*.py` | `_specs.py`, mock, compile | parity/fallback-lag 계약 테스트. 이미 수정된 #230을 새 결함으로 보고하지 않음 |
| 포트 동등성 | `EquityDataPort`, `RawObservationPort` | 공통 `_cell` | 같은 셀의 값·공개일·kind를 공유. 수정도 이 단일 owner에 적용 |
| 팩터·trace·실행 | `portfolio_design` pipeline | `strategy_factor_preview`, trace/backtest | #431 이후 별도 preview 계산 포트 제거. 실제 factor_evaluations와 PIT 유니버스 투영 |
| 신원·캐시 | 원장 build + `_field_contract` | snapshot/팩터 행렬 캐시 | 구간 의미 수정 시 `grid_lag_scope=security_span`을 계약 hash에 포함해 옛 캐시 재사용 방지 |
| 화면 | 생성 SDK, trace 응답 | frontend preview/debugger/canvas | 서버 사실을 표시하며 계산·PIT·선정을 재구현하지 않음. 기존 스택 최종 browser CI 증거 확인 |
| track sector·compat·fi/model | S25/운영 브랜치 별도 계약 | Workbench에는 미연결 | sector 표 존재와 adapter 지원은 별개. compat 최신 기준 정규화 및 정수 억원/백만원을 raw KRW로 연결하지 않음 |

## 발견 및 조치

### P1: GRID 랙이 재상장 이전 구간의 값을 새 security_id로 반환

- **상황**: 손 fixture의 036220 첫 구간은 2023-12-29에 끝나고 둘째 구간은 2024-01-08에 시작한다.
- **입력**: 2024-01-08 하루, `036220:2`, `price.close`, lag 5 패널 조회.
  원장 profile의 기본 랙이 5일 때 raw 포트도 동일하다. `price.adj_close`에서도 재현된다.
- **위치**: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_adapter.py::_cell`의
  GRID 분기는 `(ticker, cutoff)`만 조회하고 `span_seq`를 확인하지 않았다.
- **위험**: 이전 신원의 종가 11500이 새 신원의 관측으로 반환되어 구간 간 데이터가 섞인다.
  FIELD_MAP §3의 구간 분리 및 키 계약 위반이다.
- **수정**: 대상 행의 span이 다르면 관측을 생략한다. 동일 구간의 값·공개일은 유지한다.
  generic 조회 의미 변경을 필드 계약 hash에 반영해 기존 snapshot 캐시와 분리한다.
- **회귀**: `test_grid_lag_cannot_cross_a_relisting_span` 2종에서 수정 전 실패 확인.
  profile/override 랙, 일반 종목 유지, panel/raw 동등성 및 snapshot 분리를 검증한다.

### P1 검토 필요: track에서 가격축 보정 완료 행이 다시 MASKED

- **상황**: track `aebc8a09`의 실제 SQL·매크로 템플릿을 메모리 DuckDB에 설치한 합성 fixture.
- **입력**: 종가 100→50, `factor_ok=false`, `factor_source=krx_base_inconsistent`,
  `price_resolution=price_only`, `price_only_factor=0.5`, 공개일=적용일.
- **위치**: track `views.py:634–640`은 위 사건을 보정 여부와 무관하게 내고,
  `650–657`의 `v_adj_close`가 그 날짜를 가린다.
- **관측**: 표의 `adj_close=100`, `n_price_only_applied=1`이나 뷰는 `NULL`, `adj_gap=true`다.
- **위험**: 가격축 보정과 구식 미접힘 마스크가 충돌해 사용 가능한 가격을 감출 수 있다.
  다만 같은 사건 피드가 raw-bar 보유수량·손익도 소유하므로 사건을 통째로 제외하면 다른 의미가 바뀐다.
- **조치**: 운영 브랜치 변경 보류. 가격 마스킹과 손익 사건 계약을 분리할지 운영 작업에서 결정해야 한다.
  main LANG2 CI 성공으로 track e1.29 데이터 의미 호환성을 주장하지 않는다.

### P3: track README의 규칙 판본 지연

`aebc8a09`의 README e1.27.0과 model e1.29.0 불일치. 현재 코드 판별은 SHA+model로 수행했다.
운영 문서를 이번 main 버그 수정에 섞지 않았다.

## 검증 및 한계

- 독립 리뷰: main/track 합성 재현, 필드 동등성·폴백 랙·의존 방향 94개 통과.
  첫 수정 재검토의 선택 5개 통과, 공통 `_cell` 책임 유지 확인.
- 로컬 어댑터·계약·architecture 500개 통과(캐시 계약 보완 전).
  최초 repo 내부 basetemp 실행의 4개 실패는 비밀 fixture의 저장소 외부 경로 강제였다.
  저장소 밖 `/workspace/erd-tests-green`으로 다시 실행해 통과했고 검사 자체는 변경하지 않았다.
- backend 전체 lint 통과. pyright는 명시적 venv python 경로로 오류 0, Rust source 경고 4개.
  최초 pyright 기본 인터프리터의 누락 의존성 오류와 구분한다.
- 최종 회귀·전체 suite와 PR CI 결과는 PR 본문을 정본으로 기록한다.
- 실원장 DDL·행 동등성, 실제 공급자 응답, 전체 수집기 부작용 전수검사, 운영 cutover,
  실제 스크린리더 음성, 기존 SQLite 간헐 잠금 근본 원인은 미검증이다.
  합성 fixture와 소스 리뷰만으로 모든 실데이터 오류가 없다고 결론 내리지 않는다.
