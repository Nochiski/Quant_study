import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";

import {
  kiwoomMockConnectionQuery,
  useDeleteKiwoomMockConnection,
} from "../../../entities/broker";
import { t, tFill } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import {
  useSaveKiwoomMockConnection,
  useVerifyKiwoomMockConnection,
  type KiwoomField,
  type KiwoomRejection,
} from "../model/use-kiwoom-mock-commands";
import "./kiwoom-mock-settings.css";

/**
 * `/settings`의 키움 모의투자 연결 섹션(S06 ②-3, US-SM-18).
 *
 * 앱 키·시크릿·계좌번호는 비제어 입력이다 — 저장할 때 한 번 읽어 요청에 싣고 바로 입력칸을 비운다.
 * 화면에는 저장 여부와 계좌번호 끝 4자리만 보인다. "연결 확인"은 누를 때만 키움에 묻고(토큰 발급과
 * 계좌 대조), 결과 문장은 서버가 완성한 것을 그대로 보인다. 이 화면은 주문하지 않는다.
 */
export const KiwoomMockSettings = () => {
  const connection = useQuery(kiwoomMockConnectionQuery());
  const save = useSaveKiwoomMockConnection();
  const check = useVerifyKiwoomMockConnection();
  const remove = useDeleteKiwoomMockConnection();
  const [rejection, setRejection] = useState<KiwoomRejection | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [removeFailed, setRemoveFailed] = useState(false);
  const appKeyRef = useRef<HTMLInputElement>(null);
  const secretKeyRef = useRef<HTMLInputElement>(null);
  const accountRef = useRef<HTMLInputElement>(null);
  const ids = {
    title: useId(),
    appKey: useId(),
    secretKey: useId(),
    account: useId(),
    secretHint: useId(),
    accountHint: useId(),
    error: useId(),
  };

  const saved = connection.data?.saved === true;

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const appKey = appKeyRef.current?.value ?? "";
    const secretKey = secretKeyRef.current?.value ?? "";
    const account = accountRef.current?.value ?? "";
    if (!appKey.trim() || !secretKey.trim() || !account.trim()) {
      setRejection({
        field: "form",
        message: t("settings.kiwoom.form.error.required"),
      });
      return;
    }
    setRejection(null);
    const rejected = await save.submit({
      app_key: appKey,
      secret_key: secretKey,
      account_number: account,
    });
    if (rejected === null) {
      for (const ref of [appKeyRef, secretKeyRef, accountRef]) {
        if (ref.current !== null) ref.current.value = "";
      }
      check.reset();
      return;
    }
    setRejection(rejected);
    const target = {
      app_key: appKeyRef,
      secret_key: secretKeyRef,
      account_number: accountRef,
      form: null,
    }[rejected.field];
    target?.current?.focus();
  };

  const invalid = (field: KiwoomField) =>
    rejection?.field === field ? true : undefined;
  const describedBy = (field: KiwoomField, hint?: string) =>
    [hint, rejection?.field === field ? ids.error : undefined]
      .filter(Boolean)
      .join(" ") || undefined;
  const fieldError = (field: KiwoomField) =>
    rejection?.field === field ? (
      <p className="kiwoom-settings__error" id={ids.error} role="alert">
        {rejection.message}
      </p>
    ) : null;

  const runDelete = () => {
    setRemoveFailed(false);
    remove.mutate(undefined, {
      onSuccess: () => {
        setConfirming(false);
        check.reset();
      },
      onError: () => setRemoveFailed(true),
    });
  };

  return (
    <section className="kiwoom-settings" aria-labelledby={ids.title}>
      <h2 className="kiwoom-settings__title" id={ids.title}>
        {t("settings.kiwoom.title")}
      </h2>
      <p className="kiwoom-settings__description">
        {t("settings.kiwoom.description")}
      </p>
      <p className="kiwoom-settings__description">
        {t("settings.kiwoom.guide")}
      </p>
      {connection.isPending ? <p role="status">{t("page.loading")}</p> : null}
      {connection.isError ? (
        <p className="kiwoom-settings__error" role="alert">
          {t("settings.kiwoom.loadError")}
        </p>
      ) : null}
      {connection.data === undefined ? null : (
        <div
          className="kiwoom-settings__status"
          data-testid="kiwoom-connection-status"
        >
          <Badge tone={saved ? "ok" : "neutral"}>
            {saved && connection.data.account_tail !== null
              ? tFill("settings.kiwoom.status.saved", {
                  tail: connection.data.account_tail,
                })
              : t("settings.kiwoom.status.empty")}
          </Badge>
          <div className="kiwoom-settings__actions">
            <Button
              onClick={() => void check.verify()}
              disabled={!saved || check.pending}
            >
              {check.pending
                ? t("settings.kiwoom.verifying")
                : t("settings.kiwoom.verify")}
            </Button>
            {saved && !confirming ? (
              <Button tone="danger" onClick={() => setConfirming(true)}>
                {t("settings.kiwoom.delete")}
              </Button>
            ) : null}
          </div>
        </div>
      )}
      {confirming ? (
        <div className="kiwoom-settings__confirm" role="group">
          <p>{t("settings.kiwoom.delete.confirm")}</p>
          <Button tone="danger" onClick={runDelete} disabled={remove.isPending}>
            {t("settings.kiwoom.delete.submit")}
          </Button>
          <Button onClick={() => setConfirming(false)}>
            {t("settings.kiwoom.delete.cancel")}
          </Button>
        </div>
      ) : null}
      {removeFailed ? (
        <p className="kiwoom-settings__error" role="alert">
          {t("settings.kiwoom.delete.failed")}
        </p>
      ) : null}
      {check.result === null ? null : (
        <p
          className={`kiwoom-settings__result kiwoom-settings__result--${
            check.result.outcome === "connected" ? "ok" : "failed"
          }`}
          data-outcome={check.result.outcome ?? "request_failed"}
          role="status"
        >
          {check.result.message}
        </p>
      )}
      <form
        className="kiwoom-settings__form"
        onSubmit={(event) => void onSubmit(event)}
        noValidate
      >
        <h3 className="kiwoom-settings__form-title">
          {t("settings.kiwoom.form.title")}
        </h3>
        <div className="kiwoom-settings__row">
          <label htmlFor={ids.appKey}>{t("settings.kiwoom.form.appKey")}</label>
          <input
            id={ids.appKey}
            ref={appKeyRef}
            type="password"
            autoComplete="off"
            aria-invalid={invalid("app_key")}
            aria-describedby={describedBy("app_key", ids.secretHint)}
          />
          {fieldError("app_key")}
        </div>
        <div className="kiwoom-settings__row">
          <label htmlFor={ids.secretKey}>
            {t("settings.kiwoom.form.secretKey")}
          </label>
          <input
            id={ids.secretKey}
            ref={secretKeyRef}
            type="password"
            autoComplete="off"
            aria-invalid={invalid("secret_key")}
            aria-describedby={describedBy("secret_key", ids.secretHint)}
          />
          <p className="kiwoom-settings__hint" id={ids.secretHint}>
            {t("settings.kiwoom.form.secret.hint")}
          </p>
          {fieldError("secret_key")}
        </div>
        <div className="kiwoom-settings__row">
          <label htmlFor={ids.account}>
            {t("settings.kiwoom.form.account")}
          </label>
          <input
            id={ids.account}
            ref={accountRef}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            aria-invalid={invalid("account_number")}
            aria-describedby={describedBy("account_number", ids.accountHint)}
          />
          <p className="kiwoom-settings__hint" id={ids.accountHint}>
            {t("settings.kiwoom.form.account.hint")}
          </p>
          {fieldError("account_number")}
        </div>
        {fieldError("form")}
        <Button type="submit" tone="primary" disabled={save.pending}>
          {save.pending
            ? t("settings.kiwoom.form.saving")
            : t("settings.kiwoom.form.submit")}
        </Button>
      </form>
    </section>
  );
};
