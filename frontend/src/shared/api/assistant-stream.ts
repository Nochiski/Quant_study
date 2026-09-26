import { streamAssistantEvents } from "./generated/sdk.gen";
import type {
  AssistantEventEnvelopeView,
  ProposalCompileView,
  SourceView,
  StrategyProposalView,
} from "./generated/types.gen";

/**
 * SSE 연결 시도 상한(최초 연결 1 + 재시도 4). 생성 클라이언트가 세는 단위가 "시도"다.
 *
 * 이 값을 주지 않으면 무한히 다시 연결한다 — 서버가 죽은 동안 탭이 조용히 재연결을 반복하는 대신
 * 상한에서 멈추고 이력 조회로 떨어진다(spec D7).
 */
export const ASSISTANT_SSE_MAX_RETRY_ATTEMPTS = 5;

/** 서버가 스트림을 열어 주지 않은 응답. `code`는 `detail.code`이며 없으면 null이다. */
export type AssistantStreamRejection = {
  status: number;
  code: string | null;
};

export type AssistantEventStreamOptions = {
  sessionId: string;
  /** 최초 연결의 재개 위치. 재연결은 생성 클라이언트가 `Last-Event-ID`로 넘긴다(spec D6). */
  afterSequence: number;
  signal: AbortSignal;
  maxRetryAttempts?: number;
  /** 첫 재시도까지의 대기(ms). 생략하면 생성 클라이언트 기본값(3000). 테스트가 줄여 쓴다. */
  retryDelayMs?: number;
  /** 4xx 거절. 재시도하지 않고 이력으로 복구할 사유다(spec D7: 409 `no_running_turn`). */
  onRejected?: (rejection: AssistantStreamRejection) => void;
  /**
   * 연결 시도 한 번의 결말. 정상 종료와 재시도 상한 소진을 호출자가 구분하는 데 쓴다.
   *
   * 응답이 열리면 true, **연결된 뒤 본문이 끊겨도** false다. fetch 성공만 세면 마지막 시도가
   * "붙었다 끊김"일 때 상한 소진이 정상 종료로 보인다 — 생성 클라이언트는 상한을 넘겨도 던지지 않고
   * generator를 그냥 끝내므로, 호출자에게는 이 신호 말고 구분할 단서가 없다(리뷰 P1-2).
   */
  onStreamOutcome?: (ok: boolean) => void;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isString = (value: unknown): value is string => typeof value === "string";

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const rejectionCode = async (response: Response): Promise<string | null> => {
  try {
    const body: unknown = await response.json();
    const detail = isRecord(body) ? body.detail : undefined;
    return isRecord(detail) && isString(detail.code) ? detail.code : null;
  } catch {
    // 코드 없는 거절(프록시 오류 본문 등)도 거절이라는 사실 자체는 그대로 전한다.
    return null;
  }
};

/**
 * 생성 SSE 클라이언트에 "바로 끝난 스트림"으로 답한다.
 *
 * 4xx 응답을 그대로 돌려주면 클라이언트는 그것을 연결 실패로 보고 재시도 상한까지 같은 거절을
 * 반복한다. 진행 중 턴이 없다는 409는 몇 번을 다시 물어도 같은 답이므로, 사유는 `onRejected`로
 * 호출자에게 넘기고 스트림은 조용히 닫는다(spec D7).
 */
const closedStreamResponse = (): Response =>
  new Response(
    new ReadableStream<Uint8Array>({
      start: (controller) => controller.close(),
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );

/**
 * 진행 중 턴의 이벤트 스트림을 연다.
 *
 * 생성 SDK의 SSE 클라이언트를 그대로 쓴다 — `EventSource`는 재연결마다 같은 URL을 처음부터 다시 받아
 * 이벤트를 중복 수신하고, `Last-Event-ID` 헤더를 붙일 수 없다(spec D7).
 *
 * 4xx 거절과 연결 시도 결과를 호출자에게 알리려고 `fetch`를 한 겹 감싼다. 생성 클라이언트는 실패 원인을
 * `SSE failed: <status>` 문자열로만 던져 상태 코드와 `detail.code`를 구분할 수 없다.
 */
export const openAssistantEventStream = ({
  sessionId,
  afterSequence,
  signal,
  maxRetryAttempts = ASSISTANT_SSE_MAX_RETRY_ATTEMPTS,
  retryDelayMs,
  onRejected,
  onStreamOutcome,
}: AssistantEventStreamOptions) =>
  streamAssistantEvents({
    path: { session_id: sessionId },
    query: { after_sequence: afterSequence },
    signal,
    sseMaxRetryAttempts: maxRetryAttempts,
    ...(retryDelayMs === undefined ? {} : { sseDefaultRetryDelay: retryDelayMs }),
    // 연결 실패·본문 끊김·상한 소진 직전까지 모두 이 콜백을 지난다(fetch가 던진 경우도 포함).
    onSseError: () => onStreamOutcome?.(false),
    fetch: async (input, init) => {
      const response = await globalThis.fetch(input, init);
      if (response.ok) {
        onStreamOutcome?.(true);
        return response;
      }
      // 5xx는 서버가 다시 살아날 수 있는 일시 오류다 — 생성 클라이언트의 재시도에 맡긴다.
      // 실패로 세는 것은 뒤이어 불리는 `onSseError`가 한다.
      if (response.status >= 500) return response;
      onRejected?.({
        status: response.status,
        code: await rejectionCode(response),
      });
      return closedStreamResponse();
    },
  });

const isSourceList = (value: unknown): value is SourceView[] =>
  Array.isArray(value) &&
  value.every(
    (item) => isRecord(item) && isString(item.title) && isString(item.url),
  );

const isCompile = (value: unknown): value is ProposalCompileView =>
  isRecord(value) &&
  typeof value.ok === "boolean" &&
  Array.isArray(value.diagnostics);

const isProposal = (value: unknown): value is StrategyProposalView =>
  isRecord(value) &&
  isString(value.title) &&
  isString(value.summary) &&
  isString(value.rationale) &&
  isString(value.source_text) &&
  isSourceList(value.sources) &&
  isCompile(value.compile);

/** 생성 SDK가 아는 이벤트 갈래 이름 전부. */
type AssistantEventType = AssistantEventEnvelopeView["event"]["type"];

/**
 * 갈래별로 리듀서가 실제로 읽는 필드가 있는지 본다.
 *
 * 프레임은 신뢰하지 않는 입력이다(프록시가 끼어든 본문, 서버 버전 불일치). 필드가 없으면 누적 텍스트에
 * `undefined`가 섞이는 식으로 조용히 망가지므로, 좁히지 못한 프레임은 버린다.
 *
 * 키를 갈래 합집합으로 묶는다(Phase B 감사 NB-6). backend가 갈래를 더하고 SDK를 재생성하면 리듀서
 * `switch`처럼 이 표도 컴파일되지 않는다 — 표를 잊으면 그 갈래 프레임이 런타임에 전부 버려진다.
 */
const EVENT_FIELDS: Record<
  AssistantEventType,
  (event: Record<string, unknown>) => boolean
> = {
  text_delta: (event) => isString(event.text),
  thinking_summary: (event) => isString(event.text),
  tool_call: (event) =>
    isString(event.call_id) &&
    isString(event.name) &&
    isRecord(event.arguments),
  tool_result: (event) =>
    isString(event.call_id) &&
    isString(event.name) &&
    typeof event.ok === "boolean" &&
    isString(event.summary),
  search_activity: (event) =>
    isString(event.query) && isSourceList(event.sources),
  proposal: (event) => isProposal(event.proposal),
  usage: (event) =>
    isFiniteNumber(event.input_tokens) &&
    isFiniteNumber(event.output_tokens) &&
    isFiniteNumber(event.cache_read_tokens) &&
    isFiniteNumber(event.cache_write_tokens),
  done: (event) => isString(event.stop_reason),
  failure: (event) => isString(event.code) && isString(event.message),
};

/**
 * SSE 프레임 하나를 이벤트 봉투로 좁힌다. 좁히지 못하면 null — 호출자는 그 프레임을 버린다.
 *
 * 생성 SDK는 이 엔드포인트의 200 본문을 `unknown`으로 만든다(OpenAPI가 `text/event-stream` 본문 스키마를
 * 싣지 않는다). 봉투 타입 자체는 생성된 것을 쓰고 손으로 다시 적지 않는다.
 */
export const assistantEventEnvelope = (
  frame: unknown,
): AssistantEventEnvelopeView | null => {
  if (!isRecord(frame)) return null;
  if (!isFiniteNumber(frame.sequence) || !isString(frame.turn_id)) return null;
  const event = frame.event;
  if (!isRecord(event) || !isString(event.type)) return null;
  // 자기 키만 본다 — `constructor` 같은 프로토타입 이름이 판정 함수로 잡히면 안 된다.
  if (!Object.hasOwn(EVENT_FIELDS, event.type)) return null;
  if (!EVENT_FIELDS[event.type as AssistantEventType](event)) return null;
  return frame as unknown as AssistantEventEnvelopeView;
};
