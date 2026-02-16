import logging
from uuid import uuid4
from typing import List, Optional
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..abstractions import AbstractExtractor
from .enums import InstagramErrorCode, ContentType
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
    Загрузчик медиа из Instagram через yt-dlp (без cookies).

    Поддерживает:
    - отдельные изображения
    - публикации с видео
    - альбомы (карусели)
    - Reels
    - IGTV
    """

    def __init__(
        self,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
    ) -> None:
        logger.info("Инициализация загрузчика Instagram (yt-dlp, без cookies)")

        self.proxy = proxy
        self.cookie_path = cookie_path

        self.unsupported_types: List[ContentType] = [
            ContentType.UNKNOWN,
        ]

        self._data: Optional[InstagramData] = None
        self._last_result: Optional[InstagramResult] = None

        # Базовые опции yt-dlp
        self._base_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,       # Позволяем карусели
            "extract_flat": False,
            "skip_download": True,     # Только extract, не качаем
        }

        if self.proxy:
            self._base_opts["proxy"] = self.proxy

        # Куки опциональны — если есть, подключаем для приватного контента
        if self.cookie_path:
            from pathlib import Path
            if Path(self.cookie_path).exists():
                self._base_opts["cookiefile"] = self.cookie_path
                logger.info(f"Cookies подключены: {self.cookie_path}")
            else:
                logger.warning(f"Cookie файл не найден: {self.cookie_path}, работаем без cookies")

        logger.debug("Загрузчик Instagram (yt-dlp) успешно инициализирован")

    def _validate_instagram_url(self, url: str) -> bool:
        try:
            parsed_url = urlparse(url=url)
            return parsed_url.netloc.endswith("instagram.com")
        except Exception as e:
            logger.debug(f"Ошибка при проверке URL: {e}")
            return False

    def _classify_url(self, url: str) -> ContentType:
        logger.debug(f"Классификация URL: {url}")
        try:
            parsed = urlparse(url)
            path_parts = parsed.path.strip("/").split("/")

            if not path_parts:
                return ContentType.UNKNOWN

            if "reel" in path_parts or "reels" in path_parts:
                return ContentType.REEL
            elif "tv" in path_parts:
                return ContentType.IGTV
            elif "p" in path_parts:
                return ContentType.POST
            elif "stories" in path_parts:
                return ContentType.STORIES
            else:
                return ContentType.UNKNOWN

        except Exception as e:
            logger.error(f"Ошибка классификации URL: {e}")
            return ContentType.UNKNOWN

    def _extract_media_info(self, url: str) -> InstagramResult:
        """Основной метод извлечения медиа информации через yt-dlp."""
        try:
            opts = dict(self._base_opts)

            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context="yt-dlp не вернул данные",
                    code=InstagramErrorCode.NO_CONTENT_FOUND,
                )

            # Автор
            uploader = (
                info.get("uploader")
                or info.get("channel")
                or info.get("uploader_id")
                or "Unknown"
            )
            title = info.get("title") or info.get("description", "")[:50] or "Instagram"

            self._data.author_name = uploader
            self._data.title = title
            self._data.description = info.get("description")

            # Обработка карусели (entries) или одиночного медиа
            entries = info.get("entries")
            if entries:
                # Это карусель / playlist
                for entry in entries:
                    if entry is None:
                        continue
                    self._process_entry(entry)
            else:
                # Одиночное медиа
                self._process_entry(info)

            # Проверка наличия медиа
            if not self._data.videos and not self._data.images:
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context="Медиа не найдено в контенте",
                    code=InstagramErrorCode.NO_MEDIA_FOUND,
                )

            logger.info(
                f"Извлечено {len(self._data.videos)} видео и "
                f"{len(self._data.images)} изображений"
            )
            return InstagramResult(data=self._data)

        except DownloadError as e:
            error_str = str(e)
            logger.error(f"yt-dlp DownloadError: {error_str}")

            if "login" in error_str.lower() or "authentication" in error_str.lower():
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context=f"Требуется авторизация: {e}",
                    code=InstagramErrorCode.AUTHENTICATION_FAILED,
                )

            if "not found" in error_str.lower() or "404" in error_str:
                return InstagramResult(
                    status="error",
                    data=self._data,
                    context=f"Пост не найден: {e}",
                    code=InstagramErrorCode.POST_NOT_FOUND,
                )

            return InstagramResult(
                status="error",
                data=self._data,
                context=f"Ошибка извлечения: {e}",
                code=InstagramErrorCode.EXTRACTION_ERROR,
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

    def _process_entry(self, entry: dict) -> None:
        """Обрабатывает одну запись (видео или изображение) из yt-dlp."""
        entry_id = str(uuid4())
        width = entry.get("width")
        height = entry.get("height")

        # Определяем — видео или картинка
        video_url = entry.get("url", "")
        thumbnail = entry.get("thumbnail", "")
        ext = entry.get("ext", "")
        formats = entry.get("formats")

        is_video = (
            ext in ("mp4", "webm", "m4v")
            or entry.get("duration") is not None
            or (formats and any(
                f.get("vcodec", "none") != "none"
                for f in formats
            ))
        )

        if is_video:
            # Выбираем лучший видео URL
            best_url = video_url
            best_width = width
            best_height = height

            if formats:
                # Ищем лучший формат с видео
                video_formats = [
                    f for f in formats
                    if f.get("vcodec", "none") != "none"
                ]
                if video_formats:
                    # Сортируем по высоте (разрешению)
                    video_formats.sort(
                        key=lambda f: (f.get("height") or 0),
                        reverse=True,
                    )
                    best = video_formats[0]
                    best_url = best.get("url", best_url)
                    best_width = best.get("width", best_width)
                    best_height = best.get("height", best_height)

            self._data.is_video = True
            self._data.videos.append(
                InstagramVideo(
                    id=entry_id,
                    has_audio=True,
                    url=best_url,
                    width=best_width,
                    height=best_height,
                    name=entry.get("title", f"video_{entry_id}"),
                )
            )

            # Превью
            if thumbnail:
                self._data.thumbnails.append(
                    InstagramImage(
                        id=str(uuid4()),
                        url=thumbnail,
                        width=best_width,
                        height=best_height,
                        name=f"Thumbnail_{entry_id}",
                    )
                )
        else:
            # Изображение
            image_url = (
                video_url
                or thumbnail
                or entry.get("display_url", "")
            )
            if image_url:
                self._data.is_image = True
                self._data.images.append(
                    InstagramImage(
                        id=entry_id,
                        url=image_url,
                        width=width,
                        height=height,
                        name=entry.get("title", f"image_{entry_id}"),
                    )
                )

    def extract_info(self, url: str) -> InstagramResult:
        logger.info(f"Извлечение информации из URL: {url}")

        self._data = InstagramData(url=url)

        # Проверка URL
        if not url or not isinstance(url, str):
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context="Предоставлен неверный URL",
                code=InstagramErrorCode.EMPTY_URL,
            )
            return self._last_result

        if not self._validate_instagram_url(url):
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context="Неверный или неподдерживаемый URL Instagram",
                code=InstagramErrorCode.INVALID_URL,
            )
            return self._last_result

        # Классификация URL
        content_type = self._classify_url(url=url)

        if content_type in self.unsupported_types:
            self._last_result = InstagramResult(
                status="error",
                data=self._data,
                context=f"Неподдерживаемый тип контента: {content_type.value}",
                code=InstagramErrorCode.CONTENT_NOT_SUPPORTED,
            )
            return self._last_result

        # Извлечение информации
        self._last_result = self._extract_media_info(url=url)
        return self._last_result

    def get_error_description(self, code: InstagramErrorCode) -> str:
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
            InstagramErrorCode.GALLERY_DL_ERROR.value: "Произошла внутренняя ошибка",
        }
        return descriptions.get(code, "Неизвестная ошибка")