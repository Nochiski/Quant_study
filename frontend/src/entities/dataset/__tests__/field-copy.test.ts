import { describe, expect, expectTypeOf, it } from "vitest";

import type { MessageKey } from "../../../shared/config";
import { readOpenApi } from "../../../shared/testing/openapi-codes";
import {
  cellKindCopy,
  fieldFrequencyCopy,
  fieldValueTypeCopy,
  type CellKind,
  type FieldFrequency,
  type FieldValueType,
} from "../model/field-copy";

const HANGUL = /[가-힣]/;

describe("field contract vocabulary copy (#350)", () => {
  // `tCode` 가 생성 enum 누락을 typecheck 에서 막고(en 은 `Record<MessageKey, string>`), 이 테스트는
  // 실행 시 계약 파일과 대조한다. 값 목록의 owner 는 backend `domain/equity` 다.
  it.each([
    ["CellKind", cellKindCopy],
    ["FieldValueType", fieldValueTypeCopy],
    ["FieldFrequency", fieldFrequencyCopy],
  ] as const)("names every %s value the backend can send", (schema, copy) => {
    const values = readOpenApi().components.schemas[schema]?.enum ?? [];
    expect(values.length).toBeGreaterThan(0);
    const untranslated = values.filter(
      (value) => !HANGUL.test((copy as (code: string) => string)(value)),
    );
    expect(untranslated).toEqual([]);
  });

  it("keeps no copy for a value the generated SDK no longer has", () => {
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `dataset.cellKind.${string}`>,
        `dataset.cellKind.${CellKind}`
      >
    >().toBeNever();
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `dataset.valueType.${string}`>,
        `dataset.valueType.${FieldValueType}`
      >
    >().toBeNever();
    expectTypeOf<
      Exclude<
        Extract<MessageKey, `dataset.frequency.${string}`>,
        `dataset.frequency.${FieldFrequency}`
      >
    >().toBeNever();
  });

  it("says a ledger-masked cell apart from an unknown value", () => {
    // 가린 셀은 결측 정책이 채우지 않는 사건 경계다(#298) — 모르는 값(결측)과 다르게 읽혀야 한다.
    expect(cellKindCopy("masked")).toBe("원장이 가림");
    expect(cellKindCopy("missing")).toBe("결측");
  });
});
