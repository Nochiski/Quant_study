import type { BacktestRunState } from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";
import { FailureNotice } from "../../../shared/ui";

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
  const translated = run.error_code
    ? tOptional(`backtest.run.error.${run.error_code}`)
    : null;
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
