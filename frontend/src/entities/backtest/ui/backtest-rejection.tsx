import { t } from "../../../shared/config";
import "./backtest-rejection.css";

type BacktestRejectionProps = {
  /** 문장 앞의 짧은 제목(예: "백테스트 시작 실패"). 없으면 문장만 보인다. */
  title?: string;
  /** 번역된 본문 문장. 서버 원문을 넣지 않는다. */
  message: string;
  /** 접힌 "서버 사유"에 둘 서버 원문·진단. 없으면 상세를 그리지 않는다. */
  detail: string | null;
  className?: string;
};

/**
 * 서버가 거절한 백테스트 요청 한 줄. 본문은 번역된 문장이고 서버 원문은 접힌 상세로 내린다 — 결과 화면의
 * run 실패 표시와 같은 방식이다(#260, #268 리뷰 P3-3). 편집기 툴바와 결과 화면 실행 제어가 함께 쓴다.
 */
export const BacktestRejection = ({
  title,
  message,
  detail,
  className,
}: BacktestRejectionProps) => (
  <div
    className={["backtest-rejection", className].filter(Boolean).join(" ")}
    role="alert"
  >
    {title === undefined ? message : `${title}: ${message}`}
    {detail === null ? null : (
      <details className="backtest-rejection__reason">
        <summary>{t("backtest.start.serverReason")}</summary>
        {detail}
      </details>
    )}
  </div>
);
