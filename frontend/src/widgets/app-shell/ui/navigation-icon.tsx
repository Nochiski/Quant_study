type NavigationIconName =
  | "stockChart"
  | "home"
  | "strategies"
  | "backtests"
  | "experiments"
  | "factors"
  | "deployments"
  | "realtime"
  | "orders"
  | "risk"
  | "settings";

const PATHS: Record<NavigationIconName, string> = {
  // 캔들 셋(심지 + 몸통)
  stockChart: "M6 3v18M12 5v15M18 2v16M4.5 7h3v8h-3zM10.5 9h3v6h-3zM16.5 5h3v9h-3z",
  home: "M4 11 12 4l8 7M6 9.5V20h4v-6h4v6h4V9.5",
  strategies: "M4 18l5-6 4 3 7-10M14 5h6v6",
  backtests: "M4 4v16h16M8 16v-5M12 16V7M16 16v-6",
  experiments: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  factors: "M5 4h14v4H5zM5 10h14v4H5zM5 16h9v4H5z",
  deployments: "M12 16V3m-5 5 5-5 5 5M4 14v6h16v-6",
  realtime: "M3 12h4l3-7 4 14 3-7h4",
  orders: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  risk: "M12 3 4 6v6c0 4 3 7 8 9 5-2 8-5 8-9V6zM12 8v5M12 17h.01",
  settings: "M4 7h8m4 0h4M4 17h3m4 0h9M12 4v6M7 14v6",
};

/** 아이콘은 같은 viewBox·선 굵기를 쓰며 이름은 옆의 실제 메뉴 문구가 제공한다. */
export const NavigationIcon = ({ name }: { name: NavigationIconName }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d={PATHS[name]} />
  </svg>
);
