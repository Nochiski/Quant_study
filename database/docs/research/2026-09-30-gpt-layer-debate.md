# stage · equity 층이 필요한가 — GPT 토론 기록 (2026-09-30)

**계기(사용자)**: "원장에서 굳이 stage·equity 를 거칠 필요 없는 데이터가 있나? 아니면 층이 굳이 필요한가?" → "지피티랑 토론".
**방식**: codex CLI(read-only, 저장소 열람 허용) 2라운드. 자료 `scratchpad/gpt_layers_brief.md`·`gpt_layers_round2.md`(세션 로컬).
GPT 주장 중 코드 인용은 Claude 가 직접 열어 확인했다(아래 "확인").

## 결론 (합의)

1. **층(기능)은 유지한다.** 시점(가용일)·판본·원문 풀기·수정주가·재무 표준화는 한 곳에 있어야 한다. 모델이 원장을 직접 읽으면 로직이
   모델·백테스트·호환층에 복제된다. 다만 "모든 버그가 모델로 옮겨 간다"는 과장 — **중복 물리화를 줄이면 복사·판본 조율 결함은 실제로 준다**(GPT).
2. **줄일 것은 층 개수가 아니라 전량 빌드의 실패 전파·중복 사본**이다.
3. **분리 기준은 "모델이 직접 읽나"가 아니라 전이 의존 폐포**다. Claude 의 첫 입장(공시 이벤트 13표는 모델이 안 쓴다)은 **틀렸다**.
4. **다음 2주는 전량 계산 유지**, 증분 빌드는 하지 않는다(정정·분기 차감·이동창 영향 구간 재계산이 이번 달 버그 유형을 재생산할 위험).
5. 원천 대조 게이트는 **기본 WARN**, 확정 오류만 필드 격리, HALT 는 시점 누출·광범위 단위 오류·필수 입력 부재·최소 커버리지 미달에만.

## 확인한 사실 (GPT 지적 → 코드)

| 지적 | 확인 |
|---|---|
| 수정주가가 공시 이벤트에 의존 | `adj_factor.inputs = (corp_event, price_daily, trading_calendar, stg_event_cr, security, security_span)`(`src/equity/rules_s06.py:659-660`), `corp_event` 는 `rules_s05.py` 에서 `stg_event_*` 12곳 참조 |
| 연간 재무·감사·지연·배당도 모델 입력 | `factor_inputs.TABLE_SOURCES`(`queries.py:705-717`) — `fin_std`·`audit_opinion`·`disclosure_version`·`dividend_event` |
| `fin_std` 는 최초 발표본 PIT 가 아니다 | `vintage_kind='api_restated'`·`restated_unknown=true`(`rules_s12.py:25-27`) |
| stage 가용일이 별도 참조표에 의존 | `STG_FIN.available = lookup(stg_rcept_dt_map)`(`rules_dart.py:95`) — 선언 그래프에 넣어야 한다 |
| 병목 3표 = stage 시간 71% | stg_fin 1,535 + stg_fin_wise 521 + stg_credit_daily 488 = 2,544 / 3,594s |
| EG5a·C4 는 규칙 판본이 바뀌면 건너뛴다 | 맞다 — 마찰은 "판본을 올리지 않으면 실패"하는 쪽(09-26 e1.18→e1.21 경험) |
| WISE 원장은 같은 날 재수집을 덮어쓴다 | `INSERT OR REPLACE`, 키에 수집일(`backfill_wise.py:352`) — 날짜 사이는 쌓인다 |

## 새로 드러난 위험 (미검증 — 최우선 조사)

**`fin_std` 값–접수번호–공개일 결합.** 보고서 묶음(`grp`)의 공개일은 `min(rcept_no)`(`sql/fin_std.sql:122`), 계정 값은
자연키당 `observed_date` 최소(first_write_wins, `fin_std.sql:143-146`)로 따로 고른다. 원본을 먼저 관측한 뒤 정정본에서 **새로 생긴 계정**은
정정본 값이 원본 공개일로 찍힐 수 있다 → 백테스트 look-ahead. 건수 미측정.

## 남은 이견

- Claude: 97표를 손으로 정리하지 않고 선언(`inputs`·`sources`·`TABLE_SOURCES`)에서 폐포를 자동 계산하면 반나절.
  GPT: 선언만으로는 부족(가용일 참조표·게이트 대조 원천·문서 프리패스 포함 필요), 누락 검증까지 반나절 단정 불가. 병목 측정과 병행하자. → **병행으로 합의**.
- Claude: 병목은 원문 파싱 캐시로만 줄이자. GPT: 파싱 캐시는 `stg_fin_wise` 에만 해당(`build.py:522` 블롭 경로), `stg_fin`·`stg_credit_daily` 는 SQL 경로라
  단계별 시간 측정이 먼저. 캐시도 무효화 규칙(원문 해시 + 파서 판본 + 요청 문맥)과 전량 출력 동등성 검증이 필요. → **GPT 쪽이 맞다**.
- 백테스트 재무: GPT — 전 종목 최초본 복원(4C)은 2주 선행 조건 아님, 정정 사례·신규상장·누적/분기 혼동 사례로 **작은 4C 실험** 후 확대 판단.

## 2주 작업 순서 (합의안)

1. 전이 의존 그래프 추출·누락 검증 + 병목 3표 단계별 시간 측정.
2. `fin_std` 값–접수번호–공개일 결합 검증과 판본 혼합 결함 수정.
3. WISE 분기 수집(플랜 `2026-09-30-wise-quarterly.md`) + 같은 날짜·유니버스·모델의 WISE/DART 그림자 비교.
4. 폐포 우선 실행 · 범위별 게이트 · **모델은 고정된 빌드 묶음**을 읽기(표별 `current_build` 혼합 방지) · 폐포 밖 실패 격리.
5. 측정 근거에 따른 SQL 병목 개선 · WISE 파싱 캐시(전량 출력 동등성 확인).
6. 원천 대조 등급(WARN/격리/HALT) 적용 · 소규모 4C 복원 실험.
