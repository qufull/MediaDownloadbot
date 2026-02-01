from enum import Enum


class AbstractErrorCodeModel(Enum):
    """
    Базовый класс для кодов ошибок сервиса.
    
    Этот enum должен быть расширен конкретными реализациями сервисов,
    чтобы задавать свои собственные коды ошибок.
    """
    SUCCESS = "SUCCESS"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    COOKIE_FILE_NOT_FOUND = "COOKIE_FILE_NOT_FOUND"
    CONTENT_NOT_SUPPORTED = "CONTENT_NOT_SUPPORTED"
