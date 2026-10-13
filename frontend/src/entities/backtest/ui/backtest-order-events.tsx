import { useId } from "react";
import type { BacktestRunResult } from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";

type Artifacts = BacktestRunResult["artifacts"];

/** 엔진 기록을 순서대로 표시한다. 체결 가능 여부나 원문 메시지의 의미를 다시 판정하지 않는다. */
export const BacktestOrderEvents = ({ artifacts }: { artifacts: Artifacts }) => {
  const titleId = useId();
  const updates = artifacts.order_updates;
  const securityByOrder = new Map(artifacts.orders.map((order) => [order.order_id, order.security_id]));
  const zeroCapEvents = updates?.filter((event) => event.reason_code === "liquidity_cap_zero").length ?? 0;
  return (
    <section className="trade-panel" aria-labelledby={titleId}>
      <header><h4 id={titleId}>{t("backtest.orderEvents.title")}</h4></header>
      {updates == null ? (
        <p className="inline-state">{t("backtest.orderEvents.unrecorded")}</p>
      ) : updates.length === 0 ? (
        <p className="inline-state">{t("backtest.orderEvents.empty")}</p>
      ) : (
        <>
          <p>{t("backtest.orderEvents.count").replace("{count}", String(updates.length))}</p>
          {zeroCapEvents > 0 ? <p>{t("backtest.orderEvents.zeroCapCount").replace("{count}", String(zeroCapEvents))}</p> : null}
          <details>
            <summary>{t("backtest.orderEvents.records")}</summary>
            <div className="result-table-wrap" tabIndex={0} role="region" aria-label={t("backtest.orderEvents.records")}>
              <table className="result-table">
                <thead><tr>
                  <th scope="col">{t("backtest.orderEvents.session")}</th>
                  <th scope="col">{t("backtest.orderEvents.order")}</th>
                  <th scope="col">{t("backtest.result.column.security")}</th>
                  <th scope="col">{t("backtest.orderEvents.status")}</th>
                  <th scope="col">{t("backtest.orderEvents.reason")}</th>
                  <th scope="col">{t("backtest.orderEvents.capVolume")}</th>
                  <th scope="col">{t("backtest.orderEvents.detail")}</th>
                </tr></thead>
                <tbody>{updates.map((event, index) => (
                  <tr key={`${event.order_id}-${event.session}-${index}`}>
                    <td>{event.session}</td>
                    <td><code>{event.order_id}</code></td>
                    <td><code>{securityByOrder.get(event.order_id) ?? "—"}</code></td>
                    <td>{tOptional(`backtest.orderEvents.status.${event.status}`) ?? event.status}</td>
                    <td>{event.reason_code == null ? "—" : tOptional(`backtest.orderEvents.reason.${event.reason_code}`) ?? event.reason_code}</td>
                    <td>{event.cap_volume ?? "—"}</td>
                    <td>{event.detail ?? "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
};
