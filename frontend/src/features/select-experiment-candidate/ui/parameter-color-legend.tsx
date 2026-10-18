import { t } from "../../../shared/config";
import { mapHeatBounds, type mapHeatScale } from "../model/parameter-map-view";

const label = (value: number) =>
  new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(
    value,
  );

/** 전체 실험 범위의 요약만 반올림하고 펼친 구간은 색 칠하기와 같은 경계 원수치로 읽는다. */
export const ParameterColorLegend = ({
  scale,
}: {
  scale: ReturnType<typeof mapHeatScale>;
}) => {
  if (scale === null) return <p>{t("experiments.map.noColor")}</p>;
  const uniform = scale.min === scale.max;
  const bounds = mapHeatBounds(scale);
  return (
    <details className="ui-disclosure candidate-map__color-legend">
      <summary>
        {t("experiments.map.colorLegend")} ·{" "}
        {uniform
          ? label(scale.min)
          : `${label(scale.min)} ~ ${label(scale.max)}`}
      </summary>
      <p>{t("experiments.map.relativeColor")}</p>
      {uniform ? (
        <p data-heat="4">
          {t("experiments.map.sameColor")}:{" "}
          <span title={String(scale.min)}>{label(scale.min)}</span>
        </p>
      ) : (
        <>
          <p>{t("experiments.map.colorBounds")}</p>
          <dl>
            {Array.from({ length: 7 }, (_, index) => {
              const start = bounds[index];
              const end = bounds[index + 1];
              return (
                <div key={index} data-heat={index + 1}>
                  <dt>{index + 1}</dt>
                  <dd>
                    {String(start)} ≤ {t("experiments.map.colorValue")}{" "}
                    {index === 6 ? "≤" : "<"} {String(end)}
                  </dd>
                </div>
              );
            })}
          </dl>
        </>
      )}
    </details>
  );
};
