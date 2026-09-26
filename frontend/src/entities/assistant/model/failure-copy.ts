import type { AssistantFailure } from "./chat-state";
import type { FailureCode } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";

/**
 * 턴 실패 코드 → 화면 문장.
 *
 * 서버가 보낸 `Failure.message`를 그리지 않는다. 그 값은 진단용 고정 문구이고 문구의 owner는
 * frontend i18n이다(spec D2·`frontend-api-state.md`의 코드 기반 번역 규칙).
 */
const FAILURE_MESSAGE: Record<FailureCode, MessageKey> = {
  auth: "assistant.turn.failure.auth",
  rate_limit: "assistant.turn.failure.rate_limit",
  network: "assistant.turn.failure.network",
  refusal: "assistant.turn.failure.refusal",
  provider: "assistant.turn.failure.provider",
  internal: "assistant.turn.failure.internal",
  tool_rounds_exceeded: "assistant.turn.failure.tool_rounds_exceeded",
  timeout: "assistant.turn.failure.timeout",
  cancelled: "assistant.turn.failure.cancelled",
  proposal_invalid: "assistant.turn.failure.proposal_invalid",
  output_truncated: "assistant.turn.failure.output_truncated",
  token_budget_exceeded: "assistant.turn.failure.token_budget_exceeded",
};

/** 모르는 코드(서버가 어휘를 늘린 경우)는 일반 문구로 떨어진다 — 서버 원문을 대신 쓰지 않는다. */
export const assistantFailureMessage = (failure: AssistantFailure): string =>
  t(FAILURE_MESSAGE[failure.code] ?? "assistant.turn.failure.unknown");
