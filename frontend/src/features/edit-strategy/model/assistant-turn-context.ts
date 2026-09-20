import type { ReactNode } from "react";

// 어시스턴트 어휘의 단일 입구는 `entities/assistant`다 — 생성 SDK 타입을 여기서 직접 들여오지 않는다.
import type {
  DocumentRefView,
  TurnContextPayload,
} from "../../../entities/assistant";
import type { AssistantProposalApply } from "./use-apply-assistant-proposal";
import type { ProposalBacktestChain } from "./use-apply-then-backtest";
import { currentDiagnostics, type DocumentState } from "./document-state";

/**
 * 사이드바가 턴을 시작할 때 실어 보내는 문서 컨텍스트(spec D7: 서버는 문서를 따로 들지 않는다).
 * 두 전략 화면이 같은 값을 만들어야 하므로 문서 상태의 owner인 이 feature가 투영을 소유한다.
 */
export type AssistantDocumentContext = {
  documentRef: DocumentRefView;
  turnContext: TurnContextPayload;
};

/**
 * 전략 화면이 IDE `assistant` 슬롯에 꽂는 사이드바를 만드는 함수. 페이지는 문서 컨텍스트와 적용
 * 핸들만 넘기고 채팅 UI는 알지 않는다(feature가 feature를 import하지 않는다, spec D7).
 */
export type AssistantSlotRender = (slot: {
  document: AssistantDocumentContext;
  apply: AssistantProposalApply;
  /** "적용 후 백테스트" 한 동작. 적용과 실행 사이의 검증 대기는 이 체인이 맡는다. */
  backtest: ProposalBacktestChain;
}) => ReactNode;

export type AssistantContextOptions = {
  /** 저장 전 문서의 세션 식별자. 저장된 revision을 열었으면 쓰이지 않는다. */
  draftId: string | null;
  /** 실행 설정(기간·수수료 등). 전략 언어 밖의 값이라 문서 텍스트와 따로 보낸다. */
  environment?: Record<string, unknown> | null;
};

/** 세션이 붙는 문서. 저장된 revision과 초안 중 정확히 하나다(application이 검증한다). */
const documentRefOf = (
  state: DocumentState,
  draftId: string | null,
): DocumentRefView =>
  state.strategyId !== null && state.baseRevision !== null
    ? { strategy_id: state.strategyId, revision: state.baseRevision }
    : { draft_id: draftId };

/**
 * 진단은 backend가 완성한 문장을 그대로 줄로 옮긴다(frontend가 다시 조립·번역하지 않는다, SoT 규칙).
 * 텍스트와 같은 버전의 진단만 담는다 — `currentDiagnostics`가 뒤처진 parse의 결과를 버린다.
 */
export const assistantDocumentContext = (
  state: DocumentState,
  { draftId, environment = null }: AssistantContextOptions,
): AssistantDocumentContext => ({
  documentRef: documentRefOf(state, draftId),
  turnContext: {
    source_text: state.source,
    source_format: state.format,
    diagnostics: currentDiagnostics(state).map(
      (diagnostic) =>
        `[${diagnostic.severity}] ${diagnostic.pointer === "" ? "/" : diagnostic.pointer}: ${diagnostic.message}`,
    ),
    environment,
  },
});
