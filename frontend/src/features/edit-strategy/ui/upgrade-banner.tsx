import { t, tOptional } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import type {
  DocumentUpgrade,
  UpgradeStatus,
} from "../model/use-upgrade-document";
import "./upgrade-banner.css";

type UpgradeBannerProps = {
  upgrade: DocumentUpgrade;
  /** 저장된 1.0 revision 참조로 보낸 backtest가 422 `requires_upgrade`로 거부됐다. */
  backtestRejected?: boolean;
};

const failureText = (status: Extract<UpgradeStatus, { kind: "failed" }>) => {
  if (status.reason === "editor-unavailable") return t("upgrade.error.editor");
  if (status.reason === "composing") return t("upgrade.error.composing");
  const known =
    status.code === null
      ? undefined
      : tOptional(`upgrade.error.${status.code}`);
  return known ?? t("upgrade.error.request").replace("{detail}", status.detail);
};

/**
 * 편집기 위에 고정되는 schema 1.0 안내(WORKFLOW P2-02). 1.0 텍스트면 backend 변환을 한 번 호출해
 * 편집기에 넣고, legacy JSON 동결 row면 생성된 1.1 텍스트를 새 revision으로 저장하라고만 안내한다.
 * 실패 문구는 배너 안에 남고 텍스트는 바뀌지 않는다.
 */
export const UpgradeBanner = ({
  upgrade,
  backtestRejected = false,
}: UpgradeBannerProps) => {
  const { availability, status } = upgrade;
  // 방금 적용한 결과는 다음 편집 전까지 보인다(상태가 그 텍스트 버전에 묶여 있다).
  const applied = status.kind === "applied";
  if (availability.kind === "none" && !backtestRejected && !applied)
    return null;
  const summary =
    availability.kind === "frozen-generated"
      ? t("upgrade.frozenGenerated")
      : availability.kind === "upgradeable"
        ? t("upgrade.body")
        : applied
          ? t("upgrade.applied")
          : t("upgrade.backtestBlocked");
  return (
    <section className="upgrade" aria-label={t("upgrade.title")}>
      <div className="upgrade__summary" role="status">
        <Badge tone="warn">{t("upgrade.title")}</Badge>
        <span>{summary}</span>
        {backtestRejected && availability.kind !== "none" ? (
          <span className="upgrade__note">{t("upgrade.backtestBlocked")}</span>
        ) : null}
      </div>
      {availability.kind === "upgradeable" ? (
        <div className="upgrade__actions">
          <Button
            size="small"
            tone="primary"
            onClick={upgrade.upgrade}
            disabled={!upgrade.canUpgrade}
          >
            {status.kind === "pending"
              ? t("upgrade.pending")
              : t("upgrade.action")}
          </Button>
          {status.kind === "failed" ? (
            <span className="upgrade__error" role="alert">
              {failureText(status)}
            </span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};
