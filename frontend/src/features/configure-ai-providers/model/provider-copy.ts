import {
  AssistantRequestError,
  assistantRejectionMessage,
  isAssistantRejectionCode,
  type AssistantRejectionCode,
  type ProbeFailure,
  type ProviderKind,
} from "../../../entities/assistant";
import { t, type MessageKey } from "../../../shared/config";

const KIND_LABEL: Record<ProviderKind, MessageKey> = {
  anthropic: "assistant.provider.kind.anthropic",
  openai: "assistant.provider.kind.openai",
};

/** 저장·전송 계약인 `kind` 값과 화면 이름을 분리한다(spec D2). */
export const providerKindLabel = (kind: ProviderKind): string =>
  t(KIND_LABEL[kind]);

const PROBE_MESSAGE: Record<ProbeFailure, MessageKey> = {
  auth: "assistant.probe.auth",
  model_not_found: "assistant.probe.model_not_found",
  network: "assistant.probe.network",
  rate_limit: "assistant.probe.rate_limit",
  unknown: "assistant.probe.unknown",
};

export const probeFailureMessage = (failure: ProbeFailure | null): string =>
  t(failure === null ? "assistant.probe.unknown" : PROBE_MESSAGE[failure]);

/** 거부를 표시할 자리. `form`은 특정 입력칸에 걸 수 없는 사유다. */
export type ProviderField =
  "kind" | "label" | "model" | "secret" | "baseUrl" | "form";

export type ProviderRejection = { field: ProviderField; message: string };

/** probe 실패는 사유마다 고쳐야 하는 입력칸이 다르다 — 사유를 그 칸에 건다. */
const PROBE_FIELD: Record<ProbeFailure, ProviderField> = {
  auth: "secret",
  model_not_found: "model",
  network: "form",
  rate_limit: "form",
  unknown: "form",
};

/**
 * 거부 코드마다 사유를 걸 입력칸. 문구는 entity의 공용 표가 소유하고(Phase B 감사 NB-1), 여기에는
 * 설정 폼만 아는 칸 배치만 둔다. 없는 코드는 폼 전체에 건다.
 */
const CODE_FIELD: Partial<Record<AssistantRejectionCode, ProviderField>> = {
  "assistant.base_url_rejected": "baseUrl",
  "assistant.provider_not_installed": "kind",
};

/**
 * 서버 거부를 화면 문구로 옮긴다.
 *
 * 서버가 보낸 문장을 그리지 않고 코드·`failure` 열거값만 읽는다 — 문구의 owner는 frontend i18n이고,
 * 공급자 오류 본문에 키 조각이 섞여 오더라도 화면에 닿지 않는다(spec D6, `AssistantRequestError`).
 */
export const providerRejection = (error: unknown): ProviderRejection => {
  if (!(error instanceof AssistantRequestError)) {
    return { field: "form", message: t("assistant.error.unknown") };
  }
  if (error.code === "assistant.probe_failed") {
    const failure = error.probeFailure;
    return {
      field: failure === null ? "form" : PROBE_FIELD[failure],
      message: probeFailureMessage(failure),
    };
  }
  return {
    field:
      (isAssistantRejectionCode(error.code)
        ? CODE_FIELD[error.code]
        : undefined) ?? "form",
    message: assistantRejectionMessage(error),
  };
};
