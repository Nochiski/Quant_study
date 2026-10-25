import {
  deleteKiwoomMockConnection,
  getKiwoomMockConnection,
  saveKiwoomMockConnection,
  verifyKiwoomMockConnection,
} from "./generated/sdk.gen";
import type {
  KiwoomMockConnectionBody,
  KiwoomMockConnectionCheckBody,
  SaveKiwoomMockConnectionBody,
} from "./generated/types.gen";

/** 저장 요청의 거부. 서버가 고친 칸(`field`)과 완성한 한글 문장을 그대로 나른다. 키는 싣지 않는다. */
export class BrokerConnectionRequestError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly field: string | null;
  readonly serverMessage: string | null;

  constructor(
    context: string,
    status: number,
    code: string | undefined,
    field: string | null,
    serverMessage: string | null,
  ) {
    super(
      `Broker connection request failed: ${context} status=${status} code=${code ?? "-"}`,
    );
    this.name = "BrokerConnectionRequestError";
    this.status = status;
    this.code = code;
    this.field = field;
    this.serverMessage = serverMessage;
  }
}

const unwrap = <T>(
  response: { data?: T; error?: unknown; response?: { status: number } },
  context: string,
): T => {
  if (response.error !== undefined) {
    const raw = response.error as { detail?: unknown };
    const detail =
      typeof raw?.detail === "object" &&
      raw.detail !== null &&
      !Array.isArray(raw.detail)
        ? (raw.detail as Record<string, unknown>)
        : {};
    throw new BrokerConnectionRequestError(
      context,
      response.response?.status ?? 0,
      typeof detail.code === "string" ? detail.code : undefined,
      typeof detail.field === "string" ? detail.field : null,
      typeof detail.message === "string" ? detail.message : null,
    );
  }
  if (response.data === undefined) {
    throw new Error(
      `Broker connection response did not contain data: ${context}`,
    );
  }
  return response.data;
};

/**
 * 키움 모의 연결 설정 API(S06 ②-2). 앱 키·시크릿·계좌번호는 저장 요청으로만 흐르고 응답에는 없다
 * (OpenAPI `writeOnly`). 응답은 저장 여부와 계좌번호 끝 4자리, 연결 확인 결과 코드와 한글 문장이다.
 */
export const brokerConnectionApi = {
  async status(): Promise<KiwoomMockConnectionBody> {
    return unwrap(await getKiwoomMockConnection(), "getKiwoomMockConnection");
  },

  async save(
    input: SaveKiwoomMockConnectionBody,
  ): Promise<KiwoomMockConnectionBody> {
    return unwrap(
      await saveKiwoomMockConnection({ body: input }),
      "saveKiwoomMockConnection",
    );
  },

  async remove(): Promise<void> {
    const response = await deleteKiwoomMockConnection();
    if (response.error !== undefined) {
      throw new BrokerConnectionRequestError(
        "deleteKiwoomMockConnection",
        response.response?.status ?? 0,
        undefined,
        null,
        null,
      );
    }
  },

  async verify(): Promise<KiwoomMockConnectionCheckBody> {
    return unwrap(
      await verifyKiwoomMockConnection(),
      "verifyKiwoomMockConnection",
    );
  },
};
