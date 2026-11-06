import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { chartLinePreviewQuery, type ChartLinePreviewBody } from "../../../entities/chart";

/** 끄는 동안 마지막 움직임 뒤 이만큼 멈추면 묻는다(ms). 포인터 움직임 자체는 그리는 쪽이 rAF로 묶는다. */
export const PREVIEW_DELAY_MS = 40;

/**
 * 그리는 중·끄는 중 선의 미리보기. 초안이 바뀔 때마다 묻지 않고 잠깐 멈춘 초안만 `POST /lines/preview`로 보낸다.
 * 다음 응답이 올 때까지 앞 응답을 그대로 보여 선이 깜박이지 않는다. 초안이 없어지면 바로 비운다.
 */
export const useLinePreview = (draft: ChartLinePreviewBody | null) => {
  const key = draft === null ? null : JSON.stringify(draft);
  const [settled, setSettled] = useState<string | null>(key);
  useEffect(() => {
    if (key === null) return;
    const timer = window.setTimeout(() => setSettled(key), PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [key]);
  const active = key === null || settled === null ? null : (JSON.parse(settled) as ChartLinePreviewBody);
  const query = useQuery({ ...chartLinePreviewQuery(active), placeholderData: keepPreviousData });
  return {
    drawing: active === null ? null : (query.data ?? null),
    error: active === null ? null : query.error,
  };
};
