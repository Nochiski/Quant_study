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
  /**
   * 적용은 끝났지만 실행할 수 없어 백테스트를 시작하지 않았다. 그 적용 결과가 화면에 남아 있는 동안만
   * 참이다 — 문서를 고치거나 다른 제안을 적용하면 거짓이 된다.
   */
  notStarted: boolean;
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
 * 고치면, 검증이 끝나 실행 가능하면(실행), 검증이 끝났는데 실행할 수 없으면(실행하지 않음).
 *
 * 마지막 경우는 요청을 **버린다**. 실행 설정이 무효이거나 앞선 실행이 도는 등 문서와 무관한 이유로
 * 게이트가 닫혀 있을 수 있는데, 요청을 남겨 두면 대기 표시가 사라진 뒤 게이트가 열리는 순간 예고 없이
 * 실행된다(Phase B 감사 NB-2). 대신 `notStarted`로 "적용했지만 백테스트는 시작하지 않았다"를 알린다.
 * 왜 실행할 수 없는지는 Problems 패널과 툴바가 이미 소유한 사실이라 여기서 다시 말하지 않는다.
 */
export const useApplyProposalThenBacktest = (
  apply: AssistantProposalApply,
  state: DocumentState,
  backtest: BacktestTrigger,
): ProposalBacktestChain => {
  const [armed, setArmed] = useState<ArmedRun | null>(null);
  // 실행하지 않고 버린 요청이 묶였던 적용 결과. 적용 훅은 적용마다 새 상태 객체를 만들고, 문서가
  // 바뀌면 그 객체를 내려놓으므로 객체가 같을 때만 알림을 보인다.
  const [notStartedFor, setNotStartedFor] =
    useState<ProposalApplyStatus | null>(null);
  // 실행하기로 판정해 대기에서 꺼낸 요청. effect가 이것을 보고 실행을 한 번 부른다.
  const [firing, setFiring] = useState<ArmedRun | null>(null);
  // 실행을 이미 부른 요청. `run`의 정체성이 바뀌어 effect가 다시 돌아도 두 번 부르지 않는다.
  const fired = useRef<ArmedRun | null>(null);
  const applyProposal = apply.apply;
  const { canRun, run } = backtest;
  const { documentEpoch, sourceVersion } = state;

  const applyThenBacktest = useCallback(
    (proposal: AssistantProposal): void => {
      setNotStartedFor(null);
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
  // 대기가 끝나는 두 판정(실행·실행하지 않음)은 그 렌더에서 요청을 대기에서 꺼낸다. 렌더 중에 자기
  // 상태를 고치는 것은 React가 허용한 조정 방식이고, effect로 미루면 게이트가 바뀌는 렌더와 순서가
  // 갈릴 수 있다. 실행으로 꺼낸 요청도 대기에 남겨 두면, 시작된 실행이 게이트를 닫는 순간 그 요청이
  // "실행할 수 없음"으로 읽혀 접수된 백테스트를 두고 시작하지 않았다고 알리게 된다.
  if (armed !== null && phase === "ready") {
    setArmed(null);
    setFiring(armed);
  } else if (armed !== null && phase === "blocked") {
    setArmed(null);
    setNotStartedFor(apply.status);
  }

  // 검증이 끝나는 시점은 렌더 사이에만 알 수 있어 effect로 잇는다. 여기서 하는 일은 실행 호출 하나다.
  useEffect(() => {
    if (firing === null || fired.current === firing) return;
    fired.current = firing;
    run();
  }, [firing, run]);

  return {
    applyThenBacktest,
    waiting: phase === "waiting",
    notStarted: notStartedFor !== null && notStartedFor === apply.status,
  };
};
