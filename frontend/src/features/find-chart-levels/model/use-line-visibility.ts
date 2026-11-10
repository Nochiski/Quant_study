import { useState } from "react";

import { useUpdateChartLine, type ChartLineBody } from "../../../entities/chart";

/**
 * 자동 후보의 보이기 저장 칸 바꾸기(묶음은 함께 — 수평 구간 한 벌, "이것만"은 고른 띠를 켜고 나머지를 끈다).
 * 고치기 요청은 entities `useUpdateChartLine`이 한 줄로 차례대로 보내고 누르는 순간 체크 칸을 바꿔 둔다. 일부만
 * 실패하면 그 줄들은 서버 값으로 돌아가고(`onError`가 목록을 다시 읽는다) 실패한 선 수를 `failed`로 알린다.
 */
export const useLineVisibility = () => {
  const update = useUpdateChartLine();
  const [failed, setFailed] = useState(0);

  const setVisible = (lines: readonly ChartLineBody[], visible: boolean) => {
    void Promise.allSettled(lines.map((line) => update.mutateAsync({ line, patch: { visible } }))).then(
      (results) => {
        const count = results.filter((result) => result.status === "rejected").length;
        if (count > 0) setFailed((current) => current + count);
      },
    );
  };

  return { setVisible, failed, dismiss: () => setFailed(0) };
};
