import { useId, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import { Link } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import { NavigationIcon } from "./navigation-icon";
import "./app-shell.css";

type AppShellProps = {
  operationsEnabled: boolean;
  children: ReactNode;
};

const RESEARCH = [
  {
    key: "stockChart",
    label: "nav.stockChart",
    icon: "stockChart",
    to: "/research/chart",
  },
  { key: "home", label: "nav.home", icon: "home", to: "/", exact: true },
  {
    key: "strategies",
    label: "nav.strategies",
    icon: "strategies",
    to: "/research/strategies",
  },
  {
    key: "backtests",
    label: "nav.backtests",
    icon: "backtests",
    to: "/research/backtests",
  },
  {
    key: "experiments",
    label: "nav.experiments",
    icon: "experiments",
    to: "/research/experiments",
  },
  {
    key: "factors",
    label: "nav.factors",
    icon: "factors",
    to: "/research/factors",
  },
] as const;

const OPERATIONS = [
  { to: "/operations/deployments", label: "nav.deployments", icon: "deployments" },
  { to: "/operations/realtime", label: "nav.realtime", icon: "realtime" },
  { to: "/operations/orders", label: "nav.orders", icon: "orders" },
  { to: "/operations/risk", label: "nav.risk", icon: "risk" },
] as const;

/**
 * 앱 틀: 브랜드, 연구 묶음(개별종목차트 · 홈 · 전략 · 백테스트 · 실험 · 내 팩터), "향후" 표시를 단 운영 묶음
 * (배포 · 실시간 · 주문 · 리스크), 접기. 나머지는 라우트 화면이 채운다. 아직 화면이 없는 운영 항목은 보조 기술이
 * "향후 제공, 사용 불가"로 듣는 비활성 글자라, 기능 플래그와 무관하게 실거래처럼 읽히지 않는다.
 */
export const AppShell = ({ operationsEnabled, children }: AppShellProps) => {
  const operationsId = useId();
  const compactViewport = useMediaQuery("(max-width: 1279px)");
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(null);
  const collapsed = manualCollapsed ?? compactViewport;
  const unavailable = t("nav.operations.unavailable");
  const disabledItem = (label: string, icon: Parameters<typeof NavigationIcon>[0]["name"]) => (
    <span
      className="app-shell__link app-shell__link--disabled"
      aria-disabled="true"
    >
      <span className="app-shell__icon" aria-hidden="true">
        <NavigationIcon name={icon} />
      </span>
      <span className="app-shell__label">{label}</span>
      <span className="sr-only">({unavailable})</span>
    </span>
  );
  return (
    <div
      className={`app-shell ${collapsed ? "app-shell--collapsed" : ""}`.trim()}
    >
      <a className="app-shell__skip" href="#app-main">
        {t("shell.skipToContent")}
      </a>
      <nav className="app-shell__nav" aria-label={t("shell.navigation")}>
        <div className="app-shell__brand">{t("shell.appName")}</div>
        <ul className="app-shell__list">
          {RESEARCH.map((item) => (
            <li key={item.key}>
              <Link
                to={item.to}
                search={{}}
                className="app-shell__link"
                activeProps={{ "aria-current": "page" }}
                activeOptions={{ exact: "exact" in item && item.exact }}
              >
                <span className="app-shell__icon" aria-hidden="true">
                  <NavigationIcon name={item.icon} />
                </span>
                <span className="app-shell__label">{t(item.label)}</span>
              </Link>
            </li>
          ))}
        </ul>
        <p className="app-shell__group" id={operationsId}>
          <span className="app-shell__label">
            {t("nav.operations")}{" "}
            <Badge tone="neutral">{t("nav.operations.future")}</Badge>
          </span>
        </p>
        <ul className="app-shell__list" aria-labelledby={operationsId}>
          {OPERATIONS.map((item) => (
            <li key={item.to}>
              {operationsEnabled ? (
                <Link
                  to={item.to}
                  className="app-shell__link"
                  activeProps={{ "aria-current": "page" }}
                >
                  <span className="app-shell__icon" aria-hidden="true">
                    <NavigationIcon name={item.icon} />
                  </span>
                  <span className="app-shell__label">{t(item.label)}</span>
                  <span className="sr-only">({unavailable})</span>
                </Link>
              ) : (
                disabledItem(t(item.label), item.icon)
              )}
            </li>
          ))}
        </ul>
        <ul className="app-shell__list app-shell__list--footer">
          <li>
            <Link
              to="/settings"
              className="app-shell__link"
              activeProps={{ "aria-current": "page" }}
            >
              <span className="app-shell__icon" aria-hidden="true">
                <NavigationIcon name="settings" />
              </span>
              <span className="app-shell__label">{t("nav.settings")}</span>
            </Link>
          </li>
        </ul>
        <div className="app-shell__collapse">
          <Button
            size="small"
            tone="ghost"
            aria-expanded={!collapsed}
            onClick={(event) => {
              const control = event.currentTarget;
              setManualCollapsed(!collapsed);
              // 메뉴 높이가 바뀐 뒤에도 조작한 버튼의 초점을 화면 안에 유지한다.
              requestAnimationFrame(() =>
                control.scrollIntoView({ block: "nearest", inline: "nearest" }),
              );
            }}
          >
            <span aria-hidden="true">{collapsed ? "»" : "«"}</span>
            <span className="sr-only">
              {collapsed ? t("shell.expandNav") : t("shell.collapseNav")}
            </span>
          </Button>
        </div>
      </nav>
      <main id="app-main" className="app-shell__main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
};
