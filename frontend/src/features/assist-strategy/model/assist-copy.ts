import { AssistantRequestError } from "../../../entities/assistant";
import { t, tOptional, type MessageKey } from "../../../shared/config";

/**
 * 도구 이름 → 화면 문구.
 *
 * 이름 자체는 backend `domain/assistant`가 소유하는 프로토콜 식별자라 번역하지 않고 키로만 쓴다.
 * 서버가 도구를 늘리면 번역이 없을 뿐이므로 그때는 이름을 그대로 보여 준다.
 */
export const assistToolLabel = (name: string): string =>
  tOptional(`assistant.chat.toolName.${name}`) ?? name;

const REJECTION_MESSAGE: Record<string, MessageKey> = {
  "assistant.no_active_provider": "assistant.error.no_active_provider",
  "assistant.provider_not_installed": "assistant.error.provider_not_installed",
  "assistant.provider_secret_missing":
    "assistant.error.provider_secret_missing",
  "assistant.document_ref_invalid": "assistant.error.document_ref_invalid",
  "assistant.session.not_found": "assistant.error.not_found",
  "assistant.turn.not_found": "assistant.error.not_found",
  "assistant.provider.not_found": "assistant.error.not_found",
};

/**
 * 진행 중 턴이 있다는 409.
 *
 * 다른 거부와 달리 셋을 함께 한다 — 배너로 알리고, 보내지 못한 질문을 입력칸으로 되돌리고,
 * 이력으로 그 턴을 따라잡는다. 질문이 서버에 닿지 않았다는 사실을 알릴 자리가 필요하다.
 */
export const TURN_IN_PROGRESS = "assistant.turn_in_progress";

/**
 * 요청 거부를 화면 문구로 옮긴다.
 *
 * `features/configure-ai-providers`의 같은 성격 함수와 나란한 자리지만 feature가 feature를 import하지
 * 않으므로(FSD) 채팅이 실제로 만나는 코드만 여기서 옮긴다. 서버가 보낸 문장은 쓰지 않는다 —
 * 문구의 owner는 frontend i18n이고 공급자 오류 본문에는 키 조각이 섞인다(spec D6).
 */
export const assistRejection = (error: unknown): string => {
  if (!(error instanceof AssistantRequestError) || error.code === undefined) {
    return t("assistant.error.unknown");
  }
  return t(REJECTION_MESSAGE[error.code] ?? "assistant.error.unknown");
};
