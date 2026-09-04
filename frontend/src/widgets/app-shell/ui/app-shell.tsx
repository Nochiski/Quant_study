import type { ReactNode } from "react";

import { t } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Badge } from "../../../shared/ui";
import "./app-shell.css";

type AppShellProps = {
  operationsEnabled: boolean;
  children: ReactNode;
};

const OPERATIONS = [
  { to: "/operations/deployments", label: "nav.deployments" },
  { to: "/operations/orders", label: "nav.orders" },
  { to: "/operations/positions", label: "nav.positions" },
  { to: "/operations/risk", label: "nav.risk" },
] as const;

/**
 * Persistent frame: left navigation (research now, operations later) and the routed content.
 * Operations entries are rendered as disabled text with a "future" badge while the feature flag
 * is off so they can never be mistaken for live trading controls.
 */
export const AppShell = ({ operationsEnabled, children }: AppShellProps) => (
  <div className="app-shell">
    <a className="app-shell__skip" href="#app-main">
      {t("shell.skipToContent")}
    </a>
    <nav className="app-shell__nav" aria-label={t("shell.navigation")}>
      <div className="app-shell__brand">{t("shell.appName")}</div>
      <p className="app-shell__group">{t("nav.research")}</p>
      <ul className="app-shell__list">
        <li>
          <Link
            to="/research/strategies/new"
            className="app-shell__link"
            activeProps={{ "aria-current": "page" }}
          >
            {t("nav.strategies")}
          </Link>
        </li>
        <li>
          <Link
            to="/legacy/builder"
            search={{}}
            className="app-shell__link"
            activeProps={{ "aria-current": "page" }}
          >
            {t("nav.legacyBuilder")}
          </Link>
        </li>
      </ul>
      <p className="app-shell__group">
        {t("nav.operations")}{" "}
        {operationsEnabled ? null : (
          <Badge tone="neutral">{t("nav.operations.future")}</Badge>
        )}
      </p>
      <ul className="app-shell__list">
        {OPERATIONS.map((item) =>
          operationsEnabled ? (
            <li key={item.to}>
              <Link
                to={item.to}
                className="app-shell__link"
                activeProps={{ "aria-current": "page" }}
              >
                {t(item.label)}
              </Link>
            </li>
          ) : (
            <li key={item.to}>
              <span
                className="app-shell__link app-shell__link--disabled"
                aria-disabled="true"
              >
                {t(item.label)}
              </span>
            </li>
          ),
        )}
      </ul>
    </nav>
    <main id="app-main" className="app-shell__main" tabIndex={-1}>
      {children}
    </main>
  </div>
);
