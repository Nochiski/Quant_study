import { useState } from "react";

import { useUpdateChartLine, type ChartLineBody, type ChartTarget } from "../../../entities/chart";
import { useCommittedRef } from "../../../shared/lib/react";

export type VisibilityFailure = { count: number; error: Error };

/**
 * 자동 후보의 보이기 저장 칸 바꾸기(묶음은 함께 — 수평 구간 한 벌, "이것만"은 고른 띠를 켜고 나머지를 끈다).
 * 고치기 요청은 entities `useUpdateChartLine`이 한 줄로 차례대로 보내고 누르는 순간 체크 칸을 바꿔 둔다. 일부만
 * 실패하면 그 줄들은 서버 값으로 돌아가고(`onError`가 목록을 다시 읽는다) 실패한 선 수와 마지막 서버 사유를
 * `failure`로 알린다.
 *
 * - 알림은 그 종목의 것이다 — 다른 종목으로 넘어가면 비우고, 종목을 바꾼 뒤에 온 앞 종목의 응답은 넣지 않는다
 *   (`useLineRemoval`과 같은 규칙).
 * - 새로 바꾸기 시작하면 앞 알림을 지운다(다시 눌러 성공했는데 낡은 알림이 남지 않게).
 */
export const useLineVisibility = (target: ChartTarget) => {
  const update = useUpdateChartLine();
  const [failure, setFailure] = useState<VisibilityFailure | null>(null);
  const scopeKey = `${target.kind}|${target.code}`;
  const [scope, setScope] = useState(scopeKey);
  if (scope !== scopeKey) {
    // 렌더 중 상태 맞추기: 다른 종목으로 넘어가면 알림을 버린다
    setScope(scopeKey);
    setFailure(null);
  }
  const scopeRef = useCommittedRef(scopeKey);

  const setVisible = (lines: readonly ChartLineBody[], visible: boolean) => {
    const requested = scopeKey;
    setFailure(null);
    void Promise.allSettled(lines.map((line) => update.mutateAsync({ line, patch: { visible } }))).then(
      (results) => {
        if (scopeRef.current !== requested) return;
        const rejected = results.filter((result) => result.status === "rejected");
        const last = rejected.at(-1);
        if (last === undefined) return;
        const error = last.reason instanceof Error ? last.reason : new Error(String(last.reason));
        setFailure((current) => ({ count: (current?.count ?? 0) + rejected.length, error }));
      },
    );
  };

  return { setVisible, failure, dismiss: () => setFailure(null) };
};
