from .enums import RedditErrorCode


class InvalidRedditUrlError(ValueError):
    """Исключение при некорректном URL Reddit."""
    def __init__(self, message: str, code: RedditErrorCode = RedditErrorCode.INVALID_URL):
        super().__init__(message)
        self.code = code
        self.message = message

