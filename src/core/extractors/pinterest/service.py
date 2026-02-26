import logging
from uuid import uuid4
from typing import List, Optional
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..abstractions import AbstractExtractor
from .enums import PinterestErrorCode, PinterestContentType as ContentType
from .models import (
    PinterestData,
    PinterestImage,
    PinterestVideo,
    PinterestResult,
)

logger = logging.getLogger("pinterest")


class PinterestExtractor(AbstractExtractor):
    """
    Загрузчик медиа из Pinterest через yt-dlp.

    Поддерживает:
    - Отдельные пины (изображения)
    - Видео-пины
    - Короткие ссылки (pin.it)
    - Стандартные ссылки (pinterest.com)
    """

    def __init__(
            self,
            proxy: Optional[str] = None,
            cookie_path: Optional[str] = None,
    ) -> None:
        logger.info("Инициализация экстрактора Pinterest (yt-dlp)")

        self.proxy = proxy
        self.cookie_path = cookie_path

        self.unsupported_types: List[ContentType] = [
            ContentType.UNKNOWN,
        ]

        self._data: Optional[PinterestData] = None
        self._last_result: Optional[PinterestResult] = None

        # Базовые опции yt-dlp
        self._base_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,  # По умолчанию не качаем доски целиком
            "extract_flat": False,
            "skip_download": True,  # Только извлечение метаданных
        }

        if self.proxy:
            self._base_opts["proxy"] = self.proxy

        # Куки опциональны
        if self.cookie_path:
            from pathlib import Path
            if Path(self.cookie_path).exists():
                self._base_opts["cookiefile"] = self.cookie_path
                logger.info(f"Cookies подключены: {self.cookie_path}")
            else:
                logger.warning(f"Cookie файл не найден: {self.cookie_path}, работаем без cookies")

        logger.debug("Экстрактор Pinterest успешно инициализирован")

    def _validate_pinterest_url(self, url: str) -> bool:
        try:
            parsed_url = urlparse(url=url)
            netloc = parsed_url.netloc.lower()
            # Проверяем как полную, так и короткую ссылку
            return "pinterest." in netloc or netloc.endswith("pin.it")
        except Exception as e:
            logger.debug(f"Ошибка при проверке URL: {e}")
            return False

    def _classify_url(self, url: str) -> ContentType:
        logger.debug(f"Классификация URL: {url}")
        try:
            parsed = urlparse(url)
            path = parsed.path.lower()

            if "pin/" in path or parsed.netloc.endswith("pin.it"):
                return ContentType.PIN
            elif "board/" in path:
                return ContentType.BOARD
            elif "story/" in path:
                return ContentType.STORY
            else:
                return ContentType.POST  # Запасной вариант для обратной совместимости

        except Exception as e:
            logger.error(f"Ошибка классификации URL Pinterest: {e}")
            return ContentType.UNKNOWN

    def _extract_media_info(self, url: str) -> PinterestResult:
        info = None
        try:
            opts = dict(self._base_opts)
            opts.update({"ignoreerrors": True, "no_warnings": True})

            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

        except Exception as e:
            logger.warning("yt-dlp primary extract failed: %s", e)

        # ── Fallback: повторная попытка с extract_flat (для фото-пинов) ──
        if not info:
            try:
                flat_opts = dict(self._base_opts)
                flat_opts.update({
                    "ignoreerrors": True,
                    "extract_flat": True,  # ← не ищем форматы вообще
                    "skip_download": True,
                })
                with YoutubeDL(flat_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.warning("yt-dlp flat extract also failed: %s", e)

        if not info:
            return PinterestResult(
                status="error",
                data=self._data,
                context="Pinterest не отдал данные (возможно, приватный пин)",
                code=PinterestErrorCode.NO_CONTENT_FOUND,
            )

        self._data.author_name = info.get("uploader") or info.get("creator") or "Unknown"
        self._data.title = info.get("title") or info.get("description", "")[:50] or "Pinterest"

        if "entries" in info:
            for entry in info["entries"]:
                if entry:
                    self._process_entry(entry)
        else:
            self._process_entry(info)

        # Финальный фолбэк: если _process_entry тоже ничего не нашла
        if not self._data.videos and not self._data.images:
            fallback_url = info.get("url") or info.get("thumbnail")
            if fallback_url:
                self._data.is_image = True
                self._data.images.append(
                    PinterestImage(
                        id=str(uuid4())[:8],
                        url=fallback_url,
                        width=info.get("width"),
                        height=info.get("height"),
                        name=self._data.title,
                    )
                )

        if not self._data.videos and not self._data.images:
            return PinterestResult(
                status="error",
                data=self._data,
                context="Не найдено ни видео, ни фото в пине",
                code=PinterestErrorCode.NO_MEDIA_FOUND,
            )

        return PinterestResult(data=self._data)

    def _process_entry(self, entry: dict) -> None:
        """Обрабатывает запись Pinterest, принудительно устанавливая формат 'best'."""
        # Для Pinterest почти всегда лучше использовать 'best'
        # так как yt-dlp не всегда отдает стабильные format_id
        video_format_id = "best"

        # Генерируем уникальный ID только для внутреннего маппинга в кнопках бота
        # Но yt-dlp мы будем просить скачать 'best'
        internal_id = str(uuid4())[:8]

        width = entry.get("width")
        height = entry.get("height")
        video_url = entry.get("url", "")
        thumbnail = entry.get("thumbnail", "")
        ext = entry.get("ext", "")
        formats = entry.get("formats")

        is_video = (
                ext in ("mp4", "webm", "mov", "m4v")
                or entry.get("duration") is not None
                or (formats and any(f.get("vcodec", "none") != "none" for f in formats))
        )

        if is_video:
            # Для Pinterest мы просто помечаем качество как "Best" или берем высоту из метаданных
            best_height = height
            if not best_height and formats:
                video_formats = [f for f in formats if f.get("vcodec", "none") != "none"]
                if video_formats:
                    video_formats.sort(key=lambda f: (f.get("height") or 0), reverse=True)
                    best_height = video_formats[0].get("height")

            self._data.is_video = True
            self._data.videos.append(
                PinterestVideo(
                    id=video_format_id,  # <--- СЮДА ПИШЕМ 'best'
                    has_audio=True,
                    url=video_url,
                    width=width,
                    height=best_height,
                    name=f"🎬 {best_height}p" if best_height else "🎬 Best Quality",
                )
            )

            if thumbnail:
                self._data.thumbnails.append(
                    PinterestImage(
                        id=str(uuid4()),
                        url=thumbnail,
                        width=width,
                        height=height,
                        name=f"Thumbnail_{internal_id}",
                    )
                )
        else:
            image_url = (
                    entry.get("thumbnail")  # ← поставьте thumbnail первым
                    or video_url
                    or entry.get("display_url", "")
            )
            if image_url:
                self._data.is_image = True
                self._data.images.append(
                    PinterestImage(
                        id=image_url,
                        url=image_url,
                        width=width,
                        height=height,
                        name=entry.get("title", f"pin_image_{internal_id}"),
                    )
                )

    def extract_info(self, url: str) -> PinterestResult:
        logger.info(f"Извлечение информации из URL Pinterest: {url}")

        self._data = PinterestData(url=url)

        if not url or not isinstance(url, str):
            self._last_result = PinterestResult(
                status="error",
                data=self._data,
                context="Предоставлен пустой или неверный URL",
                code=PinterestErrorCode.EMPTY_URL,
            )
            return self._last_result

        if not self._validate_pinterest_url(url):
            self._last_result = PinterestResult(
                status="error",
                data=self._data,
                context="Неверный или неподдерживаемый URL Pinterest",
                code=PinterestErrorCode.INVALID_URL,
            )
            return self._last_result

        content_type = self._classify_url(url=url)

        if content_type in self.unsupported_types:
            self._last_result = PinterestResult(
                status="error",
                data=self._data,
                context=f"Неподдерживаемый тип контента: {content_type.value}",
                code=PinterestErrorCode.CONTENT_NOT_SUPPORTED,
            )
            return self._last_result

        # Вызов основной логики
        self._last_result = self._extract_media_info(url=url)
        return self._last_result

    def get_error_description(self, code: PinterestErrorCode) -> str:
        descriptions = {
            PinterestErrorCode.SUCCESS.value: "Операция успешно завершена",
            PinterestErrorCode.INVALID_URL.value: "Неверный URL Pinterest",
            PinterestErrorCode.EMPTY_URL.value: "Предоставлен пустой URL",
            PinterestErrorCode.AUTHENTICATION_FAILED.value: "Ошибка авторизации в Pinterest",
            PinterestErrorCode.POST_NOT_FOUND.value: "Пин не найден (404)",
            PinterestErrorCode.CONTENT_NOT_SUPPORTED.value: "Этот тип контента не поддерживается",
            PinterestErrorCode.EXTRACTION_ERROR.value: "Не удалось извлечь медиа-данные",
            PinterestErrorCode.NO_CONTENT_FOUND.value: "Контент не найден по этой ссылке",
            PinterestErrorCode.NO_MEDIA_FOUND.value: "В этом пине нет доступных видео или фото",
            PinterestErrorCode.DOWNLOAD_ERROR.value: "Ошибка при загрузке файла",
            PinterestErrorCode.UNEXPECTED_ERROR.value: "Произошла непредвиденная ошибка",
        }
        # Если пришел объект Enum, берем его value, если строка — используем как есть
        code_val = code.value if hasattr(code, 'value') else code
        return descriptions.get(code_val, f"Неизвестная ошибка ({code_val})")