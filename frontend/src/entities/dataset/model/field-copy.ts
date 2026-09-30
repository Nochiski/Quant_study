import type { DatasetFieldProfile } from "../../../shared/api";
import { tCode } from "../../../shared/config";

/** 필드 계약의 셀 상태·값 타입·빈도. 값 목록의 owner 는 backend `domain/equity` 이고 생성 SDK 유니온으로 온다. */
export type CellKind =
  DatasetFieldProfile["coverage"]["supported_cell_kinds"][number];
export type FieldValueType = DatasetFieldProfile["value_type"];
export type FieldFrequency = DatasetFieldProfile["frequency"];

/**
 * 필드 계약 어휘의 로케일 문구(#350, 도메인 리뷰 A DR-A-11). 계약 인스펙터·편집기 완성·추적 원시
 * 데이터가 같은 문구를 쓴다. 값이 늘었는데 문구가 없으면 `tCode` 가 typecheck 에서 막는다.
 */
export const cellKindCopy = (kind: CellKind): string =>
  tCode(`dataset.cellKind.${kind}`, kind);

export const fieldValueTypeCopy = (type: FieldValueType): string =>
  tCode(`dataset.valueType.${type}`, type);

export const fieldFrequencyCopy = (frequency: FieldFrequency): string =>
  tCode(`dataset.frequency.${frequency}`, frequency);
