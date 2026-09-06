import { useId, useState, type ReactNode } from "react";

import { t } from "../../../shared/config";
import { useMediaQuery } from "../../../shared/lib/media";
import { Link } from "../../../shared/lib/router";
import { Badge, Button } from "../../../shared/ui";
import "./app-shell.css";

type AppShellProps = {
  operationsEnabled: boolean;
  children: ReactNode;
};

const RESEARCH = [
  { key: "research", label: "nav.research", icon: "⚗", to: null },
  {
    key: "strategies",
    label: "nav.strategies",
    icon: "↗",
    to: "/research/strategies",
  },
  { key: "backtests", label: "nav.backtests", icon: "▤", to: null },
  { key: "experiments", label: "nav.experiments", icon: "▦", to: null },
] as const;

const OPERATIONS = [
  { to: "/operations/deployments", label: "nav.deployments", icon: "➜" },
  { to: "/operations/realtime", label: "nav.realtime", icon: "∿" },
  { to: "/operations/orders", label: "nav.orders", icon: "▥" },
  { to: "/operations/risk", label: "nav.risk", icon: "⛨" },
] as const;

/**
 * Persistent frame from the concept: brand, research group (연구 · 전략 · 백테스트 · 실험),
 * operations group marked 향후 (배포 · 실시간 · 주문 · 리스크), collapse control; the routed
 * page fills the rest. Entries without a screen yet are disabled text that assistive technology
 * hears as "coming later, not available", in both flag states, so nothing reads as live trading.
 */
export const AppShell = ({ operationsEnabled, children }: AppShellProps) => {
  const operationsId = useId();
  const compactViewport = useMediaQuery("(max-width: 1279px)");
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(null);
  const collapsed = manualCollapsed ?? compactViewport;
  const unavailable = t("nav.operations.unavailable");
  const disabledItem = (label: string, icon: string) => (
    <span
      className="app-shell__link app-shell__link--disabled"
      aria-disabled="true"
    >
      <span className="app-shell__icon" aria-hidden="true">
        {icon}
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
              {item.to ? (
                <Link
                  to={item.to}
                  search={{}}
                  className="app-shell__link"
                  activeProps={{ "aria-current": "page" }}
                >
                  <span className="app-shell__icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="app-shell__label">{t(item.label)}</span>
                </Link>
              ) : (
                disabledItem(t(item.label), item.icon)
              )}
            </li>
          ))}
          <li>
            <Link
              to="/legacy/builder"
              search={{}}
              className="app-shell__link app-shell__link--secondary"
              activeProps={{ "aria-current": "page" }}
            >
              <span className="app-shell__icon" aria-hidden="true">
                ⌂
              </span>
              <span className="app-shell__label">{t("nav.legacyBuilder")}</span>
            </Link>
          </li>
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
                    {item.icon}
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
        <div className="app-shell__collapse">
          <Button
            size="small"
            tone="ghost"
            aria-expanded={!collapsed}
            onClick={() => setManualCollapsed(!collapsed)}
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
