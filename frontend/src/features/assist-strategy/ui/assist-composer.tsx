import { useId, useState, type KeyboardEvent } from "react";

import { t } from "../../../shared/config";
import { Button } from "../../../shared/ui";

type AssistComposerProps = {
  /** 진행 중 턴이 있으면 전송 대신 중지를 보여 준다. */
  running: boolean;
  /** 세션 생성·턴 시작 요청이 도는 중. 같은 질문을 두 번 보내지 않게 막는다. */
  busy: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
};

/**
 * 질문 입력칸.
 *
 * Enter는 전송, Shift+Enter는 줄바꿈이다. 한글 입력은 조합 중에도 Enter를 내므로
 * `isComposing`일 때는 전송하지 않는다 — 그러지 않으면 마지막 글자를 확정하는 Enter가 질문을
 * 보내 버린다.
 */
export const AssistComposer = ({
  running,
  busy,
  onSend,
  onCancel,
}: AssistComposerProps) => {
  const inputId = useId();
  const [text, setText] = useState("");

  const submit = () => {
    if (running || busy || text.trim() === "") return;
    onSend(text);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
  };

  return (
    <form
      className="assist__composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label className="sr-only" htmlFor={inputId}>
        {t("assistant.chat.input")}
      </label>
      <textarea
        className="assist__input"
        id={inputId}
        rows={3}
        value={text}
        placeholder={t("assistant.chat.input.placeholder")}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="assist__composer-actions">
        {running ? (
          <Button size="small" tone="danger" onClick={onCancel}>
            {t("assistant.chat.stop")}
          </Button>
        ) : (
          <Button
            size="small"
            tone="primary"
            type="submit"
            disabled={busy || text.trim() === ""}
          >
            {t("assistant.chat.send")}
          </Button>
        )}
      </div>
    </form>
  );
};
