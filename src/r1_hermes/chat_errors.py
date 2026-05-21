from __future__ import annotations


class ChatRunError(RuntimeError):
    """Safe boundary error for authenticated chat handler failures."""

    code = "CHAT_RUN_FAILED"
    safe_message = "chat run failed"

    def __init__(self, safe_message: str | None = None) -> None:
        if safe_message is not None:
            self.safe_message = safe_message
        super().__init__(self.safe_message)


class ChatRunFailedError(ChatRunError):
    code = "CHAT_RUN_FAILED"
    safe_message = "chat run failed"


class ChatRunTimeoutError(ChatRunError):
    code = "CHAT_RUN_TIMEOUT"
    safe_message = "run exceeded the R1 gateway timeout limit"


class ChatOutputTooLargeError(ChatRunError):
    code = "CHAT_OUTPUT_TOO_LARGE"
    safe_message = "chat response exceeded the outbound size limit"
