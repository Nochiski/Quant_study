import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import {
  BrokerConnectionRequestError,
  brokerConnectionApi,
  refreshKiwoomMockConnection,
  type ConnectionOutcome,
  type SaveKiwoomMockConnectionBody,
} from "../../../entities/broker";
import { refreshCredentialsFile } from "../../../entities/credentials-file";
import { t } from "../../../shared/config";

export type KiwoomField = "app_key" | "secret_key" | "account_number" | "form";

export type KiwoomRejection = { field: KiwoomField; message: string };

const FIELDS: ReadonlySet<string> = new Set([
  "app_key",
  "secret_key",
  "account_number",
]);

const rejection = (error: unknown): KiwoomRejection => {
  if (
    error instanceof BrokerConnectionRequestError &&
    error.serverMessage !== null
  ) {
    const field =
      error.field !== null && FIELDS.has(error.field) ? error.field : "form";
    return { field: field as KiwoomField, message: error.serverMessage };
  }
  return { field: "form", message: t("settings.kiwoom.form.error.failed") };
};

/**
 * 저장 명령. 앱 키·시크릿·계좌번호를 싣으므로 react-query mutation을 쓰지 않는다 — mutation 인자는
 * `MutationCache`에 남는다. 값은 폼의 비제어 입력에서 읽혀 이 호출 인자로만 흐른다(AI 키 설정과 같은 규칙).
 */
export const useSaveKiwoomMockConnection = () => {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  const submit = async (
    input: SaveKiwoomMockConnectionBody,
  ): Promise<KiwoomRejection | null> => {
    setPending(true);
    try {
      await brokerConnectionApi.save(input);
      await Promise.all([
        refreshKiwoomMockConnection(queryClient),
        refreshCredentialsFile(queryClient),
      ]);
      return null;
    } catch (error) {
      return rejection(error);
    } finally {
      setPending(false);
    }
  };

  return { pending, submit };
};

/** 화면에 남기는 연결 확인 결과. 서버가 완성한 문장과 결과 코드만 담는다. */
export type VerifyOutcome = {
  outcome: ConnectionOutcome | null;
  message: string;
};

/**
 * 연결 확인. 키를 싣지 않지만 결과를 카드에 남기려고 저장과 같은 모양으로 둔다.
 *
 * `reset`(새 키 저장·삭제) 뒤에 도착한 옛 확인 응답은 버린다 — 확인하지 않은 새 키에 "연결됐습니다"가
 * 남지 않게 한다(2026-10-22 리뷰 #595). 세대 번호는 콜백에서만 읽고 쓴다.
 */
export const useVerifyKiwoomMockConnection = () => {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<VerifyOutcome | null>(null);
  const generation = useRef(0);

  const verify = async (): Promise<void> => {
    const started = generation.current;
    setPending(true);
    try {
      const check = await brokerConnectionApi.verify();
      if (generation.current === started)
        setResult({ outcome: check.outcome, message: check.message });
    } catch {
      if (generation.current === started)
        setResult({
          outcome: null,
          message: t("settings.kiwoom.verify.failed"),
        });
    } finally {
      setPending(false);
    }
  };

  const reset = () => {
    generation.current += 1;
    setResult(null);
  };

  return { pending, result, verify, reset };
};
