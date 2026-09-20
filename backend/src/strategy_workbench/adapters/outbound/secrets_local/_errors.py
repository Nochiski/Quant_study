class SecretStoreStorageError(RuntimeError):
    """비밀 파일을 읽거나 안전하게 쓸 수 없다.

    이 예외의 메시지에는 **절대 비밀 값을 넣지 않는다.** 진단에 필요한 것은 경로·profile_id·
    OS 오류이지 키가 아니다(`.claude/rules/error-messages.md` 보안 예외, 설계 spec 완료 정의 3).
    """
