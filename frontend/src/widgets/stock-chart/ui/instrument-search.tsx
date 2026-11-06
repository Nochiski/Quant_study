import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";

import {
  chartErrorSentence,
  chartInstrumentSearchQuery,
  type ChartInstrumentBody,
} from "../../../entities/chart";
import { failureReason } from "../../../shared/api";
import { t } from "../../../shared/config";
import { FailureNotice } from "../../../shared/ui";

/** 입력이 멈춘 뒤 이만큼 지나면 묻는다(ms). */
const SEARCH_DELAY_MS = 200;

/**
 * 종목·지수 검색. 검색 칸 아래에 결과 버튼 목록이 열린다 — 버튼은 native 요소라 Tab·Enter가 그대로 되고, 검색
 * 칸에서 ↓로 첫 결과에, 결과 사이는 ↑/↓로 옮긴다. Esc는 목록을 닫고 검색 칸으로 돌아간다. 순위는 서버가 정한다.
 */
export const InstrumentSearch = ({ onPick }: { onPick: (instrument: ChartInstrumentBody) => void }) => {
  const id = useId();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);
  const search = useQuery(chartInstrumentSearchQuery(query));
  const results = search.data?.instruments ?? [];
  const showing = open && query.trim() !== "";

  const buttons = () => Array.from(list.current?.querySelectorAll("button") ?? []);
  const move = (from: number, step: number) => {
    const all = buttons();
    if (all.length === 0) return;
    if (from + step < 0) input.current?.focus();
    else all[Math.min(from + step, all.length - 1)]?.focus();
  };
  const close = () => {
    setOpen(false);
    input.current?.focus();
  };
  const pick = (instrument: ChartInstrumentBody) => {
    onPick(instrument);
    setText("");
    setQuery("");
    setOpen(false);
  };
  const onInputKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      move(-1, 1);
    } else if (event.key === "Escape") setOpen(false);
    else if (event.key === "Enter" && results.length > 0) {
      event.preventDefault();
      pick(results[0]);
    }
  };
  const onResultKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(index, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };
  const onBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  };

  return (
    <div className="instrument-search" onBlur={onBlur}>
      <label htmlFor={`${id}-input`}>{t("chart.search.label")}</label>
      <input
        ref={input}
        id={`${id}-input`}
        className="ui-input instrument-search__input"
        type="search"
        autoComplete="off"
        placeholder={t("chart.search.placeholder")}
        aria-describedby={`${id}-hint`}
        aria-expanded={showing}
        aria-controls={`${id}-results`}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onInputKey}
      />
      <span id={`${id}-hint`} className="sr-only">
        {t("chart.search.hint")}
      </span>
      {showing ? (
        <div className="instrument-search__popup" id={`${id}-results`}>
          {search.isError ? (
            <FailureNotice
              title={t("chart.error.search")}
              message={chartErrorSentence(search.error)}
              reason={failureReason(search.error)}
            />
          ) : search.isPending || query !== text ? (
            <p className="instrument-search__state" role="status">
              {t("chart.search.loading")}
            </p>
          ) : results.length === 0 ? (
            <p className="instrument-search__state" role="status">
              {t("chart.search.none")}
            </p>
          ) : (
            <ul ref={list} className="instrument-search__list" aria-label={t("chart.search.results")}>
              {results.map((instrument, index) => (
                <li key={`${instrument.kind}:${instrument.code}`}>
                  <button
                    type="button"
                    className="instrument-search__result"
                    onClick={() => pick(instrument)}
                    onKeyDown={(event) => onResultKey(event, index)}
                  >
                    <span className="instrument-search__name">{instrument.name}</span>
                    <span className="instrument-search__meta">
                      {instrument.kind === "index" ? (instrument.group ?? instrument.code) : instrument.code}
                      {" · "}
                      {t(`chart.search.kind.${instrument.kind}`)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
};
