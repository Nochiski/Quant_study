import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";
import {
  ledgerRefreshQuery,
  refreshAfterApply,
} from "../model/refresh-queries";
import "./ledger-refresh.css";

const FINISHED = new Set(["succeeded", "failed", "partial"]);

/**
 * DB 새로고침이 끝났다는 알림(어느 화면에서든). 처음 읽었을 때 이미 끝나 있던 결과는 알리지 않는다.
 * 반영에 성공하면 화면의 모든 서버 조회를 다시 읽는다 — 새 데이터로 다시 세운 앱이 답한다.
 */
export const LedgerRefreshNotice = () => {
  const queryClient = useQueryClient();
  const status = useQuery(ledgerRefreshQuery());
  const [known, setKnown] = useState<string | null | undefined>(undefined);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const data = status.data;
  if (known === undefined && data !== undefined) {
    // 처음 읽은 상태의 끝난 시각은 알리지 않는다(렌더 중 상태 맞추기).
    setKnown(data.finished_at ?? null);
  }
  const finishedAt = data?.finished_at ?? null;
  const fresh =
    data !== undefined &&
    known !== undefined &&
    finishedAt !== null &&
    finishedAt !== known &&
    FINISHED.has(data.code);
  const succeeded = fresh && data?.code === "succeeded";

  useEffect(() => {
    if (succeeded) void refreshAfterApply(queryClient);
  }, [succeeded, finishedAt, queryClient]);

  if (!fresh || data === undefined || dismissed === finishedAt) return null;
  const failed = data.code !== "succeeded";
  return (
    <div className="refresh-notice">
      <div className="refresh-notice__item" role={failed ? "alert" : "status"}>
        <strong>
          {t(
            `refresh.notice.${data.code as "succeeded" | "failed" | "partial"}`,
          )}
        </strong>
        <span>{data.message}</span>
        <Button
          size="small"
          tone="ghost"
          onClick={() => setDismissed(finishedAt)}
        >
          {t("refresh.notice.dismiss")}
        </Button>
      </div>
    </div>
  );
};
