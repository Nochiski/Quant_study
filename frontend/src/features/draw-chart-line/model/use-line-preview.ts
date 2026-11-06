import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { chartLinePreviewQuery, type ChartLinePreviewBody } from "../../../entities/chart";

/** 끄는 동안 마지막 움직임 뒤 이만큼 멈추면 묻는다(ms). 포인터 움직임 자체는 그리는 쪽이 rAF로 묶는다. */
export const PREVIEW_DELAY_MS = 40;

/**
 * 그리는 중·끄는 중 선의 미리보기. 초안이 바뀔 때마다 묻지 않고 잠깐 멈춘 초안만 `POST /lines/preview`로 보낸다.
 * 다음 응답이 올 때까지 앞 응답을 보이되 같은 동작(`gesture`) 안에서만 그런다. 초안이 없어지면 멈춘 초안도
 * 바로 비운다 — 다음 동작이 앞 동작의 잔상으로 시작하지 않는다.
 */
export const useLinePreview = (draft: ChartLinePreviewBody | null, gesture: string) => {
  const key = draft === null ? null : JSON.stringify(draft);
  const [settled, setSettled] = useState<string | null>(key);
  if (key === null && settled !== null) {
    // 렌더 중 상태 맞추기: 동작이 끝나면 멈춘 초안을 버린다
    setSettled(null);
  }
  useEffect(() => {
    if (key === null) return;
    const timer = window.setTimeout(() => setSettled(key), PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [key]);
  const active = key === null || settled === null ? null : (JSON.parse(settled) as ChartLinePreviewBody);
  const query = useQuery(chartLinePreviewQuery(active, gesture));
  return {
    drawing: active === null ? null : (query.data ?? null),
    error: active === null ? null : query.error,
  };
};
