from enum import Enum


class ContentType(Enum):
    """Перечисление, представляющее различные типы контента Rutube."""
    LIVE = "live"
    VIDEO = "video"
    SHORTS = "shorts"
    ACCOUNT = "account"
    PLAYLIST = "playlist"
    

class RutubeErrorCode(Enum):
    """Перечисление, представляющее коды ошибок для операций с Rutube."""
    
    # Успех
    SUCCESS = "SUCCESS"
    
    # Ошибки проверки входных данных (1xx)
    INVALID_URL = "INVALID_URL"
    EMPTY_URL = "EMPTY_URL"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    
    # Сетевые ошибки (2xx)
    CONNECTION_ERROR = "CONNECTION_ERROR"
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
    EXTRACTOR_ERROR = "EXTRACTOR_ERROR"
    PROXY_ERROR = "PROXY_ERROR"
    
    # Ошибки контента (3xx)
    LIVE_STREAM_NOT_SUPPORTED = "LIVE_STREAM_NOT_SUPPORTED"
    PLAYLIST_NOT_SUPPORTED = "PLAYLIST_NOT_SUPPORTED"
    ACCOUNT_NOT_SUPPORTED = "ACCOUNT_NOT_SUPPORTED"
    NO_MEDIA_FORMATS_FOUND = "NO_MEDIA_FORMATS_FOUND"
    NO_THUMBNAILS_FOUND = "NO_THUMBNAILS_FOUND"
    
    # Ошибки файловой системы (4xx)
    COOKIE_FILE_NOT_FOUND = "COOKIE_FILE_NOT_FOUND"
    OUTPUT_PATH_ERROR = "OUTPUT_PATH_ERROR"
    FILE_WRITE_ERROR = "FILE_WRITE_ERROR"
    
    # Системные ошибки (5xx)
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    INITIALIZATION_ERROR = "INITIALIZATION_ERROR"
    EXTRACT_INFO_NOT_CALLED = "EXTRACT_INFO_NOT_CALLED"
    YT_DLP_ERROR = "YT_DLP_ERROR"
