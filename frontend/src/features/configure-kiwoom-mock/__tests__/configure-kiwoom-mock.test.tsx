import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import type { ReactElement } from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
} from "vitest";

import type {
  KiwoomMockConnectionBody,
  KiwoomMockConnectionCheckBody,
} from "../../../entities/broker";
import { KiwoomMockSettings } from "../ui/kiwoom-mock-settings";

const API = "http://localhost:8000";
const PATH = `${API}/api/v1/broker-connections/kiwoom-mock`;
const APP_KEY = "fake-app-key-0123456789";
const SECRET = "fake-secret-0123456789";
const ACCOUNT = "5012345678";

let status: KiwoomMockConnectionBody;
let saveBodies: Record<string, unknown>[];
let saveReply: () => Response;
let verifyReply: KiwoomMockConnectionCheckBody;
let verifyGate: Promise<void>;
let deleted: number;
let fileReads = 0;
let fileProblems: {
  section: string;
  reason: string;
  fields: string[];
  message: string;
}[] = [];

const ENV_PATH = "C:\\Users\\me\\OneDrive\\문서\\auto_trading\\.env";

const server = setupServer(
  http.get(`${API}/api/v1/settings/credentials-file`, () => {
    fileReads += 1;
    return HttpResponse.json({ path: ENV_PATH, problems: fileProblems });
  }),
  http.get(PATH, () => HttpResponse.json(status)),
  http.put(PATH, async ({ request }) => {
    saveBodies.push((await request.json()) as Record<string, unknown>);
    return saveReply();
  }),
  http.delete(PATH, () => {
    deleted += 1;
    status = { saved: false, account_tail: null };
    return new HttpResponse(null, { status: 204 });
  }),
  http.post(`${PATH}/verify`, async () => {
    const reply = verifyReply;
    await verifyGate;
    return HttpResponse.json(reply);
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
beforeEach(() => {
  fileReads = 0;
  fileProblems = [];
  status = { saved: false, account_tail: null };
  saveBodies = [];
  deleted = 0;
  verifyGate = Promise.resolve();
  saveReply = () => {
    status = { saved: true, account_tail: "5678" };
    return HttpResponse.json(status);
  };
  verifyReply = {
    outcome: "connected",
    message: "연결됐습니다. 키움 모의 계좌가 입력한 계좌번호와 같습니다.",
    account_tail: "5678",
  };
});

const renderWithClient = (ui: ReactElement) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};

const fill = async (
  user: ReturnType<typeof userEvent.setup>,
  account = ACCOUNT,
) => {
  await user.type(screen.getByLabelText("App Key"), APP_KEY);
  await user.type(screen.getByLabelText("App Secret"), SECRET);
  await user.type(screen.getByLabelText("모의 계좌번호"), account);
};

describe("KiwoomMockSettings", () => {
  it("shows the real file the keys are saved in", async () => {
    renderWithClient(<KiwoomMockSettings />);
    const location = await screen.findByTestId("credentials-file-location");
    expect(location).toHaveTextContent(`저장 위치: ${ENV_PATH}`);
  });

  it("reads the file problems again after saving keys", async () => {
    fileProblems = [
      {
        section: "kiwoom",
        reason: "incomplete",
        fields: ["STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT"],
        message:
          "키움 모의 연결 세 줄 중 빠진 줄이 있습니다: STRATEGY_WORKBENCH_KIWOOM_MOCK_ACCOUNT",
      },
    ];
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    expect(await screen.findByTestId("credentials-file-problems")).toBeTruthy();
    const before = fileReads;

    fileProblems = [];
    await fill(user, "5012-3456-78");
    await user.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(fileReads).toBeGreaterThan(before));
    await waitFor(() =>
      expect(screen.queryByTestId("credentials-file-problems")).toBeNull(),
    );
  });

  it("shows nothing saved and keeps the check disabled", async () => {
    renderWithClient(<KiwoomMockSettings />);
    expect(await screen.findByText("저장된 키 없음")).toBeTruthy();
    expect(screen.getByRole("button", { name: "연결 확인" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("sends the three values once, clears the inputs and shows only the tail", async () => {
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await screen.findByText("저장된 키 없음");
    await fill(user, "5012-3456-78");
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText("저장됨 · 계좌 끝 5678")).toBeTruthy();
    expect(saveBodies).toEqual([
      { app_key: APP_KEY, secret_key: SECRET, account_number: "5012-3456-78" },
    ]);
    for (const label of ["App Key", "App Secret", "모의 계좌번호"]) {
      expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
    }
    const text = document.body.textContent ?? "";
    for (const secret of [APP_KEY, SECRET, ACCOUNT])
      expect(text).not.toContain(secret);
  });

  it("puts the server's field message next to the rejected field", async () => {
    saveReply = () =>
      HttpResponse.json(
        {
          detail: {
            code: "broker.credentials_invalid",
            message: "계좌번호는 숫자 10자리입니다('-'는 빼도 됩니다).",
            field: "account_number",
          },
        },
        { status: 422 },
      );
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await screen.findByText("저장된 키 없음");
    await fill(user, "12");
    await user.click(screen.getByRole("button", { name: "저장" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("숫자 10자리");
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByLabelText("모의 계좌번호"),
      ),
    );
  });

  it("asks for all three fields before calling the server", async () => {
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await screen.findByText("저장된 키 없음");
    await user.click(screen.getByRole("button", { name: "저장" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "세 칸을 모두 입력하세요.",
    );
    expect(saveBodies).toEqual([]);
  });

  it("shows the server's connection message as it is", async () => {
    status = { saved: true, account_tail: "5678" };
    verifyReply = {
      outcome: "ip_not_allowed",
      message: "이 PC의 인터넷 주소(IP)가 키움에 등록되지 않았습니다.",
      account_tail: "5678",
    };
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await user.click(await screen.findByRole("button", { name: "연결 확인" }));
    const result = await screen.findByText(
      "이 PC의 인터넷 주소(IP)가 키움에 등록되지 않았습니다.",
    );
    expect(result.getAttribute("data-outcome")).toBe("ip_not_allowed");
  });

  it("deletes only after the confirmation step", async () => {
    status = { saved: true, account_tail: "5678" };
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await user.click(await screen.findByRole("button", { name: "삭제" }));
    expect(deleted).toBe(0);
    await user.click(screen.getByRole("button", { name: "삭제 확인" }));
    expect(await screen.findByText("저장된 키 없음")).toBeTruthy();
    expect(deleted).toBe(1);
  });

  it("offers to save again or delete when the saved entry is unreadable", async () => {
    // 리뷰 #594 P3: 깨진 항목은 500 이 나고 삭제 버튼이 숨어 지울 수 없었다
    status = { saved: false, account_tail: null, unreadable: true };
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    expect(
      await screen.findByText(
        "저장된 키를 읽을 수 없음 — 다시 저장하거나 지우세요",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "연결 확인" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.click(screen.getByRole("button", { name: "삭제" }));
    await user.click(screen.getByRole("button", { name: "삭제 확인" }));
    expect(await screen.findByText("저장된 키 없음")).toBeTruthy();
    expect(deleted).toBe(1);
  });

  it("does not show a late check result for keys saved after it started", async () => {
    // 리뷰 #595 P3: 확인을 기다리는 동안 새 키를 저장하면 옛 키의 "연결됐습니다"가 남았다
    status = { saved: true, account_tail: "5678" };
    let release = () => {};
    verifyGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const user = userEvent.setup();
    renderWithClient(<KiwoomMockSettings />);
    await user.click(await screen.findByRole("button", { name: "연결 확인" }));
    expect(screen.getByRole("button", { name: "저장" })).toHaveProperty(
      "disabled",
      true,
    );
    release();
    expect(
      await screen.findByText(
        "연결됐습니다. 키움 모의 계좌가 입력한 계좌번호와 같습니다.",
      ),
    ).toBeTruthy();
    // 결과가 온 뒤 새 키를 저장하면 그 결과는 지워진다
    await fill(user, "5099999999");
    await user.click(screen.getByRole("button", { name: "저장" }));
    await screen.findByText("저장됨 · 계좌 끝 5678");
    await waitFor(() =>
      expect(
        screen.queryByText(
          "연결됐습니다. 키움 모의 계좌가 입력한 계좌번호와 같습니다.",
        ),
      ).toBeNull(),
    );
  });
});
