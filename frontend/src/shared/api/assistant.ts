import {
  activateAssistantProvider,
  createAssistantProvider,
  deleteAssistantProvider,
  listAssistantProviders,
  testAssistantProvider,
} from "./generated/sdk.gen";
import type {
  CreateProviderProfileRequestWritable,
  ProbeFailure,
  ProbeResultView,
  ProviderProfileView,
  ProvidersView,
} from "./generated/types.gen";

/**
 * 어시스턴트 API가 코드로 거부한 응답.
 *
 * `strategy-workbench.ts`의 `ApiRequestError`와 나란한 자리지만 별도 타입인 이유는 `probe_failed`가
 * `failure` 열거값을 함께 싣기 때문이다 — 화면이 "키를 확인하세요"와 "잠시 후 다시"를 구분하려면 그 값이
 * 필요하다(spec D2). 서버가 보낸 `message`는 담지 않는다: 화면 문구의 owner는 frontend i18n이고,
 * 공급자 오류 본문이 키 조각을 담아 오는 사고를 구조적으로 막는다(spec D6).
 */
export class AssistantRequestError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly probeFailure: ProbeFailure | null;

  constructor(
    context: string,
    status: number,
    code: string | undefined,
    probeFailure: ProbeFailure | null,
  ) {
    super(
      `Assistant request failed: ${context} status=${status} code=${code ?? "-"}`,
    );
    this.name = "AssistantRequestError";
    this.status = status;
    this.code = code;
    this.probeFailure = probeFailure;
  }
}

const PROBE_FAILURES: ReadonlySet<string> = new Set<ProbeFailure>([
  "auth",
  "model_not_found",
  "network",
  "rate_limit",
  "unknown",
]);

/** 코드 detail만 꺼낸다 — FastAPI 요청 검증 오류의 배열 detail은 코드가 아니므로 버린다. */
const codedDetail = (error: unknown): Record<string, unknown> | null => {
  if (typeof error !== "object" || error === null || !("detail" in error)) {
    return null;
  }
  const detail = (error as { detail: unknown }).detail;
  return typeof detail === "object" && detail !== null && !Array.isArray(detail)
    ? (detail as Record<string, unknown>)
    : null;
};

const assistantError = (
  response: { error?: unknown; response?: { status: number } },
  context: string,
): AssistantRequestError => {
  const detail = codedDetail(response.error);
  const code = typeof detail?.code === "string" ? detail.code : undefined;
  const failure = detail?.failure;
  return new AssistantRequestError(
    context,
    response.response?.status ?? 0,
    code,
    typeof failure === "string" && PROBE_FAILURES.has(failure)
      ? (failure as ProbeFailure)
      : null,
  );
};

const unwrap = <T>(
  response: { data?: T; error?: unknown; response?: { status: number } },
  context: string,
): T => {
  if (response.error !== undefined) throw assistantError(response, context);
  if (response.data === undefined) {
    throw new Error(`Assistant response did not contain data: ${context}`);
  }
  return response.data;
};

/**
 * 생성 SDK 위의 얇은 공급자 프로파일 API. 타입을 손으로 옮겨 적지 않고 생성 타입을 그대로 통과시킨다.
 *
 * `secret`은 이 방향으로만 흐르고 응답 스키마에는 없다(OpenAPI `writeOnly`).
 */
export const assistantProviderApi = {
  async listProviders(): Promise<ProvidersView> {
    return unwrap(await listAssistantProviders(), "listAssistantProviders");
  },

  async createProvider(
    input: CreateProviderProfileRequestWritable,
  ): Promise<ProviderProfileView> {
    return unwrap(
      await createAssistantProvider({ body: input }),
      "createAssistantProvider",
    );
  },

  async activateProvider(profileId: string): Promise<ProviderProfileView> {
    return unwrap(
      await activateAssistantProvider({ path: { profile_id: profileId } }),
      "activateAssistantProvider",
    );
  },

  async testProvider(profileId: string): Promise<ProbeResultView> {
    return unwrap(
      await testAssistantProvider({ path: { profile_id: profileId } }),
      "testAssistantProvider",
    );
  },

  async deleteProvider(profileId: string): Promise<void> {
    const response = await deleteAssistantProvider({
      path: { profile_id: profileId },
    });
    if (response.error !== undefined) {
      throw assistantError(response, "deleteAssistantProvider");
    }
  },
};
