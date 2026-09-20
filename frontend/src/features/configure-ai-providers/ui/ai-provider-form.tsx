import { useId, useRef, useState, type FormEvent } from "react";
import { flushSync } from "react-dom";

import {
  type ProviderKind,
  type ProviderKindView,
} from "../../../entities/assistant";
import { t } from "../../../shared/config";
import { Badge, Button } from "../../../shared/ui";
import {
  providerKindLabel,
  type ProviderField,
  type ProviderRejection,
} from "../model/provider-copy";
import { useCreateProvider } from "../model/use-provider-commands";

type AiProviderFormProps = { kinds: readonly ProviderKindView[] };

const firstInstalled = (
  kinds: readonly ProviderKindView[],
): ProviderKind | null =>
  kinds.find((candidate) => candidate.installed)?.kind ?? null;

/**
 * 공급자 추가 폼.
 *
 * API 키만 비제어 입력이다 — 제출 때 한 번 읽어 요청 본문에 싣고 그 자리에서 입력칸을 비운다. 키는
 * 이 컴포넌트의 state에도, react-query 캐시에도, localStorage에도 들어가지 않는다: 생성은 mutation이
 * 아니라 `useCreateProvider`의 plain async 명령이라 인자가 `MutationCache`에 남지 않는다(리뷰 P1-1).
 * 저장은 서버가 연결 테스트를 통과시킨 뒤에만 일어나므로(spec D6) 제출 버튼이 곧 "연결 테스트 후 저장"이다.
 *
 * `base_url`은 고급 설정이 펼쳐져 있을 때만 전송한다. 접는 것은 "이 값을 쓰지 않는다"는 뜻이고,
 * 키가 실제로 전송될 주소를 사용자가 취소했다고 믿는 채로 보내지 않기 위해서다(리뷰 P2-1).
 */
export const AiProviderForm = ({ kinds }: AiProviderFormProps) => {
  const create = useCreateProvider();
  const secretRef = useRef<HTMLInputElement>(null);
  const kindRef = useRef<HTMLSelectElement>(null);
  const labelRef = useRef<HTMLInputElement>(null);
  const modelRef = useRef<HTMLInputElement>(null);
  const baseUrlRef = useRef<HTMLInputElement>(null);
  const ids = {
    kind: useId(),
    label: useId(),
    model: useId(),
    secret: useId(),
    baseUrl: useId(),
    error: useId(),
  };
  const [pickedKind, setPickedKind] = useState<ProviderKind | null>(null);
  const [label, setLabel] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [rejection, setRejection] = useState<ProviderRejection | null>(null);

  const kind = pickedKind ?? firstInstalled(kinds);
  const installedKind =
    kind !== null && kinds.some((item) => item.kind === kind && item.installed);
  const defaultModel =
    kinds.find((item) => item.kind === kind)?.default_model ?? "";
  const sentBaseUrl = advanced && baseUrl.trim() !== "" ? baseUrl.trim() : null;

  const invalid = (field: ProviderField) =>
    rejection?.field === field ? true : undefined;
  const describedBy = (field: ProviderField, hint?: string) =>
    [hint, rejection?.field === field ? ids.error : undefined]
      .filter(Boolean)
      .join(" ") || undefined;
  /**
   * 거부 사유를 표시하고 그 칸으로 포커스를 옮긴다.
   *
   * `role="alert"`로 읽히기는 하지만 포커스가 제출 버튼에 남으면 고칠 자리를 손으로 찾아야 한다
   * (리뷰 P3-4). `base_url`은 접혀 있을 수 있어 펼침과 포커스를 같은 `flushSync` 뒤에 한다.
   */
  const reject = (next: ProviderRejection) => {
    flushSync(() => {
      if (next.field === "baseUrl") setAdvanced(true);
      setRejection(next);
    });
    const target = {
      kind: kindRef.current,
      label: labelRef.current,
      model: modelRef.current,
      secret: secretRef.current,
      baseUrl: baseUrlRef.current,
      form: null,
    }[next.field];
    target?.focus();
  };

  const fieldError = (field: ProviderField) =>
    rejection?.field === field ? (
      <p className="ai-provider-form__error" id={ids.error} role="alert">
        {rejection.message}
      </p>
    ) : null;

  const send = async (secret: string) => {
    if (kind === null) return;
    const rejected = await create.submit({
      kind,
      label: label.trim(),
      model: model.trim() === "" ? null : model.trim(),
      base_url: sentBaseUrl,
      secret,
    });
    if (rejected === null) {
      setLabel("");
      setModel("");
      setBaseUrl("");
      setAdvanced(false);
      return;
    }
    // 거부가 base_url을 지목하면 그 칸을 화면에 되돌려 놓는다 — 접힌 채로는 고칠 자리가 안 보인다.
    reject(rejected);
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (kind === null || !installedKind) {
      reject({
        field: "kind",
        message: t("assistant.error.provider_not_installed"),
      });
      return;
    }
    if (label.trim() === "") {
      reject({
        field: "label",
        message: t("assistant.provider.form.error.label"),
      });
      return;
    }
    const secretInput = secretRef.current;
    const secret = secretInput === null ? "" : secretInput.value;
    if (secret === "") {
      reject({
        field: "secret",
        message: t("assistant.provider.form.error.secret"),
      });
      return;
    }
    // 읽은 즉시 비운다 — 거부로 끝나도 키가 DOM에 남지 않는다.
    if (secretInput !== null) secretInput.value = "";
    setRejection(null);
    void send(secret);
  };

  return (
    <form className="ai-provider-form" onSubmit={submit}>
      <h3 className="ai-provider-form__title">
        {t("assistant.provider.form.title")}
      </h3>
      <div className="ai-provider-form__row">
        <label htmlFor={ids.kind}>{t("assistant.provider.form.kind")}</label>
        <select
          id={ids.kind}
          ref={kindRef}
          value={kind ?? ""}
          aria-invalid={invalid("kind")}
          aria-describedby={describedBy("kind")}
          onChange={(event) => {
            setPickedKind(event.target.value as ProviderKind);
            setRejection(null);
          }}
        >
          {kinds.map((item) => (
            <option
              key={item.kind}
              value={item.kind}
              disabled={!item.installed}
            >
              {providerKindLabel(item.kind)}
              {item.installed
                ? ""
                : ` — ${t("assistant.provider.notInstalled")}`}
            </option>
          ))}
        </select>
        {fieldError("kind")}
      </div>
      <div className="ai-provider-form__row">
        <label htmlFor={ids.label}>{t("assistant.provider.form.label")}</label>
        <input
          id={ids.label}
          ref={labelRef}
          value={label}
          autoComplete="off"
          aria-invalid={invalid("label")}
          aria-describedby={describedBy("label")}
          onChange={(event) => setLabel(event.target.value)}
        />
        {fieldError("label")}
      </div>
      <div className="ai-provider-form__row">
        <label htmlFor={ids.secret}>
          {t("assistant.provider.form.secret")}
        </label>
        <input
          id={ids.secret}
          ref={secretRef}
          type="password"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={invalid("secret")}
          aria-describedby={describedBy("secret", `${ids.secret}-hint`)}
        />
        <p className="ai-provider-form__hint" id={`${ids.secret}-hint`}>
          {t("assistant.provider.form.secret.hint")}
        </p>
        {fieldError("secret")}
      </div>
      <div className="ai-provider-form__row">
        <label htmlFor={ids.model}>{t("assistant.provider.form.model")}</label>
        <input
          id={ids.model}
          ref={modelRef}
          value={model}
          autoComplete="off"
          placeholder={defaultModel}
          aria-invalid={invalid("model")}
          aria-describedby={describedBy("model", `${ids.model}-hint`)}
          onChange={(event) => setModel(event.target.value)}
        />
        <p className="ai-provider-form__hint" id={`${ids.model}-hint`}>
          {t("assistant.provider.form.model.hint")}
        </p>
        {fieldError("model")}
      </div>
      <div className="ai-provider-form__row">
        <div className="ai-provider-form__advanced">
          <Button
            size="small"
            tone="ghost"
            aria-expanded={advanced}
            onClick={() => setAdvanced(!advanced)}
          >
            {t("assistant.provider.form.advanced")}
          </Button>
          {!advanced && baseUrl.trim() !== "" ? (
            <Badge tone="neutral">
              {t("assistant.provider.form.baseUrl.unused")}
            </Badge>
          ) : null}
        </div>
        {advanced ? (
          <>
            <label htmlFor={ids.baseUrl}>
              {t("assistant.provider.form.baseUrl")}
            </label>
            <input
              id={ids.baseUrl}
              ref={baseUrlRef}
              value={baseUrl}
              autoComplete="off"
              inputMode="url"
              aria-invalid={invalid("baseUrl")}
              aria-describedby={describedBy("baseUrl", `${ids.baseUrl}-hint`)}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            <p className="ai-provider-form__hint" id={`${ids.baseUrl}-hint`}>
              {t("assistant.provider.form.baseUrl.hint")}
            </p>
            {fieldError("baseUrl")}
          </>
        ) : null}
      </div>
      {fieldError("form")}
      <Button type="submit" tone="primary" disabled={create.pending}>
        {create.pending
          ? t("assistant.provider.form.submitting")
          : t("assistant.provider.form.submit")}
      </Button>
    </form>
  );
};
