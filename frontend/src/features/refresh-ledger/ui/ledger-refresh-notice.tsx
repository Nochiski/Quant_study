import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { RefreshOutcome } from "../../../shared/api";
import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import {
  ledgerRefreshQuery,
  refreshAfterApply,
} from "../model/refresh-queries";
import "./ledger-refresh.css";

interface Notice {
  finishedAt: string;
  outcome: RefreshOutcome;
  // 지금 상태가 아직 그 결과를 말할 때만(끝난 뒤 계산을 시작하면 상태 문장이 바뀐다)
  message: string | null;
}

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
    data.last_outcome !== null &&
    data.last_outcome !== undefined
  ) {
    // 결과 갈래는 backend 가 싣는다 — 그 뒤 code 가 바뀌어도(`busy_work`·`locked`) 그대로다.
    const outcome = data.last_outcome;
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
