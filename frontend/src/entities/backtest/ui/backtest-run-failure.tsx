import {
  ApiRequestError,
  failureReason,
  type BacktestRunState,
} from "../../../shared/api";
import { t, tOptional, type MessageKey } from "../../../shared/config";
import { FailureNotice } from "../../../shared/ui";
import { backtestErrorSentence } from "../model/backtest-error";

type BacktestRunFailureProps = {
  run: Pick<BacktestRunState, "status" | "error" | "error_code">;
  className?: string;
  /** `FailureNotice`의 경고 알림 여부. 목록의 지난 실행은 `false`다. */
  announce?: boolean;
};

/**
 * run 실패 한 줄. 결과 화면과 백테스트 이력이 이 하나를 써서 같은 실패를 같은 문장으로 보인다(#304).
 * "failed" 배지만으로는 원인을 알 수 없어 사유를 보이되(#154), `error_code` 번역
 * `backtest.run.error.<code>`가 있으면 그 복구 문장을 본문으로 두고 서버 원문은 접힌 사유로 내린다(#158,
 * `.claude/rules/frontend-api-state.md`). 번역이 없을 때만 원문을 본문으로 쓴다. 취소와 겹친 실패는
 * "실행 오류" 대신 별도 제목을 단다.
 */
export const BacktestRunFailure = ({
  run,
  className,
  announce,
}: BacktestRunFailureProps) => {
  if (!run.error) return null;
  // 키를 `MessageKey` 로 만들어 backend 실패 코드(생성 SDK 유니온)마다 문구가 있음을 typecheck 가
  // 막는다(#362 DR-B-08). 생성 SDK 보다 새 코드가 오면 문구가 없어 서버 원문을 본문으로 쓴다.
  const key: MessageKey | null = run.error_code
    ? `backtest.run.error.${run.error_code}`
    : null;
  const translated = key === null ? null : tOptional(key);
  return (
    <FailureNotice
      className={className}
      title={t(
        run.status === "cancelled"
          ? "backtest.run.failedBeforeCancel"
          : "backtest.run.failed",
      )}
      message={translated ?? run.error}
      reason={translated === null ? null : run.error}
      announce={announce}
    />
  );
};

/**
 * 완료된 run의 결과 조회 실패 한 줄(#330). 결과 경로의 코드(결과 파일을 읽을 수 없는 410
 * `backtest.result.unreadable` 등)는 시작 거절과 같은 `backtest.error.<code>` 번역을 본문으로 쓰고, 서버 사유는
 * 접힌 상세로 내린다. 코드가 없거나 번역이 없으면 일반 문장이다. 실패가 없으면 그리지 않는다.
 */
export const BacktestResultFailure = ({
  error,
  className,
}: {
  error: unknown;
  className?: string;
}) =>
  error === null ? null : (
    <FailureNotice
      className={className}
      message={
        backtestErrorSentence(
          error instanceof ApiRequestError ? (error.code ?? null) : null,
        ) ?? t("backtest.result.failedGeneric")
      }
      reason={failureReason(error)}
    />
  );
