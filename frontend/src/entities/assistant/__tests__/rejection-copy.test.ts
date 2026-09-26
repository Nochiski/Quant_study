import { describe, expect, it } from "vitest";

import {
  ASSISTANT_TURN_IN_PROGRESS,
  AssistantRequestError,
  assistantRejectionMessage,
  isAssistantRejectionCode,
  type AssistantRejectionCode,
} from "..";
import { t } from "../../../shared/config";

const rejected = (code: string | undefined) =>
  new AssistantRequestError("test", 422, code, null);

/**
 * 생성 SDK의 거부 코드 전수. `Record`라 코드가 늘면 이 목록도 컴파일되지 않는다 — 표와 테스트가
 * 같은 합집합을 본다.
 */
const EVERY_CODE = Object.keys({
  "assistant.base_url_rejected": true,
  "assistant.document_ref_invalid": true,
  "assistant.no_active_provider": true,
  "assistant.no_running_turn": true,
  "assistant.probe_failed": true,
  "assistant.provider.not_found": true,
  "assistant.provider_not_installed": true,
  "assistant.provider_secret_missing": true,
  "assistant.session.not_found": true,
  "assistant.turn.not_found": true,
  "assistant.turn_in_progress": true,
} satisfies Record<AssistantRejectionCode, true>) as AssistantRejectionCode[];

/** 일반 문구로 두기로 정한 코드. 이미 끝난 턴의 중지 거절은 이력이 곧 설명한다. */
const GENERIC_BY_DESIGN: readonly AssistantRejectionCode[] = [
  "assistant.no_running_turn",
];

describe("assistantRejectionMessage", () => {
  it.each(EVERY_CODE)("%s를 아는 코드로 판정한다", (code) => {
    expect(isAssistantRejectionCode(code)).toBe(true);
  });

  it.each(EVERY_CODE.filter((code) => !GENERIC_BY_DESIGN.includes(code)))(
    "%s는 구체 사유 문구로 옮긴다",
    (code) => {
      expect(assistantRejectionMessage(rejected(code))).not.toBe(
        t("assistant.error.unknown"),
      );
    },
  );

  it("두 화면이 함께 만나는 코드는 같은 문구다", () => {
    // 설정 화면과 사이드바가 표를 나눠 들던 시절에는 한쪽만 고쳐도 컴파일러가 침묵했다(감사 NB-1).
    expect(
      assistantRejectionMessage(rejected("assistant.provider_secret_missing")),
    ).toBe(t("assistant.error.provider_secret_missing"));
    expect(
      assistantRejectionMessage(rejected("assistant.turn.not_found")),
    ).toBe(t("assistant.error.not_found"));
  });

  it("진행 중 턴 거부는 채팅 안내 문구다", () => {
    expect(
      assistantRejectionMessage(rejected(ASSISTANT_TURN_IN_PROGRESS)),
    ).toBe(t("assistant.chat.turnInProgress"));
  });

  it("모르는 코드·코드 없음·다른 오류는 일반 문구로 떨어진다", () => {
    const unknown = t("assistant.error.unknown");
    expect(assistantRejectionMessage(rejected("assistant.renamed"))).toBe(
      unknown,
    );
    expect(assistantRejectionMessage(rejected(undefined))).toBe(unknown);
    expect(assistantRejectionMessage(new Error("boom"))).toBe(unknown);
  });

  it("객체 프로토타입의 이름을 코드로 착각하지 않는다", () => {
    expect(isAssistantRejectionCode("toString")).toBe(false);
    expect(isAssistantRejectionCode("constructor")).toBe(false);
  });
});
