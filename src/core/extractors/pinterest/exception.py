from .enums import PinterestErrorCode


class PinterestSessionError(Exception):
    """Исключение для ошибок сессии Pinterest."""
    def __init__(self, message: str, code: PinterestErrorCode = PinterestErrorCode.AUTHENTICATION_FAILED):
        super().__init__(message)
        self.code = code
        self.message = message