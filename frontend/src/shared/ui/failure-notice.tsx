import { t } from "../config";

type FailureNoticeProps = {
  /** 문장 앞의 짧은 제목(예: "백테스트 시작 실패"). 알림의 접근 가능한 이름도 된다. 없으면 문장만 보인다. */
  title?: string;
  /** 번역된 본문 문장. 서버 원문을 넣지 않는다. */
  message: string;
  /** 접힌 "서버 사유"에 둘 원문(`failureReason`). 없으면 상세를 그리지 않는다. */
  reason: string | null;
  className?: string;
};

/**
 * 실패 한 줄: 번역된 본문과 접힌 서버 사유. 서버 원문은 본문에 쓰지 않는다
 * (`.claude/rules/frontend-api-state.md`). 백테스트 시작·재실행 거절과 run 실패, 업그레이드·추적·실행
 * 계획·서버 초안 실패가 모두 이 모양을 쓴다(#270).
 */
export const FailureNotice = ({
  title,
  message,
  reason,
  className,
}: FailureNoticeProps) => (
  <div
    className={["ui-failure", className].filter(Boolean).join(" ")}
    role="alert"
    aria-label={title}
  >
    {title === undefined ? message : `${title}: ${message}`}
    {reason === null ? null : (
      <details className="ui-failure__reason">
        <summary>{t("ui.failure.serverReason")}</summary>
        {reason}
      </details>
    )}
  </div>
);
