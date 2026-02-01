from .enums import InstagramErrorCode


class InstagramSessionError(Exception):
    """Исключение для ошибок сессии Instagram."""
    def __init__(self, message: str, code: InstagramErrorCode = InstagramErrorCode.AUTHENTICATION_FAILED):
        super().__init__(message)
        self.code = code
        self.message = message
