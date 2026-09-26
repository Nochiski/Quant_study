import type {
  Assistant409Response,
  AssistantNotFoundResponse,
  AssistantUnprocessableResponse,
} from "../../../shared/api";
import { AssistantRequestError } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";

/**
 * 어시스턴트 API가 4xx로 돌려주는 거부 코드 전부.
 *
 * backend가 선언한 세 응답 모델(409·404·422)의 `detail.code` 리터럴에서 파생한다. 손으로 옮겨 적지
 * 않으므로 backend가 코드 이름을 바꾸거나 갈래를 더하고 SDK를 재생성하면 아래 표가 컴파일되지 않는다.
 */
export type AssistantRejectionCode =
  | Assistant409Response["detail"]["code"]
  | AssistantNotFoundResponse["detail"]["code"]
  | AssistantUnprocessableResponse["detail"]["code"];

/**
 * 진행 중 턴이 있다는 409.
 *
 * 채팅은 이 거부를 다른 거부와 달리 다룬다 — 배너로 알리고, 보내지 못한 질문을 입력칸으로 되돌리고,
 * 이력으로 그 턴을 따라잡는다. 그 분기가 비교할 서버 어휘라 여기서 한 번만 적는다.
 */
export const ASSISTANT_TURN_IN_PROGRESS =
  "assistant.turn_in_progress" satisfies AssistantRejectionCode;

/**
 * 거부 코드 → 화면 문구. 설정 화면과 사이드바가 함께 쓰는 단일 표다(Phase B 감사 NB-1).
 *
 * `Record<AssistantRejectionCode, …>`라 코드가 늘거나 이름이 바뀌면 여기서 컴파일 오류가 난다. 한
 * 화면만 만나는 코드도 같은 표에 둔다 — 화면마다 표를 나누면 한쪽만 고쳐 "알 수 없는 오류"로 조용히
 * 격하되는 일이 다시 생긴다.
 */
const REJECTION_MESSAGE: Record<AssistantRejectionCode, MessageKey> = {
  "assistant.base_url_rejected": "assistant.error.base_url_rejected",
  "assistant.document_ref_invalid": "assistant.error.document_ref_invalid",
  "assistant.no_active_provider": "assistant.error.no_active_provider",
  // 이미 끝난 턴을 중지하려 한 경우다. 이력이 곧 정착 상태를 보이므로 따로 설명하지 않는다.
  "assistant.no_running_turn": "assistant.error.unknown",
  // `failure` 없이 온 probe 실패. 사유가 있으면 설정 feature가 사유별 문구를 먼저 고른다.
  "assistant.probe_failed": "assistant.probe.unknown",
  "assistant.provider.not_found": "assistant.error.not_found",
  "assistant.provider_not_installed": "assistant.error.provider_not_installed",
  "assistant.provider_secret_missing":
    "assistant.error.provider_secret_missing",
  "assistant.session.not_found": "assistant.error.not_found",
  "assistant.turn.not_found": "assistant.error.not_found",
  "assistant.turn_in_progress": "assistant.chat.turnInProgress",
};

/** 서버가 보낸 코드 문자열이 생성 SDK가 아는 거부 코드인지. 모르면 일반 문구로 떨어진다. */
export const isAssistantRejectionCode = (
  code: string | undefined,
): code is AssistantRejectionCode =>
  code !== undefined && Object.hasOwn(REJECTION_MESSAGE, code);

/**
 * 요청 거부를 화면 문구로 옮긴다.
 *
 * 서버가 보낸 문장은 쓰지 않는다 — 문구의 owner는 frontend i18n이고 공급자 오류 본문에는 키 조각이
 * 섞인다(spec D6, `AssistantRequestError`). 모르는 코드와 코드 없는 오류는 일반 문구다.
 */
export const assistantRejectionMessage = (error: unknown): string => {
  if (!(error instanceof AssistantRequestError)) {
    return t("assistant.error.unknown");
  }
  return t(
    isAssistantRejectionCode(error.code)
      ? REJECTION_MESSAGE[error.code]
      : "assistant.error.unknown",
  );
};
