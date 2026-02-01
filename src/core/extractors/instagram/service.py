import logging
from uuid import uuid4
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from gallery_dl import extractor, config
from gallery_dl.extractor.instagram import InstagramPostExtractor
from gallery_dl.exception import NoExtractorError, AuthenticationError

from ..abstractions import AbstractExtractor
from ..abstractions import CookieFileNotFoundError
from .enums import InstagramErrorCode, ContentType
from .exceptions import InstagramSessionError
from .models import (
    InstagramData,
    InstagramAudio,
    InstagramImage,
    InstagramVideo,
    InstagramResult,
)


logger = logging.getLogger("instagram")


class InstagramExtractor(AbstractExtractor):
    """
    Загрузчик медиа из Instagram.
    
    Поддерживает:
    - отдельные изображения
    - публикации с видео
    - альбомы (карусели)
    - Reels
    - IGTV
    - Stories
    """

    def __init__(
        self,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
    ) -> None:
        """
        Инициализация загрузчика Instagram.
        
        Аргументы:
            cookie_path: Путь для хранения cookies и сессии
            proxy: URL прокси-сервера (опционально)
        """
        logger.info("Инициализация загрузчика Instagram")
        
        self.proxy = proxy
        self.cookies_path = Path(cookie_path) if cookie_path else None

        if not self.cookies_path or not self.cookies_path.exists():
            error_msg = f"Файл cookie не найден: {self.cookies_path}"
            logger.error(error_msg)
            raise CookieFileNotFoundError(error_msg, InstagramErrorCode.COOKIE_FILE_NOT_FOUND)

        try:
            self.unsupported_types: List[ContentType] = [
                ContentType.UNKNOWN
            ]
            
            self._data: Optional[InstagramData] = None
            self._last_result: Optional[InstagramResult] = None
            
            self._init_loader()
            logger.debug("Загрузчик Instagram успешно инициализирован")

        except Exception as e:
            error_msg = f"Ошибка инициализации загрузчика Instagram: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e

    def _validate_instagram_url(self, url: str) -> bool:
        """
        Проверка корректности URL Instagram.
        
        Аргументы:
            url: URL для проверки
            
        Возвращает:
            True, если URL корректный
        """
        try:
            parsed_url = urlparse(url=url)
            return parsed_url.netloc.endswith("instagram.com")
        except Exception as e:
            logger.debug(f"Ошибка при проверке URL: {e}")
            return False
        
    def _classify_url(self, url: str) -> ContentType:
        """
        Классификация типа контента Instagram.
        
        Args:
            url: URL Instagram для классификации
            
        Returns:
            ContentType: Классифицированный тип контента
        """
        logger.debug(f"Классификация URL: {url}")
        
        try:
            parsed = urlparse(url)
            path_parts = parsed.path.strip("/").split("/")
            
            if not path_parts:
                return ContentType.UNKNOWN
            
            if "reel" in path_parts:
                result = ContentType.REEL
            elif "tv" in path_parts:
                result = ContentType.IGTV 
            elif "p" in path_parts:
                result = ContentType.POST
            elif "stories" in path_parts:
                result = ContentType.STORIES
            else:
                result = ContentType.UNKNOWN
                
            logger.debug(f"URL классифицирован как: {result.value}")
            return result
        
        except Exception as e:
            logger.error(f"Ошибка классификации URL: {e}")
            return ContentType.UNKNOWN
        
    def _init_loader(self) -> None:
        """Инициализация и аутентификация в Instagram."""
        try:
            config.set(("extractor", "instagram"), "cookies", str(self.cookies_path))
            if self.proxy:
                config.set(("extractor", "instagram"), "proxy", self.proxy)
            
        except Exception as e:
            error_msg = f"Неожиданная ошибка при инициализации: {e}"
            logger.error(error_msg)
            raise InstagramSessionError(error_msg, InstagramErrorCode.INITIALIZATION_ERROR)

    def _extract_media_info(self, url: str) -> InstagramResult:
        """Основной метод извлечения медиа информации."""
        try:
            extr: InstagramPostExtractor = extractor.find(url=url)
            if not extr:
                error_msg = "Экстрактор не найден для данного URL"
                logger.error(error_msg)
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=InstagramErrorCode.NO_EXTRACTOR_FOUND,
                )
            
            extr.initialize()
            data = list(extr.items())
            
            if not data:
                error_msg = "Контент не найден для данного URL"
                logger.error(error_msg)
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=InstagramErrorCode.NO_CONTENT_FOUND,
                )
            
            content_class = self._classify_url(url=url)
            
            for item in data:
                content_type = item[0]
                item_data = item[-1]
                
                if content_type == 2:  # Метаданные
                    self._data.title = item_data.get("fullname")
                    self._data.author_name = item_data.get("username")
                    self._data.description = item_data.get("description")
                    
                    # Проверка на множественные сторис
                    if (
                        content_class == ContentType.STORIES
                        and item_data.get("count", 0) > 1
                    ):
                        error_msg = "Множественные сторис не поддерживаются"
                        logger.warning(error_msg)
                        return InstagramResult(
                            status="error",
                            data=self._data,
                            context=error_msg,
                            code=InstagramErrorCode.CONTENT_NOT_SUPPORTED,
                        )
                    
                elif content_type == 3:  # Медиа
                    if item_data.get("video_url") is not None:
                        self._data.is_video = True
                        self._data.videos.append(
                            InstagramVideo(
                                id=uuid4(),
                                has_audio=True,
                                url=item_data["video_url"],
                                width=item_data.get("width"),
                                height=item_data.get("height"),
                                name=f"{item_data['filename']}.{item_data['extension']}",
                            )
                        )
                        self._data.thumbnails.append(
                            InstagramImage(
                                id=uuid4(),
                                url=item_data["display_url"],
                                width=item_data.get("width"),
                                height=item_data.get("height"),
                                name=f"Thumbnail_{item_data['filename']}",
                            )
                        )
                        
                    else:
                        self._data.is_image = True
                        self._data.images.append(
                            InstagramImage(
                                id=uuid4(),
                                url=item_data["display_url"],
                                width=item_data.get("width"),
                                height=item_data.get("height"),
                                name=f"{item_data['filename']}.{item_data['extension']}",
                            )
                        )
            
            # Проверка наличия медиа
            if not self._data.videos and not self._data.images:
                error_msg = "Медиа не найдено в контенте"
                logger.warning(error_msg)
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=InstagramErrorCode.NO_MEDIA_FOUND,
                )
                        
            logger.info(f"Извлечено {len(self._data.videos)} видео и {len(self._data.images)} изображений")
            return InstagramResult(data=self._data)
            
        except AuthenticationError as e:
            error_msg = f"Ошибка аутентификации: {e}"
            logger.error(error_msg)
            return InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.AUTHENTICATION_FAILED,
            )
        except NoExtractorError as e:
            error_msg = f"Экстрактор не найден: {e}"
            logger.error(error_msg)
            return InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.NO_EXTRACTOR_FOUND,
            )
        except Exception as e:
            error_msg = f"Ошибка извлечения медиа: {e}"
            logger.error(error_msg)
            return InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.EXTRACTION_ERROR,
            )
        
    def extract_info(self, url: str) -> InstagramResult:
        """
        Извлечение медиа информации из URL Instagram.
        
        Args:
            url: URL Instagram для извлечения информации
            
        Returns:
            InstagramResult: Результат, содержащий извлеченные медиа данные
        """
        logger.info(f"Извлечение информации из URL: {url}")
        
        self._data = InstagramData(url=url)
        
        # Проверка URL
        if not url or not isinstance(url, str):
            error_msg = "Предоставлен неверный URL"
            logger.error(error_msg)
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.EMPTY_URL,
            )
            return self._last_result
        
        if not self._validate_instagram_url(url):
            error_msg = "Неверный или неподдерживаемый URL Instagram"
            logger.error(error_msg)
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.INVALID_URL,
            )
            return self._last_result
        
        # Классификация URL
        content_type = self._classify_url(url=url)
        
        if content_type in self.unsupported_types:
            error_msg = f"Неподдерживаемый тип контента: {content_type.value}"
            logger.warning(error_msg)
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=InstagramErrorCode.CONTENT_NOT_SUPPORTED,
            )
            return self._last_result
        
        # Извлечение информации
        self._last_result = self._extract_media_info(url=url)
        return self._last_result

    def get_error_description(self, code: InstagramErrorCode) -> str:
        """
        Получение человеко-читаемого описания для кода ошибки.
        
        Args:
            code: Значение перечисления кода ошибки
            
        Returns:
            Строка описания
        """
        descriptions = {
            InstagramErrorCode.SUCCESS.value: "Операция успешно завершена",
            InstagramErrorCode.INVALID_URL.value: "Предоставленный URL Instagram неверен или не поддерживается",
            InstagramErrorCode.EMPTY_URL.value: "Предоставлен пустой или неверный URL",
            InstagramErrorCode.INVALID_SHORTCODE.value: "Не удалось извлечь shortcode из URL",
            InstagramErrorCode.AUTHENTICATION_FAILED.value: "Ошибка аутентификации в Instagram",
            InstagramErrorCode.SESSION_LOAD_FAILED.value: "Не удалось загрузить сессию",
            InstagramErrorCode.SESSION_SAVE_FAILED.value: "Не удалось сохранить сессию",
            InstagramErrorCode.CONNECTION_ERROR.value: "Произошла ошибка сетевого соединения",
            InstagramErrorCode.TIMEOUT_ERROR.value: "Превышено время ожидания",
            InstagramErrorCode.BAD_RESPONSE.value: "Получен некорректный ответ от сервера",
            InstagramErrorCode.COOKIE_FILE_NOT_FOUND.value: "Файл cookie не найден",
            InstagramErrorCode.POST_NOT_FOUND.value: "Пост не найден",
            InstagramErrorCode.POST_CHANGED.value: "Пост был изменен или удален",
            InstagramErrorCode.PROFILE_NOT_EXISTS.value: "Профиль не существует",
            InstagramErrorCode.CONTENT_NOT_SUPPORTED.value: "Тип контента не поддерживается",
            InstagramErrorCode.EXTRACTION_ERROR.value: "Не удалось извлечь медиа",
            InstagramErrorCode.NO_EXTRACTOR_FOUND.value: "Не найден подходящий экстрактор для URL",
            InstagramErrorCode.NO_CONTENT_FOUND.value: "Контент не найден для данного URL",
            InstagramErrorCode.METADATA_EXTRACTION_FAILED.value: "Не удалось извлечь метаданные",
            InstagramErrorCode.NO_MEDIA_FOUND.value: "Медиа не найдено в контенте",
            InstagramErrorCode.UNEXPECTED_ERROR.value: "Произошла непредвиденная ошибка",
            InstagramErrorCode.INITIALIZATION_ERROR.value: "Не удалось инициализировать загрузчик",
            InstagramErrorCode.DOWNLOAD_ERROR.value: "Не удалось загрузить медиа",
            InstagramErrorCode.GALLERY_DL_ERROR.value: "Произошла внутренняя ошибка gallery-dl",
        }
        return descriptions.get(code, "Неизвестная ошибка")
