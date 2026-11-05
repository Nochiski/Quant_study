import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  marketOverviewQuery,
  percentMagnitude,
  shortWon,
  type MarketOverviewBody,
} from "../../../entities/market";
import { t } from "../../../shared/config";
import { browserStorage } from "../../../shared/lib/browser-storage";
import { Button, SignedValue } from "../../../shared/ui";
import { FlowCard } from "./flow-card";
import { IndexCard } from "./index-card";
import { SectorCard } from "./sector-card";
import "./market-overview.css";

// 사용자별 편의라 브라우저 보관함으로 충분하다. 읽지 못하면 펼친 채로 둔다.
const COLLAPSED_KEY = "home.market.collapsed";

const readCollapsed = (): boolean => {
  try {
    return browserStorage()?.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
};

const writeCollapsed = (collapsed: boolean): void => {
  try {
    browserStorage()?.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // 보관함이 막혀도 이번 화면에서는 접힌다
  }
};

/** 접었을 때 남기는 한 줄: 지수 둘의 종가·등락과 코스피 외국인 당일 순매수. */
const Collapsed = ({ overview }: { overview: MarketOverviewBody }) => {
  const kospiFlow = overview.flows.find((card) => card.market === "KOSPI");
  const foreign = kospiFlow?.totals
    .find((total) => total.period === "day")
    ?.amounts.find((item) => item.investor === "foreign")?.amount_krw;
  return (
    <p className="market-card market-collapsed">
      {overview.indices.map((card) =>
        card.summary ? (
          <span key={card.index_id}>
            {card.index_name}{" "}
            {card.summary.close.toLocaleString("ko-KR", {
              minimumFractionDigits: 2,
            })}{" "}
            <SignedValue
              value={card.summary.change_pct ?? 0}
              magnitude={percentMagnitude(card.summary.change_pct ?? 0)}
            />
          </span>
        ) : null,
      )}
      {foreign !== undefined ? (
        <span>
          {t("market.investor.foreign")}({t("market.market.KOSPI")}){" "}
          <SignedValue
            kind="flow"
            value={foreign}
            magnitude={shortWon(foreign)}
          />
        </span>
      ) : null}
    </p>
  );
};

/**
 * 홈 시장 블록. 값과 사유 문장은 backend 가 정한다. 연습용 데이터로 켜졌으면 숫자를 하나도 그리지 않고
 * 안내만 보인다(연습용 숫자를 시장 정보처럼 보이지 않는다).
 */
export const MarketOverview = ({
  sector,
  onSector,
}: {
  sector: string | undefined;
  onSector: (code: string | undefined) => void;
}) => {
  const overview = useQuery(marketOverviewQuery());
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const titleId = useId();
  const bodyId = useId();
  const data = overview.data;
  const toggle = () => {
    writeCollapsed(!collapsed);
    setCollapsed(!collapsed);
  };

  let body;
  if (overview.isPending) {
    body = (
      <div
        className="market-grid"
        aria-busy="true"
        aria-label={t("market.loading")}
      >
        <div className="market-card market-skeleton" />
        <div className="market-card market-skeleton" />
        <div className="market-card market-skeleton market-skeleton--wide" />
      </div>
    );
  } else if (overview.isError || data === undefined) {
    body = (
      <div className="market-card" role="alert">
        <p>{t("market.error")}</p>
        <Button size="small" onClick={() => void overview.refetch()}>
          {t("market.retry")}
        </Button>
      </div>
    );
  } else if (data.status === "practice_data") {
    body = (
      <div className="market-card market-practice">
        <strong>{t("market.practice.title")}</strong>
        <p className="market-card__muted">{data.message}</p>
      </div>
    );
  } else if (collapsed) {
    body = <Collapsed overview={data} />;
  } else {
    body = (
      <div id={bodyId} className="market-body">
        <div className="market-grid">
          <div className="market-indices">
            {data.indices.map((card) => (
              <IndexCard
                key={card.index_id}
                card={card}
                session={data.session ?? null}
              />
            ))}
          </div>
          <FlowCard cards={data.flows} session={data.session ?? null} />
        </div>
        {data.sectors ? (
          <SectorCard
            card={data.sectors}
            session={data.session ?? null}
            sector={sector}
            onSector={onSector}
          />
        ) : null}
      </div>
    );
  }

  const practice = data?.status === "practice_data";
  return (
    <section className="market" aria-labelledby={titleId}>
      <div className="market__head">
        <div className="market__title">
          <h2 id={titleId}>{t("market.title")}</h2>
          {practice ? null : (
            <span className="market-card__muted">{t("market.caption")}</span>
          )}
        </div>
        {data !== undefined && !practice ? (
          <Button
            tone="ghost"
            size="small"
            aria-expanded={!collapsed}
            aria-controls={bodyId}
            onClick={toggle}
          >
            {collapsed ? t("market.expand") : t("market.collapse")}
          </Button>
        ) : null}
      </div>
      {body}
    </section>
  );
};
