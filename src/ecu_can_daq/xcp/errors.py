from __future__ import annotations


class XcpError(RuntimeError):
    pass


class XcpTimeoutError(XcpError, TimeoutError):
    pass


class XcpNegativeResponseError(XcpError):
    def __init__(self, code: int | None, message: str = ""):
        self.code = code
        super().__init__(f"XCP negative response: {code} {message}".strip())
