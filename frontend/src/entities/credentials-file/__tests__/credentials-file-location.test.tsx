import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { CredentialsFileLocation } from "../ui/credentials-file-location";

const API = "http://localhost:8000";
const ENV_PATH = "/Users/me/Documents/auto_trading/.env";
const FILE_URL = `${API}/api/v1/settings/credentials-file`;

const server = setupServer(
  http.get(FILE_URL, () => HttpResponse.json({ path: ENV_PATH, problems: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

const renderLocation = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<CredentialsFileLocation />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

describe("CredentialsFileLocation", () => {
  it("연결이 저장되는 파일의 실제 경로와 동기화 고지를 보여 준다", async () => {
    renderLocation();

    const location = await screen.findByTestId("credentials-file-location");
    expect(location).toHaveTextContent(`저장 위치: ${ENV_PATH}`);
    expect(
      screen.getByText(
        /OneDrive·iCloud로 동기화되면 키도 함께 올라갈 수 있어요/,
      ),
    ).toBeTruthy();
    expect(screen.queryByTestId("credentials-file-problems")).toBeNull();
  });

  it("저장 파일을 읽지 못한 사유를 서버 문장 그대로 보여 준다", async () => {
    server.use(
      http.get(FILE_URL, () =>
        HttpResponse.json({
          path: ENV_PATH,
          problems: [
            {
              section: "file",
              reason: "not_utf8",
              fields: [],
              message:
                "저장 파일을 UTF-8로 읽지 못했습니다. 편집기에서 UTF-8로 다시 저장하세요.",
            },
          ],
        }),
      ),
    );
    renderLocation();

    const problems = await screen.findByTestId("credentials-file-problems");
    expect(problems).toHaveTextContent("저장 파일을 UTF-8로 읽지 못했습니다.");
    expect(problems.getAttribute("role")).toBe("alert");
  });
});
