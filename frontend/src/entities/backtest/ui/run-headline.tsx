import type { RunHeadline as RunHeadlineValues } from "../../../shared/api";
import { t } from "../../../shared/config";
import { SignedValue } from "../../../shared/ui";
import "../../../shared/ui/data-list.css";

const percent = (value: number) => `${(Math.abs(value) * 100).toFixed(2)}%`;

/**
 * 완료 run 의 머리 지표(총수익률·샤프·최대 낙폭). 서버가 결과의 FULL 지표를 옮겨 실은 값이고 다시 계산하지
 * 않는다. 끝나지 않았거나 옛 판본에서 끝난 run 은 값이 없어 "—"다(RD-06-A).
 */
export const RunHeadline = ({ headline }: { headline: RunHeadlineValues | null | undefined }) => {
  if (headline == null) return <span aria-label={t("history.backtests.noHeadline")}>—</span>;
  return (
    <dl className="data-list-page__headline">
      <div>
        <dt>{t("history.backtests.totalReturn")}</dt>
        <dd>
          {headline.total_return == null ? "—" : (
            <SignedValue value={headline.total_return} magnitude={percent(headline.total_return)} />
          )}
        </dd>
      </div>
      <div>
        <dt>{t("history.backtests.sharpe")}</dt>
        <dd>{headline.sharpe == null ? "—" : headline.sharpe.toFixed(2)}</dd>
      </div>
      <div>
        <dt>{t("history.backtests.maxDrawdown")}</dt>
        <dd>
          {headline.max_drawdown == null ? "—" : (
            <SignedValue value={headline.max_drawdown} magnitude={percent(headline.max_drawdown)} />
          )}
        </dd>
      </div>
    </dl>
  );
};
