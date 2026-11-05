import { HttpResponse, http } from "msw";

import type { LedgerRefreshBody, MarketOverviewBody } from "../../shared/api";

/**
 * 앱 셸이 어느 화면에서든 읽는 DB 새로고침 상태와 홈 시장 블록 — 연습용 원천의 응답이다(숫자도 버튼도 없다).
 * 라우터를 띄우는 테스트가 함께 쓴다(실험 완료 알림의 실험 목록처럼 전역 알림이 읽는다).
 */
export const practiceDataHandlers = (api: string) => [
  http.get(`${api}/api/v1/market/overview`, () =>
    HttpResponse.json({
      status: "practice_data",
      message:
        "시장 정보는 실제 데이터를 연결해야 보여요. 지금은 연습용 데이터로 백테스트만 돌아요.",
      session: null,
      indices: [],
      flows: [],
      sectors: null,
    } satisfies MarketOverviewBody),
  ),
  http.get(`${api}/api/v1/ledger-refresh`, () =>
    HttpResponse.json({
      code: "unavailable",
      message: "연습용 데이터로 켜져 있어 DB 새로고침을 쓸 수 없어요.",
      can_start: false,
      refreshing: false,
      reason: "practice_data",
      failure: null,
      needs_check: false,
      remote: {
        state: "unknown",
        new_tables: 0,
        download_bytes: 0,
        in_build_window: false,
        checked_at: null,
      },
      progress: null,
      active_work: null,
      started_at: null,
      finished_at: null,
    } satisfies LedgerRefreshBody),
  ),
];
