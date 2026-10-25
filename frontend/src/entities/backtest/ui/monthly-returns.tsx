import type { BacktestRunResult } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { SignedValue } from "../../../shared/ui";

type MonthlyPoint = BacktestRunResult["series"]["monthly_returns"][number];

/** 펼쳐 둘 최근 연도 수. 이전 연도는 접어 둔다(RD-07-D). */
const RECENT_YEARS = 3;
const MONTHS = Array.from({ length: 12 }, (_, index) => index + 1);

/** 표 이름에 연도 범위를 붙여 구역 이름과 가른다. */
const rangeLabel = (title: string, years: readonly number[]) =>
  years.length === 1
    ? `${title} ${years[0]}`
    : `${title} ${years[0]}–${years.at(-1)}`;

const YearTable = ({
  label,
  years,
  values,
}: {
  label: string;
  years: readonly number[];
  values: ReadonlyMap<string, number>;
}) => (
  <div className="result-table-wrap" role="region" tabIndex={0} aria-label={label}>
    <table className="monthly-table">
      <caption className="sr-only">{label}</caption>
      <thead>
        <tr>
          <th scope="col">{t("backtest.monthly.year")}</th>
          {MONTHS.map((month) => (
            <th key={month} scope="col">
              {tFill("backtest.monthly.month", { month })}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {years.map((year) => (
          <tr key={year}>
            <th scope="row">{year}</th>
            {MONTHS.map((month) => {
              const value = values.get(`${year}-${month}`);
              return (
                <td
                  key={month}
                  className={
                    value === undefined || value === 0
                      ? undefined
                      : value > 0
                        ? "monthly-table__up"
                        : "monthly-table__down"
                  }
                >
                  {value === undefined ? null : (
                    <SignedValue
                      value={value}
                      magnitude={`${(Math.abs(value) * 100).toFixed(1)}%`}
                    />
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

/**
 * 월별 수익률을 연도 × 월 표로 보인다. 칸마다 부호 있는 숫자를 쓰고 최근 세 해만 펼친다. 연간 합계는
 * 서버가 주지 않아 표시하지 않는다 — 월 수익률을 화면에서 묶으면 지표 공식을 복제하게 된다.
 */
export const MonthlyReturns = ({
  id,
  points,
}: {
  id: string;
  points: readonly MonthlyPoint[];
}) => {
  const values = new Map(
    points.map((point) => [`${point.year}-${point.month}`, point.value]),
  );
  const years = [...new Set(points.map((point) => point.year))].sort(
    (left, right) => left - right,
  );
  const recent = years.slice(-RECENT_YEARS);
  const earlier = years.slice(0, -RECENT_YEARS);
  const title = t("backtest.monthly.title");
  return (
    <section id={id} tabIndex={-1} className="monthly-panel" aria-label={title}>
      <header>
        <h4>{title}</h4>
        <span>{t("backtest.monthly.legend")}</span>
      </header>
      {years.length === 0 ? (
        <p className="inline-state">{t("backtest.monthly.empty")}</p>
      ) : (
        <YearTable label={rangeLabel(title, recent)} years={recent} values={values} />
      )}
      {earlier.length === 0 ? null : (
        <details className="ui-disclosure">
          <summary>
            {tFill("backtest.monthly.earlier", {
              from: earlier[0],
              to: earlier.at(-1),
            })}
          </summary>
          <YearTable
            label={rangeLabel(title, earlier)}
            years={earlier}
            values={values}
          />
        </details>
      )}
    </section>
  );
};
