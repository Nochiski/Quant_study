import { useState } from "react";

import type { UpgradedDocument } from "../../../shared/api";
import { t, tOptional } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import { upgradeWarningTitle } from "../model/document-upgrade";
import type {
  DocumentUpgrade,
  UpgradeStatus,
} from "../model/use-upgrade-document";
import "./upgrade-banner.css";

type UpgradeEnvironment = NonNullable<UpgradedDocument["environment"]>;

type UpgradeBannerProps = {
  upgrade: DocumentUpgrade;
  /** 저장된 은퇴 버전 revision 참조로 보낸 backtest가 422 `requires_upgrade`로 거부됐다. */
  backtestRejected?: boolean;
  /**
   * 업그레이드 응답의 실행 설정을 실행 설정 패널에 넣는다(P3-02). 사용자가 배너 버튼을 누를 때만 부른다 —
   * 옛 문서의 기간·유니버스가 지금 고른 값을 말없이 덮지 않게 한다. 넘기지 않으면 버튼이 없다.
   */
  onApplyEnvironment?: (environment: UpgradeEnvironment) => void;
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

/** 옛 문서 실행 설정의 한 줄 요약. 무엇을 채울지 누르기 전에 보이려는 것이라 기간·유니버스만 적는다. */
const environmentSummary = (environment: UpgradeEnvironment): string =>
  `${environment.start} → ${environment.end} · ${environment.universe_id}`;

const AppliedUpgrade = ({
  status,
  onApplyEnvironment,
}: {
  status: Extract<UpgradeStatus, { kind: "applied" }>;
  onApplyEnvironment?: (environment: UpgradeEnvironment) => void;
}) => {
  // 적용 결과 하나마다 한 번 채운다: 같은 응답 객체를 채웠는지 기억한다(다음 업그레이드는 새 객체다).
  const [filled, setFilled] = useState<UpgradeEnvironment | null>(null);
  const { environment, warnings } = status;
  return (
    <>
      {warnings.length > 0 ? (
        <div className="upgrade__warnings">
          <strong>{t("upgrade.warnings")}</strong>
          <ul>
            {warnings.map((warning) => (
              <li key={`${warning.code}:${warning.message}`}>
                <b>{upgradeWarningTitle(warning.code)}</b>
                <span>{warning.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {onApplyEnvironment === undefined ? null : environment === null ? (
        <p className="upgrade__note">{t("upgrade.environment.unavailable")}</p>
      ) : filled === environment ? (
        <p className="upgrade__note" role="status">
          {t("upgrade.environment.applied")}
        </p>
      ) : (
        <div className="upgrade__actions">
          <span>
            {t("upgrade.environment.found").replace(
              "{summary}",
              environmentSummary(environment),
            )}
          </span>
          <Button
            size="small"
            tone="primary"
            onClick={() => {
              onApplyEnvironment(environment);
              setFilled(environment);
            }}
          >
            {t("upgrade.environment.apply")}
          </Button>
        </div>
      )}
    </>
  );
};

/**
 * 편집기 위에 고정되는 은퇴 버전 안내(WORKFLOW P2-02·P3-02). 은퇴 버전 텍스트면 backend 변환을 한 번
 * 호출해 편집기에 넣고, legacy JSON 동결 row면 생성된 현재 버전 텍스트를 새 revision으로 저장하라고만
 * 안내한다. 문구는 버전 중립이다 — 어떤 버전이 은퇴했는지는 backend 가 판정한다(SoT authoring schema
 * 버전 행). 적용한 뒤에는 응답의 warning 과 옛 문서의 실행 설정을 보이고, 실행 설정은 누를 때만 채운다.
 */
export const UpgradeBanner = ({
  upgrade,
  backtestRejected = false,
  onApplyEnvironment,
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
      {status.kind === "applied" ? (
        <AppliedUpgrade
          status={status}
          onApplyEnvironment={onApplyEnvironment}
        />
      ) : null}
    </section>
  );
};
