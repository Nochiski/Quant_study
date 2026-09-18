import { t, tOptional } from "../../../shared/config";
import type { TransactionFeedback as Feedback } from "../model/use-source-transactions";

/**
 * 트랜잭션 결과 문구(`owner`의 것만). Form 패널·Graph 편집기(P5-02)가 같은 문구·role을 쓴다:
 * 성공은 `status`, 실패는 `alert`.
 */
export const TransactionFeedbackNote = ({
  feedback,
  owner,
}: {
  feedback: Feedback;
  owner: string;
}) => {
  if (feedback.status === "idle" || feedback.owner !== owner) return null;
  if (feedback.status === "applied")
    return (
      <p className="strategy-form__feedback" role="status">
        {t("form.feedback.applied").replace("{label}", feedback.label)}
      </p>
    );
  const reason = tOptional(`form.feedback.${feedback.reason}`);
  return (
    <p
      className="strategy-form__feedback strategy-form__feedback--error"
      role="alert"
    >
      {(reason ?? t("form.feedback.failed")).replace("{label}", feedback.label)}
    </p>
  );
};
