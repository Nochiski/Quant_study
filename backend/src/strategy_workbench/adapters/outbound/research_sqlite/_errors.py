class ResearchStorageError(RuntimeError):
    """연구 기록 저장소 상태를 믿거나 안전하게 열 수 없다.

    메시지에는 run_id·칸 이름·기대값만 적고 저장된 요청 본문(전략 spec)은 싣지 않는다.
    """
