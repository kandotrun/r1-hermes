from __future__ import annotations


class ChatRunError(RuntimeError):
    """Safe boundary error for authenticated chat handler failures."""

    code = "CHAT_RUN_FAILED"
    safe_message = "chat run failed"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class ChatRunFailedError(ChatRunError):
    code = "CHAT_RUN_FAILED"
    safe_message = "chat run failed"


class ChatRunTimeoutError(ChatRunError):
    code = "CHAT_RUN_TIMEOUT"
    safe_message = "chat run timed out"
