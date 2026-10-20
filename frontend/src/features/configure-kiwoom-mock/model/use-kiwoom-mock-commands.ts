import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  BrokerConnectionRequestError,
  brokerConnectionApi,
  refreshKiwoomMockConnection,
  type ConnectionOutcome,
  type SaveKiwoomMockConnectionBody,
} from "../../../entities/broker";
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
      await refreshKiwoomMockConnection(queryClient);
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

/** 연결 확인. 키를 싣지 않지만 결과를 카드에 남기려고 저장과 같은 모양으로 둔다. */
export const useVerifyKiwoomMockConnection = () => {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<VerifyOutcome | null>(null);

  const verify = async (): Promise<void> => {
    setPending(true);
    try {
      const check = await brokerConnectionApi.verify();
      setResult({ outcome: check.outcome, message: check.message });
    } catch {
      setResult({ outcome: null, message: t("settings.kiwoom.verify.failed") });
    } finally {
      setPending(false);
    }
  };

  return { pending, result, verify, reset: () => setResult(null) };
};
