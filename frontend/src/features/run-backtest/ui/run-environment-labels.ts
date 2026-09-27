import { tName, tOptional } from "../../../shared/config";
import type { RunEnvironmentField } from "../model/run-environment";

/** 실행 설정 칸의 이름. 설명 키가 없으면 필드 이름을 그대로 보인다(스키마가 키를 발행하지 않은 경우). */
export const runEnvironmentLabel = (field: RunEnvironmentField): string =>
  tName(field.descriptionKey) ?? field.name;

/** enum 값의 이름(`<key>.value.<값>`). 번역이 없으면 값 그대로. */
export const runEnvironmentOptionLabel = (
  field: RunEnvironmentField,
  value: string,
): string =>
  (field.descriptionKey === null
    ? undefined
    : tOptional(`${field.descriptionKey}.value.${value}`)) ?? value;
