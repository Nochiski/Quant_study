// 어시스턴트 어휘의 단일 입구는 `entities/assistant`다 — 생성 SDK 타입을 여기서 직접 들여오지 않는다.
import type {
  DocumentRefView,
  TurnContextPayload,
} from "../../../entities/assistant";
import { currentDiagnostics, type DocumentState } from "./document-state";

/**
 * 세션이 붙는 문서. 저장된 revision과 초안 중 정확히 하나다(application이 검증한다).
 *
 * 원시 값을 받는 이유는 호출자가 문서 전체가 아니라 이 세 값에만 memo를 걸 수 있게 하기 위해서다 —
 * 문서 텍스트는 타자마다 바뀌지만 세션이 붙은 문서는 그대로다.
 */
export const assistantDocumentRef = (
  strategyId: string | null,
  baseRevision: number | null,
  draftId: string | null,
): DocumentRefView =>
  strategyId !== null && baseRevision !== null
    ? { strategy_id: strategyId, revision: baseRevision }
    : { draft_id: draftId };

/**
 * 사이드바가 턴을 시작할 때 실어 보내는 문서 컨텍스트(spec D7: 서버는 문서를 따로 들지 않는다).
 * 두 전략 화면이 같은 값을 만들어야 하므로 문서 상태의 owner인 이 feature가 투영을 소유한다.
 *
 * 진단은 backend가 완성한 문장을 그대로 줄로 옮긴다(frontend가 다시 조립·번역하지 않는다, SoT 규칙).
 * 텍스트와 같은 버전의 진단만 담는다 — `currentDiagnostics`가 뒤처진 parse의 결과를 버린다.
 * `environment`는 실행 설정(기간·수수료 등)이다. 전략 언어 밖의 값이라 문서 텍스트와 따로 싣는다.
 *
 * `liveSource`는 편집기의 지금 텍스트다. 턴은 세션 생성 왕복 뒤에 시작될 수 있어 reducer 상태보다
 * 편집기가 앞서 있을 수 있으므로, 주어지면 그것이 정본이다. 그 경우 진단은 다른 텍스트의 결과이므로
 * 싣지 않는다 — 틀린 진단을 보내느니 없는 편이 낫다.
 */
export const assistantTurnContext = (
  state: DocumentState,
  environment: Record<string, unknown> | null = null,
  liveSource: string | null = null,
): TurnContextPayload => {
  const source = liveSource ?? state.source;
  return {
    source_text: source,
    source_format: state.format,
    diagnostics:
      source === state.source
        ? currentDiagnostics(state).map(
            (diagnostic) =>
              `[${diagnostic.severity}] ${diagnostic.pointer === "" ? "/" : diagnostic.pointer}: ${diagnostic.message}`,
          )
        : [],
    environment,
  };
};
