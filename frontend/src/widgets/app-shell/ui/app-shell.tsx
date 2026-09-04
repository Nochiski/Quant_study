import { useId, type ReactNode } from "react";

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
 * Operations entries are placeholders in both flag states: while the flag is off they are
 * disabled text, while it is on they link to placeholder pages. Either way the group and every
 * item say "coming later / unavailable" to assistive technology, so they can never be mistaken
 * for live trading controls.
 */
export const AppShell = ({ operationsEnabled, children }: AppShellProps) => {
  const operationsId = useId();
  const future = t("nav.operations.future");
  const unavailable = t("nav.operations.unavailable");
  return (
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
        <p className="app-shell__group" id={operationsId}>
          {t("nav.operations")} <Badge tone="neutral">{future}</Badge>
        </p>
        <ul className="app-shell__list" aria-labelledby={operationsId}>
          {OPERATIONS.map((item) =>
            operationsEnabled ? (
              <li key={item.to}>
                <Link
                  to={item.to}
                  className="app-shell__link"
                  activeProps={{ "aria-current": "page" }}
                >
                  {t(item.label)}{" "}
                  <span className="sr-only">({unavailable})</span>
                </Link>
              </li>
            ) : (
              <li key={item.to}>
                <span
                  className="app-shell__link app-shell__link--disabled"
                  aria-disabled="true"
                >
                  {t(item.label)}{" "}
                  <span className="sr-only">({unavailable})</span>
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
};
