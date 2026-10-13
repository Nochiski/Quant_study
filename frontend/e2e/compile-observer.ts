/** 컴파일 응답 관찰은 마지막 요청만 소유한다. 제품의 문서 검증 상태를 대신하지 않는다. */
export const createCompileObserver = <RequestIdentity>() => {
  let latestRequest: RequestIdentity | undefined;
  let hash: string | null = null;
  let failure: unknown;
  let failed = false;
  return {
    requested(request: RequestIdentity) {
      latestRequest = request;
      hash = null;
      failure = undefined;
      failed = false;
    },
    async received(
      request: RequestIdentity,
      readBody: () => Promise<{ spec_hash: string | null }>,
    ): Promise<void> {
      // 편집으로 대체된 응답은 본문이 취소·해제됐을 수 있으므로 읽기부터 시작하지 않는다.
      if (request !== latestRequest) return;
      try {
        const body = await readBody();
        if (request !== latestRequest) return;
        hash = body.spec_hash;
      } catch (error) {
        // await 중 새 요청이 소유권을 가져간 경우에만 이전 오류를 버린다. 현재 요청의 JSON·전송
        // 오류는 마지막 hash 단언에서 다시 던진다. 이벤트 리스너의 미처리 rejection으로 다른
        // UI 단언을 중간에 끝내거나, 실제 계약 실패를 성공으로 바꾸지 않는다.
        if (request !== latestRequest) return;
        failure = error;
        failed = true;
      }
    },
    specHash(): string | null {
      if (failed) throw failure;
      return hash;
    },
  };
};
