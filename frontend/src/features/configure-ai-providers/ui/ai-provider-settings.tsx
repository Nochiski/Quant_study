import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  assistantProvidersQuery,
  useActivateAssistantProvider,
  useDeleteAssistantProvider,
  useTestAssistantProvider,
  type ProviderKindView,
  type ProviderProfileView,
} from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { Badge, Button, EmptyState, Tooltip } from "../../../shared/ui";
import {
  probeFailureMessage,
  providerKindLabel,
  providerRejection,
} from "../model/provider-copy";
import { AiProviderForm } from "./ai-provider-form";
import "./ai-provider-settings.css";

/** 카드에 인라인으로 남기는 연결 테스트 결과. 키와 무관한 값만 담는다. */
type ProbeOutcome = { ok: boolean; message: string; latencyMs: number | null };

const KindStatus = ({ kind }: { kind: ProviderKindView }) => (
  <li className="ai-providers__kind" data-testid={`provider-kind-${kind.kind}`}>
    <span className="ai-providers__kind-name">
      {providerKindLabel(kind.kind)}
    </span>
    {kind.installed ? (
      <Badge tone="ok">{t("assistant.provider.installed")}</Badge>
    ) : (
      <Tooltip content={t("assistant.provider.notInstalled.reason")}>
        <Badge tone="warn" tabIndex={0}>
          {t("assistant.provider.notInstalled")}
        </Badge>
      </Tooltip>
    )}
  </li>
);

const ProviderCard = ({
  profile,
  probe,
  busy,
  confirming,
  onTest,
  onActivate,
  onConfirmDelete,
  onDelete,
}: {
  profile: ProviderProfileView;
  probe: ProbeOutcome | undefined;
  busy: boolean;
  confirming: boolean;
  onTest: () => void;
  onActivate: () => void;
  onConfirmDelete: (confirming: boolean) => void;
  onDelete: () => void;
}) => (
  <li className="ai-provider-card">
    <div className="ai-provider-card__head">
      <h3 className="ai-provider-card__title">{profile.label}</h3>
      {profile.active ? (
        <Badge tone="ok" data-testid="provider-active">
          {t("assistant.provider.active")}
        </Badge>
      ) : null}
    </div>
    <dl className="ai-provider-card__facts">
      <div>
        <dt>{t("assistant.provider.form.kind")}</dt>
        <dd>{providerKindLabel(profile.kind)}</dd>
      </div>
      <div>
        <dt>{t("assistant.provider.model")}</dt>
        <dd>{profile.model}</dd>
      </div>
      <div>
        <dt>{t("assistant.provider.secretTail")}</dt>
        <dd>
          {profile.secret_tail === null
            ? t("assistant.provider.secretTail.unknown")
            : `••••${profile.secret_tail}`}
        </dd>
      </div>
      {profile.base_url === null ? null : (
        <div>
          <dt>{t("assistant.provider.baseUrl")}</dt>
          <dd>{profile.base_url}</dd>
        </div>
      )}
    </dl>
    {probe === undefined ? null : (
      <p
        className={`ai-provider-card__probe ai-provider-card__probe--${probe.ok ? "ok" : "failed"}`}
        role="status"
      >
        {probe.ok && probe.latencyMs !== null
          ? `${probe.message} · ${probe.latencyMs}ms`
          : probe.message}
      </p>
    )}
    <div className="ai-provider-card__actions">
      {profile.active ? null : (
        <Button size="small" onClick={onActivate} disabled={busy}>
          {t("assistant.provider.activate")}
        </Button>
      )}
      <Button size="small" onClick={onTest} disabled={busy}>
        {t("assistant.provider.test")}
      </Button>
      {confirming ? (
        <>
          <p className="ai-provider-card__confirm">
            {t("assistant.provider.delete.confirm")}
          </p>
          <Button size="small" tone="danger" onClick={onDelete} disabled={busy}>
            {t("assistant.provider.delete.submit")}
          </Button>
          <Button
            size="small"
            tone="ghost"
            onClick={() => onConfirmDelete(false)}
          >
            {t("assistant.provider.delete.cancel")}
          </Button>
        </>
      ) : (
        <Button
          size="small"
          tone="danger"
          onClick={() => onConfirmDelete(true)}
        >
          {t("assistant.provider.delete")}
        </Button>
      )}
    </div>
  </li>
);

/**
 * 설정 화면의 "AI 어시스턴트 공급자" 섹션.
 *
 * 프로파일·활성 여부의 정본은 서버다 — 목록 query가 유일한 사본이고 변이 뒤에는 다시 읽는다.
 * 키는 추가 폼을 지나갈 뿐 이 컴포넌트가 보는 값에는 꼬리 4자리밖에 없다.
 */
export const AiProviderSettings = () => {
  const titleId = useId();
  const providers = useQuery(assistantProvidersQuery());
  const activate = useActivateAssistantProvider();
  const remove = useDeleteAssistantProvider();
  const test = useTestAssistantProvider();
  const [probes, setProbes] = useState<Record<string, ProbeOutcome>>({});
  const [confirming, setConfirming] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // 변이가 끝나도 `variables`는 남으므로 진행 중 여부와 함께 봐야 그 카드만 잠긴다.
  const busyProfile = (profileId: string): boolean =>
    (activate.isPending && activate.variables === profileId) ||
    (remove.isPending && remove.variables === profileId) ||
    (test.isPending && test.variables === profileId);

  const recordProbe = (profileId: string, outcome: ProbeOutcome) =>
    setProbes((previous) => ({ ...previous, [profileId]: outcome }));

  const runTest = (profileId: string) => {
    setActionError(null);
    test.mutate(profileId, {
      onSuccess: (result) =>
        recordProbe(profileId, {
          ok: result.ok,
          message: result.ok
            ? t("assistant.provider.test.ok")
            : probeFailureMessage(result.failure),
          latencyMs: result.latency_ms,
        }),
      onError: (error) =>
        recordProbe(profileId, {
          ok: false,
          message: providerRejection(error).message,
          latencyMs: null,
        }),
    });
  };

  const runActivate = (profileId: string) => {
    setActionError(null);
    activate.mutate(profileId, {
      onError: (error) => setActionError(providerRejection(error).message),
    });
  };

  const runDelete = (profileId: string) => {
    setActionError(null);
    remove.mutate(profileId, {
      onSuccess: () => setConfirming(null),
      onError: (error) => setActionError(providerRejection(error).message),
    });
  };

  return (
    <section className="ai-providers" aria-labelledby={titleId}>
      <h2 className="ai-providers__title" id={titleId}>
        {t("settings.assistant.title")}
      </h2>
      <p className="ai-providers__description">
        {t("settings.assistant.description")}
      </p>
      {providers.isPending ? <p role="status">{t("page.loading")}</p> : null}
      {providers.isError ? (
        <p className="ai-providers__error" role="alert">
          {t("assistant.provider.loadError")}
        </p>
      ) : null}
      {providers.data === undefined ? null : (
        <>
          <ul className="ai-providers__kinds">
            {providers.data.kinds.map((kind) => (
              <KindStatus key={kind.kind} kind={kind} />
            ))}
          </ul>
          {actionError === null ? null : (
            <p className="ai-providers__error" role="alert">
              {actionError}
            </p>
          )}
          {providers.data.profiles.length === 0 ? (
            <EmptyState
              title={t("assistant.provider.empty")}
              description={t("assistant.provider.empty.description")}
            />
          ) : (
            <ul className="ai-providers__list">
              {providers.data.profiles.map((profile) => (
                <ProviderCard
                  key={profile.profile_id}
                  profile={profile}
                  probe={probes[profile.profile_id]}
                  busy={busyProfile(profile.profile_id)}
                  confirming={confirming === profile.profile_id}
                  onTest={() => runTest(profile.profile_id)}
                  onActivate={() => runActivate(profile.profile_id)}
                  onConfirmDelete={(next) =>
                    setConfirming(next ? profile.profile_id : null)
                  }
                  onDelete={() => runDelete(profile.profile_id)}
                />
              ))}
            </ul>
          )}
          <AiProviderForm kinds={providers.data.kinds} />
        </>
      )}
    </section>
  );
};
