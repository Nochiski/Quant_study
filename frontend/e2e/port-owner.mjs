/**
 * 포트를 쥐고 있는 프로세스가 누구인지 최선껏 알아낸다.
 *
 * 잠금은 **자기 잠금의 stale 만** 회수한다. Playwright 만 죽고 uvicorn·vite preview 가 남는 경우
 * (사용량 한도로 세션이 끊기는 등) 그 고아 서버는 포트를 쥔 채 남고, 다음 실행은 "포트가 쓰이는
 * 중"이라는 사실만 알 뿐 그게 고아인지 남의 정상 실행인지 구별하지 못한다. 그래서 pid·시작 시각·
 * 커맨드를 찍어 사람이 판단할 수 있게 한다.
 *
 * **자동으로 죽이지 않는다.** 남의 정상 실행일 수 있고, 잘못 죽이면 그쪽 게이트가 깨진다.
 *
 * 전부 최선껏이다 — 조회에 실패하면 `null` 필드로 두고 진행한다. 이 정보는 오류 메시지를 풍부하게
 * 할 뿐 판정에 쓰이지 않는다.
 */

import { spawnSync } from "node:child_process";

const run = (command, args) => {
  try {
    const result = spawnSync(command, args, {
      encoding: "utf8",
      timeout: 5000,
      windowsHide: true,
      shell: false,
    });
    return result.status === 0 ? (result.stdout ?? "") : "";
  } catch {
    return "";
  }
};

/** 오류 한 줄에 들어갈 만큼만. 커맨드라인은 길고 줄바꿈까지 품는다. */
const shorten = (value, limit = 160) => {
  if (typeof value !== "string") return null;
  const flat = value.replace(/\s+/g, " ").trim();
  if (flat === "") return null;
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
};

/** WMI 의 `/Date(1789960856715)/` 를 사람이 읽는 ISO 로. 모양이 다르면 원문 그대로. */
const readWmiDate = (value) => {
  if (typeof value !== "string") return null;
  const epoch = value.match(/\/Date\((\d+)\)\//);
  if (epoch === null) return value;
  return new Date(Number(epoch[1])).toISOString();
};

/**
 * 그 포트를 LISTEN 중인 pid. 못 찾으면 null.
 * @param {number} port
 * @returns {number | null}
 */
export const listenerPid = (port) => {
  if (process.platform === "win32") {
    const table = run("netstat", ["-ano", "-p", "tcp"]);
    for (const line of table.split(/\r?\n/)) {
      if (!line.includes("LISTENING")) continue;
      const columns = line.trim().split(/\s+/);
      const local = columns[1] ?? "";
      if (!local.endsWith(`:${port}`)) continue;
      const pid = Number(columns[columns.length - 1]);
      if (Number.isInteger(pid) && pid > 0) return pid;
    }
    return null;
  }
  const found = run("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"]);
  const pid = Number(found.split(/\r?\n/)[0]);
  return Number.isInteger(pid) && pid > 0 ? pid : null;
};

/**
 * pid 의 시작 시각과 커맨드. 못 읽으면 null 필드.
 * @param {number} pid
 */
const describeProcess = (pid) => {
  if (process.platform === "win32") {
    const json = run("powershell", [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" | ` +
        "Select-Object CreationDate,CommandLine | ConvertTo-Json -Compress",
    ]);
    try {
      const parsed = JSON.parse(json);
      return {
        startedAt: readWmiDate(parsed?.CreationDate),
        command: shorten(parsed?.CommandLine),
      };
    } catch {
      return { startedAt: null, command: null };
    }
  }
  const line = run("ps", ["-o", "lstart=,command=", "-p", String(pid)]).trim();
  if (line === "") return { startedAt: null, command: null };
  // `lstart` 는 공백 5칸짜리 고정 폭이라 그 뒤가 커맨드다.
  const match = line.match(/^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.*)$/);
  return match
    ? { startedAt: match[1], command: shorten(match[2]) }
    : { startedAt: null, command: shorten(line) };
};

/**
 * 포트 주인 한 줄 설명. 주인을 못 찾으면 null.
 * @param {number} port
 */
export const describePortOwner = (port) => {
  const pid = listenerPid(port);
  if (pid === null) return null;
  const { startedAt, command } = describeProcess(pid);
  return { pid, startedAt, command };
};
