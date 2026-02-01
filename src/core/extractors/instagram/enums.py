from enum import Enum


class ContentType(Enum):
    """Типы контента Instagram."""
    REEL = "reel"
    POST = "post"
    IGTV = "igtv"
    STORIES = "stories"
    UNKNOWN = "unknown"
    
    
class InstagramErrorCode(Enum):
    """Коды ошибок, возникающих при работе с Instagram."""
    
    # Успех
    SUCCESS = "SUCCESS"
    
    # Ошибки валидации ввода (1xx)
    INVALID_URL = "INVALID_URL"              # Неверный URL
    INVALID_SHORTCODE = "INVALID_SHORTCODE"  # Ошибка извлечения shortcode
    EMPTY_URL = "EMPTY_URL"                  # Пустой URL
    
    # Ошибки аутентификации (2xx)
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"   # Ошибка входа
    SESSION_LOAD_FAILED = "SESSION_LOAD_FAILED"       # Ошибка загрузки сессии
    SESSION_SAVE_FAILED = "SESSION_SAVE_FAILED"       # Ошибка сохранения сессии
    
    # Сетевые ошибки (3xx)
    CONNECTION_ERROR = "CONNECTION_ERROR"    # Ошибка соединения
    TIMEOUT_ERROR = "TIMEOUT_ERROR"          # Превышен таймаут
    BAD_RESPONSE = "BAD_RESPONSE"            # Некорректный ответ
    
    # Ошибки контента (4xx)
    COOKIE_FILE_NOT_FOUND = "COOKIE_FILE_NOT_FOUND"
    POST_NOT_FOUND = "POST_NOT_FOUND"        # Пост не найден
    POST_CHANGED = "POST_CHANGED"            # Пост изменился
    PROFILE_NOT_EXISTS = "PROFILE_NOT_EXISTS" # Профиль не существует
    CONTENT_NOT_SUPPORTED = "CONTENT_NOT_SUPPORTED"   # Неподдерживаемый контент
    EXTRACTION_ERROR = "EXTRACTION_ERROR"    # Ошибка извлечения
    NO_EXTRACTOR_FOUND = "NO_EXTRACTOR_FOUND" # Экстрактор не найден
    NO_CONTENT_FOUND = "NO_CONTENT_FOUND"    # Контент не найден
    METADATA_EXTRACTION_FAILED = "METADATA_EXTRACTION_FAILED" # Ошибка извлечения метаданных
    NO_MEDIA_FOUND = "NO_MEDIA_FOUND"        # Медиа не найдено
    
    # Системные ошибки (5xx)
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"    # Неожиданная ошибка
    INITIALIZATION_ERROR = "INITIALIZATION_ERROR" # Ошибка инициализации
    DOWNLOAD_ERROR = "DOWNLOAD_ERROR"        # Ошибка загрузки
    GALLERY_DL_ERROR = "GALLERY_DL_ERROR"    # Ошибка gallery-dl
