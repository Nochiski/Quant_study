/**
 * Router import boundary (router ADR P0-04, D1). Pages, widgets and features use the router only
 * through these names so the dependency stays swappable and lint-visible. The route tree itself
 * lives in `app/router`.
 */
export {
  Link,
  Outlet,
  notFound,
  redirect,
  useBlocker,
  useNavigate,
  useParams,
  useRouterState,
  useSearch,
} from "@tanstack/react-router";
