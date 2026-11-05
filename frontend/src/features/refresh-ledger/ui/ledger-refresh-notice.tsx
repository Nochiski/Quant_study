import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { LedgerRefreshBody } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import {
  ledgerRefreshQuery,
  refreshAfterApply,
} from "../model/refresh-queries";
import "./ledger-refresh.css";

type Outcome = "succeeded" | "failed" | "partial";

interface Notice {
  finishedAt: string;
  outcome: Outcome;
  // 지금 상태가 아직 그 결과를 말할 때만(끝난 뒤 계산을 시작하면 상태 문장이 바뀐다)
  message: string | null;
}

// 끝난 받기의 결과. 실패 갈래가 없으면 반영에 성공한 것이다 — 그 뒤 계산이 시작돼 code 가
// 바뀌어도(`busy_work`) 결과는 같다.
const outcomeOf = (status: LedgerRefreshBody): Outcome =>
  status.failure === null || status.failure === undefined
    ? "succeeded"
    : status.code === "partial"
      ? "partial"
      : "failed";

/**
 * DB 새로고침이 끝났다는 알림(어느 화면에서든). 처음 읽었을 때 이미 끝나 있던 결과는 알리지 않는다.
 * 끝난 시각(`finished_at`)마다 한 번만 알리고, 반영에 성공했으면 그때 한 번 화면의 모든 서버 조회를
 * 다시 읽는다 — 새 데이터로 다시 세운 앱이 답한다.
 */
export const LedgerRefreshNotice = () => {
  const queryClient = useQueryClient();
  const data = useQuery(ledgerRefreshQuery()).data;
  const [known, setKnown] = useState<string | null | undefined>(undefined);
  const [notice, setNotice] = useState<Notice | null>(null);
  const finishedAt = data?.finished_at ?? null;
  // 렌더 중 상태 맞추기: 처음 읽은 끝난 시각은 기억만 하고, 새 끝난 시각에서 한 번 알린다.
  if (data !== undefined && known === undefined) {
    setKnown(finishedAt);
  } else if (
    data !== undefined &&
    finishedAt !== null &&
    finishedAt !== known &&
    !data.refreshing
  ) {
    const outcome = outcomeOf(data);
    setKnown(finishedAt);
    setNotice({
      finishedAt,
      outcome,
      message: data.code === outcome ? data.message : null,
    });
  }

  useEffect(() => {
    if (notice?.outcome === "succeeded") void refreshAfterApply(queryClient);
  }, [notice, queryClient]);

  if (notice === null) return null;
  return (
    <div className="refresh-notice">
      <div
        className="refresh-notice__item"
        role={notice.outcome === "succeeded" ? "status" : "alert"}
      >
        <strong>{t(`refresh.notice.${notice.outcome}`)}</strong>
        {notice.message === null ? null : <span>{notice.message}</span>}
        <Button size="small" tone="ghost" onClick={() => setNotice(null)}>
          {t("refresh.notice.dismiss")}
        </Button>
      </div>
    </div>
  );
};
