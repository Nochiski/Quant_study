import { getCredentialsFile } from "./generated/sdk.gen";
import type { CredentialsFileBody } from "./generated/types.gen";

/**
 * 설정 화면이 보여 줄 저장 위치(2026-10-24 사용자 결정). AI 공급자와 키움 모의 연결이 함께 쓰는 내 문서
 * `auto_trading/.env`의 절대 경로다. 서버를 켠 OS가 해석한 값을 그대로 받는다. 파일 내용은 오지 않는다.
 */
export const settingsApi = {
  async credentialsFile(): Promise<CredentialsFileBody> {
    const response = await getCredentialsFile();
    if (response.error !== undefined || response.data === undefined) {
      throw new Error(
        `Credentials file request failed: getCredentialsFile status=${response.response?.status ?? 0}`,
      );
    }
    return response.data;
  },
};
