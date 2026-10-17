import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { LineChart } from "../../../entities/backtest";
import {
  experimentParameterMapQuery,
  experimentStatusLabel,
  experimentWalkForwardQuery,
  robustnessChecksQuery,
  retentionGapLabel,
  walkForwardGapLabel,
  type Experiment,
} from "../../../entities/experiment";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Link } from "../../../shared/lib/router";
import { Button, FailureNotice } from "../../../shared/ui";
import { RobustnessConfirm } from "./robustness-confirm";
import "./robustness-check.css";

/** 서버 값을 자릿수만 맞춰 보인다. 다시 계산하지 않는다. */
const fixed = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value.toFixed(4);

const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;

/**
 * 결과 검증 카드의 "표본 밖 성과"·"파라미터 고원" 항목(US-DM-11·US-CS-09).
 *
 * 이 실행에서 "튼튼한지 확인하기"로 만든 실험(가장 최근 것)을 읽어 채운다. 실험 설계·시도 수·창 선택·고원
 * 판정은 backend 가 소유하고 화면은 응답을 그대로 보인다. 단일 실행이 아니면(실험 trial·검증 실행) 버튼을
 * 두지 않는다 — 그 요청은 창 구간과 칸 값으로 좁혀져 있다.
 */
export const RobustnessCheck = ({
  runId,
  single,
}: {
  runId: string;
  single: boolean;
}) => {
  const checks = useQuery(robustnessChecksQuery(runId));
  const [confirming, setConfirming] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  // 창을 닫으면 포커스를 연 버튼으로 돌려준다. 시작했으면 버튼이 사라지므로 항목 제목으로 옮긴다.
  const closeConfirm = (started: boolean) => {
    setConfirming(false);
    (started ? heading.current : trigger.current)?.focus();
  };
  const latest = checks.data?.[0];
  const canStart =
    single &&
    checks.isSuccess &&
    // 취소한 실험도 남은 실행이 멈출 때(settled)까지는 서버가 새 확인을 받지 않는다.
    (latest === undefined ||
      (latest.record.cancelled_at != null && latest.settled));

  let body;
  if (checks.isPending)
    body = <p role="status">{t("backtest.robustness.loading")}</p>;
  else if (checks.isError)
    body = (
      <>
        <FailureNotice
          message={t("backtest.robustness.loadFailed")}
          reason={failureReason(checks.error)}
        />
        <Button
          size="small"
          tone="secondary"
          onClick={() => void checks.refetch()}
        >
          {t("backtest.robustness.reload")}
        </Button>
      </>
    );
  else if (latest === undefined || latest.record.cancelled_at != null)
    body = (
      <Pending
        text={
          !single
            ? t("backtest.robustness.notSingle")
            : latest === undefined
              ? t("backtest.robustness.needed")
              : t("backtest.robustness.cancelled")
        }
      />
    );
  else if (!latest.settled)
    body = (
      <Pending
        text={tFill("backtest.robustness.running", {
          status: experimentStatusLabel(latest.status),
        })}
        experimentId={latest.record.experiment_id}
      />
    );
  else body = <Settled experiment={latest} />;

  return (
    <div className="robustness-check">
      <h5 ref={heading} tabIndex={-1}>
        {t("backtest.robustness.title")}
      </h5>
      {body}
      {canStart ? (
        <Button ref={trigger} onClick={() => setConfirming(true)}>
          {t("backtest.robustness.start")}
        </Button>
      ) : null}
      {confirming ? (
        <RobustnessConfirm runId={runId} onClose={closeConfirm} />
      ) : null}
    </div>
  );
};

const ExperimentLink = ({ experimentId }: { experimentId: string }) => (
  <Link to="/research/experiments/$experimentId" params={{ experimentId }}>
    {t("backtest.robustness.open")}
  </Link>
);

/** 실험이 없거나 아직 끝나지 않았을 때 두 항목이 같은 사정을 말한다. */
const Pending = ({
  text,
  experimentId,
}: {
  text: string;
  experimentId?: string;
}) => (
  <div className="robustness-check__items">
    {(["backtest.robustness.oos", "backtest.robustness.plateau"] as const).map(
      (key) => (
        <section key={key} aria-label={t(key)}>
          <h6>{t(key)}</h6>
          <p>{text}</p>
        </section>
      ),
    )}
    {experimentId === undefined ? null : (
      <ExperimentLink experimentId={experimentId} />
    )}
  </div>
);

const Settled = ({ experiment }: { experiment: Experiment }) => {
  const experimentId = experiment.record.experiment_id;
  return (
    <div className="robustness-check__items">
      <OutOfSample experimentId={experimentId} />
      <Plateau experiment={experiment} />
      <ExperimentLink experimentId={experimentId} />
    </div>
  );
};

const OutOfSample = ({ experimentId }: { experimentId: string }) => {
  const report = useQuery(experimentWalkForwardQuery(experimentId));
  const title = t("backtest.robustness.oos");
  if (report.isPending)
    return (
      <section aria-label={title}>
        <h6>{title}</h6>
        <p role="status">{t("page.loading")}</p>
      </section>
    );
  if (report.isError)
    return (
      <section aria-label={title}>
        <h6>{title}</h6>
        <FailureNotice
          message={t("experiments.walkForward.loadFailed")}
          reason={failureReason(report.error)}
        />
      </section>
    );
  const { data } = report;
  return (
    <section aria-label={title}>
      <h6>{title}</h6>
      {data.gap === null || data.gap === undefined ? (
        <>
          <p className="robustness-check__value">
            {tFill("backtest.robustness.retention", {
              sharpe: fixed(data.out_of_sample_sharpe),
              retention: percent(data.retention),
            })}
          </p>
          <p>
            {retentionGapLabel(data) ??
              t("backtest.robustness.retentionMeaning")}
          </p>
          <LineChart
            title={t("backtest.robustness.curve")}
            series={[
              {
                label: t("backtest.robustness.curve.strategy"),
                color: "var(--chart-series-1)",
                values: data.curve.map((point) => point.equity),
              },
              ...(data.curve.some((point) => point.benchmark_equity != null)
                ? [
                    {
                      label: t("backtest.robustness.curve.benchmark"),
                      color: "var(--chart-series-2)",
                      values: data.curve.map(
                        (point) => point.benchmark_equity ?? null,
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </>
      ) : (
        <p>{walkForwardGapLabel(data.gap)}</p>
      )}
      <p className="robustness-check__note">
        {data.interpretation ??
          t("experiments.walkForward.interpretationUnavailable")}
      </p>
    </section>
  );
};

const Plateau = ({ experiment }: { experiment: Experiment }) => {
  const axes = experiment.record.design.search.axes;
  const map = useQuery({
    ...experimentParameterMapQuery(experiment.record.experiment_id),
    enabled: axes.length > 0,
  });
  const title = t("backtest.robustness.plateau");
  let content;
  if (axes.length === 0)
    content = <p>{t("backtest.robustness.noParameters")}</p>;
  else if (map.isPending) content = <p role="status">{t("page.loading")}</p>;
  else if (map.isError)
    content = (
      <FailureNotice
        message={t("backtest.robustness.loadFailed")}
        reason={failureReason(map.error)}
      />
    );
  else {
    const recommended = map.data.cells.find(
      (cell) => cell.verdict === "recommended",
    );
    content =
      recommended === undefined ? (
        <p>{t("backtest.robustness.noRecommendation")}</p>
      ) : (
        <>
          <p className="robustness-check__value">
            {tFill("backtest.robustness.recommended", {
              values: axes
                .map(
                  (axis, index) =>
                    `${axis.parameter_id} ${String(axis.values[recommended.grid_index[index] ?? 0])}`,
                )
                .join(" · "),
            })}
          </p>
          <p>
            {tFill("backtest.robustness.recommendedScore", {
              plateau: fixed(recommended.plateau_score),
              score: fixed(recommended.score),
              sensitivity: percent(recommended.sensitivity),
            })}
          </p>
        </>
      );
  }
  return (
    <section aria-label={title}>
      <h6>{title}</h6>
      {content}
    </section>
  );
};
