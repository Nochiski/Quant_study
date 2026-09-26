class AssistantStorageError(RuntimeError):
    """어시스턴트 저장소 상태를 믿거나 안전하게 열 수 없다.

    비밀은 이 어댑터를 지나지 않는다(키는 `secrets_local`이 가진다). 그래도 메시지에는 진단에
    필요한 식별자·경로·기대값만 적고 저장된 본문(메시지 텍스트·이벤트 JSON)은 싣지 않는다.
    """
