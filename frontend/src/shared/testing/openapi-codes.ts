import { readFileSync } from "node:fs";

import { backendFixturePath } from "./backend-fixtures";

type Schema = {
  $ref?: string;
  const?: string;
  enum?: string[];
  properties?: Record<string, Schema>;
  anyOf?: Schema[];
  oneOf?: Schema[];
  discriminator?: { mapping?: Record<string, string> };
};

export type OpenApi = {
  paths: Record<
    string,
    Record<
      string,
      {
        responses: Record<
          string,
          { content?: { "application/json"?: { schema: Schema } } }
        >;
      }
    >
  >;
  components: { schemas: Record<string, Schema> };
};

/** 테스트 전용: backend 가 발행한 계약 파일(`backend/openapi.json`)을 읽는다. */
export const readOpenApi = (): OpenApi =>
  JSON.parse(
    readFileSync(backendFixturePath("../../openapi.json"), "utf8"),
  ) as OpenApi;

/** 응답 스키마에서 닿는 detail 의 `code` 상수를 모두 모은다. 손으로 목록을 적지 않는다. */
const reachableCodes = (openapi: OpenApi, root: Schema): Set<string> => {
  const codes = new Set<string>();
  const seen = new Set<string>();
  const visit = (schema: Schema | undefined): void => {
    if (schema === undefined) return;
    if (schema.$ref !== undefined) {
      if (seen.has(schema.$ref)) return;
      seen.add(schema.$ref);
      visit(openapi.components.schemas[schema.$ref.split("/").at(-1)!]);
      return;
    }
    const code = schema.properties?.code;
    if (code?.const !== undefined) codes.add(code.const);
    for (const value of code?.enum ?? []) codes.add(value);
    for (const ref of Object.values(schema.discriminator?.mapping ?? {}))
      visit({ $ref: ref });
    for (const child of [
      ...Object.values(schema.properties ?? {}),
      ...(schema.anyOf ?? []),
      ...(schema.oneOf ?? []),
    ])
      visit(child);
  };
  visit(root);
  return codes;
};

/** 경로의 모든 연산이 내는 거절(2xx 밖) 응답에서 닿는 `code`. */
export const rejectionCodes = (openapi: OpenApi, path: string): Set<string> =>
  new Set(
    Object.values(openapi.paths[path]!).flatMap((operation) =>
      Object.entries(operation.responses)
        .filter(([status]) => !status.startsWith("2"))
        .flatMap(([, response]) => [
          ...reachableCodes(
            openapi,
            response.content?.["application/json"]?.schema ?? {},
          ),
        ]),
    ),
  );
