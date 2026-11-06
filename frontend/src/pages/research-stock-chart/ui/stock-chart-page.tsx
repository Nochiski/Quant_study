import { t } from "../../../shared/config";
import { useNavigate, useSearch } from "../../../shared/lib/router";
import { ChartDataBasis, ChartSaveStatus, StockChart } from "../../../widgets/stock-chart";
import type { StockChartSearch } from "../../../entities/chart";
import "./stock-chart-page.css";

/** `/research/chart`: 개별종목차트. 조건(종목·주기·기간)은 주소가 갖는다 — 뒤로 가기가 이전 조건을 되살린다. */
export const StockChartPage = () => {
  const search = useSearch({ from: "/research/chart" });
  const navigate = useNavigate();
  const onSearch = (next: StockChartSearch) => void navigate({ to: "/research/chart", search: next });
  const onReplaceSearch = (next: StockChartSearch) =>
    void navigate({ to: "/research/chart", search: next, replace: true });
  return (
    <section className="page stock-chart-page" aria-labelledby="stock-chart-title">
      <header className="stock-chart-page__header">
        <div>
          <h1 id="stock-chart-title">{t("chart.page.title")}</h1>
          <ChartDataBasis search={search} />
        </div>
        <ChartSaveStatus search={search} />
      </header>
      <StockChart search={search} onSearch={onSearch} onReplaceSearch={onReplaceSearch} />
    </section>
  );
};
