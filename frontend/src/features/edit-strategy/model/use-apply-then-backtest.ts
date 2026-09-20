import { useCallback, useEffect, useRef, useState } from "react";

import type { DocumentState } from "./document-state";
import type {
  AssistantProposal,
  AssistantProposalApply,
  ProposalApplyStatus,
} from "./use-apply-assistant-proposal";

export type ProposalBacktestChain = {
  /** 제안 카드의 "적용 후 백테스트". 적용은 같은 경로를 타고, 검증이 끝나면 실행을 잇는다. */
  applyThenBacktest: (proposal: AssistantProposal) => void;
  /** 적용·확인·검증이 끝나기를 기다리는 중. 실행이 시작되거나 대기가 풀리면 거짓이 된다. */
  waiting: boolean;
};

/** 실행 게이트와 실행 방법. 페이지가 `useRunBacktest`에서 뽑아 넘긴다. */
export type BacktestTrigger = { canRun: boolean; run: () => void };

/** 실행을 이을 때까지 기억해 두는 요청. 적용한 텍스트와 그 직전 버전에 묶는다. */
type ArmedRun = { source: string; version: number; documentEpoch: number };

type ChainPhase = "idle" | "waiting" | "cancelled" | "ready" | "blocked";

/**
 * 대기의 다음 상태를 지금 문서 상태만으로 정한다. 저장하는 상태가 요청 하나뿐이라(effect에서 상태를
 * 바꾸지 않는다) 렌더 순서에 기대지 않는다.
 */
const chainPhase = (
  armed: ArmedRun | null,
  status: ProposalApplyStatus,
  state: DocumentState,
  canRun: boolean,
): ChainPhase => {
  if (armed === null || armed.documentEpoch !== state.documentEpoch)
    return "idle";
  // 확인 창에서 취소했거나 적용이 중단됐다.
  if (status.kind === "idle" || status.kind === "failed") return "cancelled";
  // 확인 창이 떠 있는 동안은 기다린다 — 덮어쓰기를 누르면 그대로 이어진다.
  if (status.kind !== "applied") return "waiting";
  if (state.source !== armed.source)
    // 같은 버전이면 편집기 change가 아직 reducer에 닿지 않은 것이고, 버전이 지났으면 사용자가
    // 기다리는 동안 문서를 또 고친 것이다.
    return state.sourceVersion === armed.version ? "waiting" : "cancelled";
  if (state.compiledVersion === state.sourceVersion)
    return canRun ? "ready" : "blocked";
  // 구문 오류면 compile 자체가 시작되지 않는다 — 그대로 두면 영원히 기다린다.
  const parseFailed =
    state.parsedVersion === state.sourceVersion &&
    state.parse !== null &&
    state.parse.status !== "ok";
  return parseFailed ? "blocked" : "waiting";
};

/**
 * "적용 후 백테스트"를 한 번의 사용자 클릭으로 잇는다(WORKFLOW B-04).
 *
 * 실행은 적용 직후에 바로 되지 않는다: 적용 → 편집기 change → reducer `edit` → parse·compile
 * 디바운스가 끝나야 실행 게이트(`canRun`)가 열린다. 그래서 적용한 텍스트를 기억해 두고 그 텍스트의
 * 검증이 끝나는 순간에만 실행을 잇는다. 자동 실행이 아니라 사용자가 누른 한 동작의 뒷부분이다
 * (자동 적용·자동 백테스트 금지는 그대로다, spec Non-goals).
 *
 * 대기는 네 가지로 풀린다. 확인 창에서 취소하거나 적용이 중단되면, 기다리는 동안 사용자가 문서를 또
 * 고치면, 검증이 끝나 실행 가능하면(실행), 검증이 끝났는데 실행할 수 없으면(실행하지 않음 — 이유는
 * Problems 패널과 툴바가 이미 소유한 사실이라 여기서 다시 말하지 않는다).
 */
export const useApplyProposalThenBacktest = (
  apply: AssistantProposalApply,
  state: DocumentState,
  backtest: BacktestTrigger,
): ProposalBacktestChain => {
  const [armed, setArmed] = useState<ArmedRun | null>(null);
  // 이미 실행을 이은 요청. 실행이 시작되면 게이트가 닫혀 phase가 바뀌므로 중복 실행만 막으면 된다.
  const fired = useRef<ArmedRun | null>(null);
  const applyProposal = apply.apply;
  const { canRun, run } = backtest;
  const { documentEpoch, sourceVersion } = state;

  const applyThenBacktest = useCallback(
    (proposal: AssistantProposal): void => {
      setArmed({
        source: proposal.source,
        version: sourceVersion,
        documentEpoch,
      });
      applyProposal(proposal);
    },
    [applyProposal, documentEpoch, sourceVersion],
  );

  const phase = chainPhase(armed, apply.status, state, canRun);

  // 검증이 끝나는 시점은 렌더 사이에만 알 수 있어 effect로 잇는다. 여기서 하는 일은 실행 호출 하나다.
  useEffect(() => {
    if (armed === null || phase !== "ready" || fired.current === armed) return;
    fired.current = armed;
    run();
  }, [armed, phase, run]);

  return { applyThenBacktest, waiting: phase === "waiting" };
};
