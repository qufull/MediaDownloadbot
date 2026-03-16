import os
import logging
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Optional, Tuple, Union

from praw import Reddit 
from yt_dlp import YoutubeDL
from praw.models import Submission
from yt_dlp.utils import DownloadError, ExtractorError

from ..abstractions import AbstractExtractor
from ..abstractions import CookieFileNotFoundError
from .enums import RedditErrorCode, ContentType
from .models import (
    RedditData,
    RedditAudio,
    RedditImage,
    RedditVideo,
    RedditResult,
)


logger = logging.getLogger("reddit")


class RedditExtractor(AbstractExtractor):
    """
    Загрузчик медиа-контента с Reddit.
    
    Поддерживает загрузку:
    - Галерей изображений
    - Видео-постов
    - Отдельных изображений
    """
    
    # Поддерживаемые домены Reddit
    SUPPORTED_DOMAINS = {"reddit.com", "i.redd.it", "v.redd.it"}

    def __init__(
        self,
        client_id: str, 
        client_secret: str,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
        user_agent: str = "bot/1.0 by TelegramDownloader",
    ) -> None:
        """
        Инициализация загрузчика Reddit.
        
        Args:
            client_id: ID клиента Reddit API
            client_secret: Секрет клиента Reddit API
            retries_count: Количество попыток повтора для загрузок
            proxy: URL прокси-сервера (опционально)
            cookie_path: Путь к файлу cookies (опционально)
            concurrent_download_count: Количество одновременных загрузок фрагментов
            user_agent: Строка User-Agent для запросов
        """
        logger.info("Инициализация загрузчика Reddit")
        
        self.proxy = proxy
        self.cookies_path = Path(cookie_path) if cookie_path else None

        # Проверка существования файла cookie
        if self.cookies_path and not self.cookies_path.exists():
            error_msg = f"Файл cookie не найден: {self.cookies_path}"
            logger.error(error_msg)
            raise CookieFileNotFoundError(error_msg, RedditErrorCode.COOKIE_FILE_NOT_FOUND)

        try:
            # Инициализация клиента Reddit
            self.reddit = Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
            )
            
            # Настройка параметров yt-dlp
            self.ydl_opts: Dict[str, Optional[Union[bool, str, Path]]] = {
                "quiet": True,
                "proxy": self.proxy,
                "no_warnings": False,
                "cookiefile": self.cookies_path,
                
                "playlistend": 1,
                "noplaylist": True,
            }
            
            self._data: Optional[RedditData] = None
            self._last_result: Optional[RedditResult] = None
            
            logger.debug("Загрузчик Reddit успешно инициализирован")
            
        except Exception as e:
            error_msg = f"Ошибка инициализации загрузчика Reddit: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e
        
    def _validate_reddit_url(self, url: str) -> bool:
        """
        Проверка валидности URL Reddit.
        
        Args:
            url: URL для проверки
            
        Returns:
            True если URL является валидным URL Reddit
        """
        try:
            parsed = urlparse(url)
            return any(domain in parsed.netloc for domain in self.SUPPORTED_DOMAINS)
        except Exception as e:
            logger.debug(f"Ошибка проверки URL: {e}")
            return False
    
    def _classify_content_type(self, submission: Submission) -> ContentType:
        """
        Классификация типа контента публикации Reddit.
        
        Args:
            submission: Объект публикации Reddit
            
        Returns:
            ContentType: Классифицированный тип контента
        """
        logger.debug("Классификация типа контента")
        
        if hasattr(submission, "is_gallery") and submission.is_gallery:
            result = ContentType.GALLERY
        elif hasattr(submission, "is_video") and submission.is_video:
            result = ContentType.VIDEO
        elif (hasattr(submission, "post_hint") and 
              submission.post_hint == "image"):
            result = ContentType.IMAGE
        elif (hasattr(submission, "domain") and 
              submission.domain == "i.redd.it"):
            result = ContentType.IMAGE
        elif (hasattr(submission, "domain") and 
              submission.domain == "v.redd.it"):
            result = ContentType.VIDEO
        else:
            result = ContentType.UNSUPPORTED
        
        logger.debug(f"Контент классифицирован как: {result.value}")
        return result
        
    def _extract_gallery(self, submission: Submission) -> None:
        """Извлечение данных из галереи изображений."""
        logger.info(f"Извлечение галереи из поста: {submission.id}")

        if not (hasattr(submission, "gallery_data") and submission.gallery_data):
            error_msg = "Данные галереи не найдены"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.GALLERY_DATA_MISSING,
            )
            return
        
        try:
            gallery_items = submission.gallery_data.get("items", [])
            if not gallery_items:
                error_msg = "Данные галереи пусты"
                logger.error(error_msg)
                self._last_result = RedditResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RedditErrorCode.GALLERY_EMPTY,
                )
                return
            
            image_count = 0
            for idx, item in enumerate(gallery_items):
                media_id = item["media_id"]
                
                if (hasattr(submission, "media_metadata") and 
                    media_id in submission.media_metadata):
                    
                    metadata = submission.media_metadata[media_id]
                    
                    if metadata.get("status") == "valid":
                        for image in metadata.get("p", []):
                            name = f"{metadata.get('e', 'Image')}_{image.get('x', 0)}x{image.get('y', 0)}_{idx}"
                            self._data.images.append(
                                RedditImage(
                                    id=str(uuid4()),
                                    url=image["u"],
                                    name=name,
                                    width=image.get("x"),
                                    height=image.get("y"),
                                )
                            )
                            image_count += 1
                            
            if image_count > 0:
                self._last_result = RedditResult(data=self._data)
                logger.info(f"Успешно извлечено {image_count} изображений из галереи")
            else:
                error_msg = "В галерее не найдено валидных изображений"
                logger.error(error_msg)
                self._last_result = RedditResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RedditErrorCode.MEDIA_METADATA_MISSING,
                )
                
        except Exception as e:
            error_msg = f"Ошибка извлечения галереи: {str(e)}"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.UNEXPECTED_ERROR,
            )
        
    def _get_extension(self, url: str) -> Tuple[str, str]:
        """
        Извлечение имени файла и расширения из URL.
        
        Args:
            url: URL для извлечения
            
        Returns:
            Кортеж (имя, расширение)
        """
        parsed = urlparse(url=url)
        filename = os.path.basename(parsed.path)
        name, ext = os.path.splitext(filename)
        return (name, ext.replace(".", "").lower())
    
    def _extract_image_from_preview(self, submission: Submission) -> bool:
        """Извлечение изображения из данных предпросмотра поста."""
        if not (hasattr(submission, "preview") and submission.preview):
            return False
        
        name, ext = self._get_extension(url=submission.url)
        if not submission.preview:
            return False
        
        images = submission.preview.get("images", [])
        if not images:
            return False
        
        for image in images:
            variants = image.get("variants", {})
            target_variant = variants.get(ext) if ext in variants else None
            
            if target_variant:
                self._add_image_from_data(target_variant, f"{name}_{ext}")
            else:
                self._add_image_from_data(image, name)
                
        return True
    
    def _add_image_from_data(self, image_data: dict, base_name: str) -> None:
        """Добавление изображений из данных предпросмотра."""
        # Дополнительные разрешения
        for idx, resolution in enumerate(image_data.get("resolutions", [])):
            self._data.images.append(
                RedditImage(
                    id=str(uuid4()),
                    url=resolution["url"],
                    name=f"{base_name}_res_{idx}",
                    width=resolution["width"],
                    height=resolution["height"],
                )
            )
            
        # Основное изображение (наивысшее качество)
        if "source" in image_data:
            source = image_data["source"]
            self._data.images.append(
                RedditImage(
                    id=str(uuid4()),
                    url=source["url"],
                    name=f"{base_name}_source",
                    width=source["width"],
                    height=source["height"],
                )
            )
    
    def _extract_image(self, submission: Submission) -> None:
        """Извлечение данных из поста с одним изображением."""
        logger.info(f"Извлечение изображения из поста: {submission.id}")
        
        try:
            if self._extract_image_from_preview(submission=submission):
                self._last_result = RedditResult(data=self._data)
                logger.info(f"Успешно извлечено {len(self._data.images)} вариантов изображения из предпросмотра")
                return
            
            # Резервный вариант: использование прямого URL если предпросмотр недоступен
            self._data.images.append(
                RedditImage(
                    id=str(uuid4()),
                    url=submission.url,
                    name="direct_image",
                    width=None,
                    height=None,
                )
            )
            self._last_result = RedditResult(data=self._data)
            logger.info("Использован прямой URL изображения как резервный вариант")
            
        except Exception as e:
            error_msg = f"Ошибка извлечения изображения: {str(e)}"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.IMAGE_EXTRACTION_FAILED,
            )
        
    def _extract_video(self, submission: Submission) -> None:
        """Извлечение данных из видео-поста."""
        logger.info(f"Извлечение видео из поста: {submission.id}")
        
        ydl_opts = self.ydl_opts.copy()
        ydl_opts['listformats'] = True

        try:
            with YoutubeDL(params=ydl_opts) as ydl:
                data = ydl.extract_info(url=submission.url, download=False)
                logger.debug("Извлечение информации о видео завершено")
                
        except ExtractorError as e:
            error_msg = f"Ошибка экстрактора видео: {str(e)}"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.EXTRACTOR_ERROR,
            )
            return
        
        except DownloadError as e:
            error_msg = f"Ошибка загрузки видео: {str(e)}"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.DOWNLOAD_ERROR,
            )
            return
    
        except Exception as e:
            error_msg = f"Неожиданная ошибка при извлечении видео: {str(e)}"
            logger.error(error_msg)
            self._last_result = RedditResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RedditErrorCode.UNEXPECTED_ERROR,
            )
            return
        
        if data.get("_type") == "playlist":
            for item in data["entries"]:
                self._extract_media_formats(item)
                self._extract_thumbnails(item)
                
        else:
            self._extract_media_formats(data)
            self._extract_thumbnails(data)
        
        self._last_result = RedditResult(data=self._data)
        logger.info(f"Успешно извлечено видео с {len(self._data.videos)} видео-форматами и {len(self._data.audios)} аудио-форматами")

    def _extract_media_formats(self, data: dict) -> None:
        video_count = 0
        audio_count = 0
        for format in data.get("formats", []):
            vcodec = format.get("vcodec", "none")
            acodec = format.get("acodec", "none")
            ext = format.get("ext", "")

            # Проверяем наличие видео-потока (любой рабочий mp4 кодек)
            if vcodec != "none" and (ext == "mp4" or "avc" in vcodec):
                self._data.videos.append(
                    RedditVideo(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                        has_audio=(acodec != "none"),
                        fps=format.get("fps"),
                        width=format.get("width"),
                        height=format.get("height"),
                        total_bitrate=format.get("tbr"),
                    )
                )
                video_count += 1

            # Проверяем наличие аудио-потока (m4a или mp4 контейнеры)
            elif vcodec == "none" and acodec != "none":
                self._data.audios.append(
                    RedditAudio(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                    )
                )
                audio_count += 1
                  
    def _extract_thumbnails(self, data: dict) -> None:
        """Извлечение миниатюр."""
        thumbnail_count = 0
        for idx, thumbnail in enumerate(data.get("thumbnails", [])):
            self._data.thumbnails.append(
                RedditImage(
                    id=str(uuid4()),
                    url=thumbnail["url"],
                    name=f"Thumbnail_{idx}",
                    width=thumbnail.get("width"),
                    height=thumbnail.get("height"),
                )
            )
            thumbnail_count += 1
            
        logger.debug(f"Извлечено {thumbnail_count} миниатюр")
    
    def extract_info(self, url: str) -> RedditResult:
        """
        Извлечение информации о медиа из URL Reddit.
        
        Args:
            url: URL Reddit для извлечения информации
            
        Returns:
            RedditResult: Результат, содержащий извлеченные данные медиа
        """
        logger.info(f"Извлечение информации из URL: {url}")
        
        self._data = RedditData(url=url)
        
        if not url or not isinstance(url, str):
            error_msg = "Предоставлен невалидный URL"
            logger.error(error_msg)
            return RedditResult(
                status="error",
                context=error_msg,
                data=RedditData(url=url),
                code=RedditErrorCode.EMPTY_URL,
            )
            
        if not self._validate_reddit_url(url):
            error_msg = "Невалидный или неподдерживаемый URL Reddit"
            logger.error(error_msg)
            return RedditResult(
                status="error",
                context=error_msg,
                data=RedditData(url=url),
                code=RedditErrorCode.INVALID_URL,
            )
            
        try:
            submission = self.reddit.submission(url=url)

            self._data.title = getattr(submission, "title", None)
            self._data.description = getattr(submission, "selftext", None)
            self._data.author_name = getattr(submission, "subreddit_name_prefixed", None)
            
            # Классификация типа контента и соответствующая обработка
            content_type = self._classify_content_type(submission)
            
            logger.info(f"Обнаружен тип контента: {content_type.value}")
            
            if content_type == ContentType.GALLERY:
                self._data.is_image = True
                self._extract_gallery(submission=submission)
            elif content_type == ContentType.VIDEO:
                self._data.is_video = True
                self._extract_video(submission=submission)
            elif content_type == ContentType.IMAGE or content_type == ContentType.LINK:
                self._data.is_image = True
                self._extract_image(submission=submission)
            else:
                error_msg = f"Неподдерживаемый тип контента: {content_type.value}"
                logger.error(error_msg)
                self._last_result = RedditResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RedditErrorCode.UNSUPPORTED_CONTENT,
                )
                
            return self._last_result
        
        except Exception as e:
            error_msg = f"Ошибка извлечения: {str(e)}"
            logger.error(error_msg)
            return RedditResult(
                status="error",
                context=error_msg,
                data=RedditData(url=url),
                code=RedditErrorCode.UNEXPECTED_ERROR,
            )

    def get_error_description(self, code: RedditErrorCode) -> str:
        """
        Получение человеко-читаемого описания для кода ошибки.
        
        Args:
            code: Значение перечисления кода ошибки
            
        Returns:
            Строка с описанием
        """
        descriptions = {
            RedditErrorCode.SUCCESS.value: "Операция успешно завершена",
            RedditErrorCode.INVALID_URL.value: "Предоставленный URL Reddit невалиден или не поддерживается",
            RedditErrorCode.EMPTY_URL.value: "Предоставлен пустой или невалидный URL",
            RedditErrorCode.UNSUPPORTED_CONTENT.value: "Тип контента Reddit не поддерживается",
            RedditErrorCode.AUTHENTICATION_FAILED.value: "Ошибка аутентификации Reddit API",
            RedditErrorCode.API_ERROR.value: "Reddit API вернул ошибку",
            RedditErrorCode.RATELIMIT_EXCEEDED.value: "Превышен лимит запросов Reddit API",
            RedditErrorCode.CONNECTION_ERROR.value: "Произошла ошибка сетевого соединения",
            RedditErrorCode.DOWNLOAD_ERROR.value: "Ошибка загрузки медиа",
            RedditErrorCode.EXTRACTOR_ERROR.value: "Ошибка извлечения медиа",
            RedditErrorCode.PROXY_ERROR.value: "Ошибка подключения к прокси",
            RedditErrorCode.GALLERY_DATA_MISSING.value: "Данные галереи не найдены в посте",
            RedditErrorCode.GALLERY_EMPTY.value: "Галерея не содержит элементов",
            RedditErrorCode.VIDEO_EXTRACTION_FAILED.value: "Ошибка извлечения видео контента",
            RedditErrorCode.IMAGE_EXTRACTION_FAILED.value: "Ошибка извлечения изображения",
            RedditErrorCode.MEDIA_METADATA_MISSING.value: "Метаданные медиа недоступны",
            RedditErrorCode.PREVIEW_DATA_MISSING.value: "Данные предпросмотра недоступны",
            RedditErrorCode.COOKIE_FILE_NOT_FOUND.value: "Файл cookie не найден",
            RedditErrorCode.OUTPUT_PATH_ERROR.value: "Ошибка пути вывода",
            RedditErrorCode.FILE_WRITE_ERROR.value: "Ошибка записи файла",
            RedditErrorCode.UNEXPECTED_ERROR.value: "Произошла непредвиденная ошибка",
            RedditErrorCode.INITIALIZATION_ERROR.value: "Ошибка инициализации загрузчика",
            RedditErrorCode.EXTRACT_INFO_NOT_CALLED.value: "extract_info() должен быть вызван перед загрузкой",
        }
        return descriptions.get(code, "Неизвестная ошибка")
