import { useId, useState } from "react";

import {
  chartErrorSentence,
  type ChartRegimeBody,
  type DateRange,
} from "../../../entities/chart";
import { failureReason } from "../../../shared/api";
import { t, tFill } from "../../../shared/config";
import { Button, FailureNotice } from "../../../shared/ui";
import type { useAddCandidates } from "../model/use-add-candidates";

type WindowFieldsProps = {
  /** 지금 구간(응답 구간 또는 고친 값). 응답 전이면 null. */
  window: DateRange | null;
  onChange: (window: DateRange) => void;
  /** 채널의 자동 제안 칩(판정 응답 `suggestions`). */
  suggestions?: ChartRegimeBody["suggestions"];
  note: string;
};

/**
 * 판정·분석 구간: 시작·끝 날짜(그 날짜가 든 봉으로 서버가 옮긴다)와 자동 제안 칩. 칩은 응답 구간과 같으면 눌린
 * 상태다. 날짜를 비우거나 시작이 끝보다 늦으면 보내지 않는다(서버도 `chart.window.reversed`로 거절한다).
 */
export const WindowFields = ({
  window,
  onChange,
  suggestions,
  note,
}: WindowFieldsProps) => {
  const id = useId();
  const set = (edge: keyof DateRange, value: string) => {
    if (window === null || value === "") return;
    const next = { ...window, [edge]: value };
    if (next.start <= next.end) onChange(next);
  };
  return (
    <section className="finder-section" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>{t("chart.finder.window.title")}</h3>
      <div className="finder-fields__dates">
        <div className="finder-field">
          <label htmlFor={`${id}-start`}>
            {t("chart.finder.window.start")}
          </label>
          <input
            id={`${id}-start`}
            className="ui-input"
            type="date"
            value={window?.start ?? ""}
            onChange={(event) => set("start", event.target.value)}
          />
        </div>
        <div className="finder-field">
          <label htmlFor={`${id}-end`}>{t("chart.finder.window.end")}</label>
          <input
            id={`${id}-end`}
            className="ui-input"
            type="date"
            value={window?.end ?? ""}
            onChange={(event) => set("end", event.target.value)}
          />
        </div>
      </div>
      {suggestions === undefined || suggestions.length === 0 ? null : (
        <div
          className="finder-chips"
          role="group"
          aria-label={t("chart.finder.window.presets")}
        >
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.bars}
              type="button"
              className="finder-chip"
              aria-pressed={
                window?.start === suggestion.start &&
                window.end === suggestion.end
              }
              onClick={() =>
                onChange({ start: suggestion.start, end: suggestion.end })
              }
            >
              {tFill("chart.finder.window.preset", { bars: suggestion.bars })}
              {suggestion.default
                ? ` · ${t("chart.finder.window.default")}`
                : ""}
            </button>
          ))}
        </div>
      )}
      <p className="finder-note">{note}</p>
    </section>
  );
};

type ParamFieldProps = {
  label: string;
  /** 사용자가 바꾼 값(null = 서버 기본). */
  value: number | null;
  /** 지금 서버가 쓴 값(응답 `record.params`) — 빈 칸의 자리표시로 보인다. */
  serverValue: number | null;
  step: number;
  onCommit: (value: number | null) => void;
};

/**
 * 분석 파라미터 숫자 칸. 칸을 비우면 서버 기본값이다. 놓거나(blur) Enter를 누를 때 보낸다 — 범위 검사는 서버 domain이
 * 하고(`chart.params.invalid`) 화면은 다시 검사하지 않는다.
 */
export const ParamField = ({
  label,
  value,
  serverValue,
  step,
  onCommit,
}: ParamFieldProps) => {
  const id = useId();
  const [text, setText] = useState<string | null>(null);
  const shown = text ?? (value === null ? "" : String(value));
  const commit = () => {
    if (text === null) return;
    setText(null);
    const trimmed = text.trim();
    const parsed = trimmed === "" ? null : Number(trimmed);
    if (parsed !== null && !Number.isFinite(parsed)) return;
    if (parsed !== value) onCommit(parsed);
  };
  return (
    <div className="finder-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="ui-input"
        type="number"
        step={step}
        value={shown}
        placeholder={serverValue === null ? "" : String(serverValue)}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
        }}
      />
    </div>
  );
};

/** 분석 조회 실패 한 줄(코드 문구 + 접힌 서버 사유). */
export const FinderFailure = ({ error }: { error: unknown }) => (
  <FailureNotice
    title={t("chart.finder.error")}
    message={chartErrorSentence(error)}
    reason={failureReason(error)}
  />
);

type CreateFooterProps = {
  addLabel: string;
  /** 지금 결과로 추가할 수 있는가(결과가 지금 입력의 것이고 고른 것이 있다). */
  canAdd: boolean;
  onAdd: () => void;
  onClose: () => void;
  adding: Pick<ReturnType<typeof useAddCandidates>, "lastOutcome" | "error">;
};

/**
 * 생성 패널 3종의 아래 줄: 일부만 저장된 묶음 알림, 저장 실패, 추가·취소 단추. 저장 중에도 단추를 끄지 않는다 —
 * 꺼진 단추는 초점을 잃어 패널의 Esc가 닿지 않는다(겹친 누르기는 `useAddCandidates`가 막는다).
 */
export const CreateFooter = ({ addLabel, canAdd, onAdd, onClose, adding }: CreateFooterProps) => (
  <>
    {adding.lastOutcome !== null && adding.lastOutcome.failed > 0 ? (
      <p className="finder-warning" role="status">
        {tFill("chart.finder.partial", {
          failed: adding.lastOutcome.failed,
          total: adding.lastOutcome.failed + adding.lastOutcome.saved,
        })}
      </p>
    ) : null}
    {adding.error ? <FinderFailure error={adding.error} /> : null}
    <Button tone="primary" disabled={!canAdd} onClick={onAdd}>
      {addLabel}
    </Button>
    <Button onClick={onClose}>{t("chart.finder.cancel")}</Button>
  </>
);

/**
 * 앞 결과를 보이며 새 조건으로 다시 찾는 동안의 한 줄(생성 패널 3종 공통). 결과 절은 `finderStale`로 옅게 그린다 —
 * 지금 보이는 판정·표가 새 조건의 것이 아니라는 표시다.
 */
export const RefreshingNote = ({ refreshing }: { refreshing: boolean }) =>
  refreshing ? (
    <p className="finder-note" role="status">
      {t("chart.finder.refreshing")}
    </p>
  ) : null;
