import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  assistantProviderApi,
  refreshAssistantProviders,
  refreshIfProviderGone,
  type CreateProviderProfileInput,
} from "../../../entities/assistant";
import { t } from "../../../shared/config";
import {
  probeFailureMessage,
  providerRejection,
  type ProviderRejection,
} from "./provider-copy";

/** 카드에 인라인으로 남기는 연결 테스트 결과. 키와 무관한 값만 담는다. */
export type ProbeOutcome = {
  ok: boolean;
  message: string;
  latencyMs: number | null;
};

/**
 * 프로파일 생성 명령.
 *
 * react-query mutation을 **쓰지 않는다**. mutation은 인자를 `Mutation.state.variables`에 보관하고
 * `MutationCache`에 남기므로, 그 경로로 보내면 제출한 API 키 평문이 앱 전역 `QueryClient` 안에 남는다
 * (설정 화면이 열려 있는 동안은 gc도 일어나지 않는다 — B-01 리뷰 P1-1). 키는 폼의 비제어 입력에서
 * 읽혀 이 함수의 호출 인자로만 흐르고, 호출이 끝나면 어떤 state·캐시·클로저도 그 값을 참조하지 않는다.
 * 남기는 것은 진행 중 여부(`pending`)와 반환된 거부 사유뿐이다.
 */
export const useCreateProvider = () => {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  const submit = async (
    input: CreateProviderProfileInput,
  ): Promise<ProviderRejection | null> => {
    setPending(true);
    try {
      await assistantProviderApi.createProvider(input);
      await refreshAssistantProviders(queryClient);
      return null;
    } catch (error) {
      return providerRejection(error);
    } finally {
      setPending(false);
    }
  };

  return { pending, submit };
};

/**
 * 연결 테스트 명령.
 *
 * 저장된 프로파일 id만 보내므로 키를 싣지 않지만, 생성과 같은 모양으로 둔다.
 *
 * 진행 중인 id를 **집합으로** 든다. 슬롯 하나짜리 state(또는 공유 mutation의 `variables`)로 판정하면
 * 응답이 빠른 카드가 먼저 끝나면서 아직 비행 중인 느린 카드의 잠금까지 풀어 버린다 — 공급자마다
 * 응답 속도가 다른 실전에서 흔한 경우다(리뷰 P3-2·R2-1). 집합에서 끝난 id만 빼면 카드별 잠금이
 * 실제로 성립한다. B-02 이후의 멱등하지 않은 명령(턴 시작·취소)이 이 파일을 본보기로 삼는다.
 */
export const useProbeProvider = () => {
  const queryClient = useQueryClient();
  const [probingIds, setProbingIds] = useState<ReadonlySet<string>>(new Set());

  const probe = async (profileId: string): Promise<ProbeOutcome> => {
    setProbingIds((previous) => new Set(previous).add(profileId));
    try {
      const result = await assistantProviderApi.testProvider(profileId);
      return {
        ok: result.ok,
        message: result.ok
          ? t("assistant.provider.test.ok")
          : probeFailureMessage(result.failure),
        latencyMs: result.latency_ms,
      };
    } catch (error) {
      refreshIfProviderGone(queryClient, error);
      return {
        ok: false,
        message: providerRejection(error).message,
        latencyMs: null,
      };
    } finally {
      setProbingIds((previous) => {
        const next = new Set(previous);
        next.delete(profileId);
        return next;
      });
    }
  };

  return { isProbing: (profileId: string) => probingIds.has(profileId), probe };
};
