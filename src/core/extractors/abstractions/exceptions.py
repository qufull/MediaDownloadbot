from .enums import AbstractErrorCodeModel


class CookieFileNotFoundError(FileNotFoundError):
    """Исключение при отсутствии файла cookie."""
    def __init__(self, message: str, code: AbstractErrorCodeModel = AbstractErrorCodeModel.COOKIE_FILE_NOT_FOUND):
        super().__init__(message)
        self.code = code
        self.message = message


class ExtractInfoNotCalledError(Exception):
    """Исключение при попытке загрузки до вызова extract_info()."""
    def __init__(self, message: str, code: AbstractErrorCodeModel = AbstractErrorCodeModel.EXTRACTION_ERROR):
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedContentTypeError(Exception):
    """Исключение для неподдерживаемых типов контента."""
    def __init__(self, message: str, code: AbstractErrorCodeModel = AbstractErrorCodeModel.CONTENT_NOT_SUPPORTED):
        super().__init__(message)
        self.code = code
        self.message = message
