from typing import ClassVar


class CapabilityError(Exception):
    """Closed, secret-safe capability admission errors."""

    messages: ClassVar[dict[str, str]] = {
        "unknown": "The requested tool is not installed.",
        "changed": "The requested tool definition changed after this request was admitted.",
        "disabled": "The requested tool is disabled for this installation.",
        "mutable_output": "This tool output mode is not supported in Phase 4.",
        "unsupported_execution": "This tool execution kind is not supported in Phase 4.",
    }

    def __init__(self, code: str):
        if code not in self.messages:
            raise ValueError("unknown capability error code")
        self.code = code
        super().__init__(self.messages[code])
