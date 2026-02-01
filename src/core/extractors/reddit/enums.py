from enum import Enum


class ContentType(Enum):
    """Перечисление типов контента Reddit."""
    LINK = "link"
    VIDEO = "video"
    IMAGE = "image"
    GALLERY = "gallery"
    UNSUPPORTED = "unsupported"
        

class RedditErrorCode(Enum):
    """Коды ошибок для операций с Reddit."""
    
    # Успех
    SUCCESS = "SUCCESS"
    
    # Ошибки проверки входных данных (1xx)
    INVALID_URL = "INVALID_URL"
    EMPTY_URL = "EMPTY_URL"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    
    # Ошибки аутентификации/API (2xx)
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    API_ERROR = "API_ERROR"
    RATELIMIT_EXCEEDED = "RATELIMIT_EXCEEDED"
    
    # Сетевые ошибки (3xx)
    CONNECTION_ERROR = "CONNECTION_ERROR"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    EXTRACTOR_ERROR = "EXTRACTOR_ERROR"
    PROXY_ERROR = "PROXY_ERROR"
    
    # Ошибки контента (4xx)
    GALLERY_DATA_MISSING = "GALLERY_DATA_MISSING"
    GALLERY_EMPTY = "GALLERY_EMPTY"
    VIDEO_EXTRACTION_FAILED = "VIDEO_EXTRACTION_FAILED"
    IMAGE_EXTRACTION_FAILED = "IMAGE_EXTRACTION_FAILED"
    MEDIA_METADATA_MISSING = "MEDIA_METADATA_MISSING"
    PREVIEW_DATA_MISSING = "PREVIEW_DATA_MISSING"
    
    # Ошибки файловой системы (5xx)
    COOKIE_FILE_NOT_FOUND = "COOKIE_FILE_NOT_FOUND"
    OUTPUT_PATH_ERROR = "OUTPUT_PATH_ERROR"
    FILE_WRITE_ERROR = "FILE_WRITE_ERROR"
    
    # Системные ошибки (6xx)
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    INITIALIZATION_ERROR = "INITIALIZATION_ERROR"
    EXTRACT_INFO_NOT_CALLED = "EXTRACT_INFO_NOT_CALLED"
