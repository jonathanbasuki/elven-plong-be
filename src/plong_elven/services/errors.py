"""Application-wide error type, replacing DispatchError."""


class AppError(Exception):
    """Raised by services when a business rule is violated. Handlers convert this
    to an HTTP response with the same status_code and {"error": {"code": ..., "message": ...}}."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
