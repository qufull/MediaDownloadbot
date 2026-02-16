import logging
import re
from uuid import uuid4
from urllib.parse import urlparse, parse_qs
from typing import List, Optional, Dict, Any

import urllib.request
import urllib.error
import json

from ..abstractions import AbstractExtractor
from .enums import YoutubeErrorCode, ContentType
from .models import YoutubeData, YoutubeVideo, YoutubeImage, YoutubeAudio, YoutubeResult

logger = logging.getLogger("youtube")


class MassbotsApiError(Exception):
    """Ошибка при обращении к massbots.dl API."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Massbots API error {status_code}: {message}")


class YoutubeExtractor(AbstractExtractor):
    """
    YouTube extractor через massbots.dl API.

    Полностью заменяет yt-dlp для YouTube. Использует:
      - GET /video/{id}         — метаданные видео
      - GET /video/{id}/formats — доступные форматы для скачивания

    massbots.dl API возвращает Telegram file_id напрямую,
    поэтому файлы не нужно скачивать на диск.
    """

    BASE_URL = "https://api.massbots.xyz"

    def __init__(
        self,
        massbots_token: str,
        massbots_bot_id: Optional[str] = None,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
        request_timeout: float = 30.0,
    ) -> None:
        if not massbots_token:
            raise ValueError("massbots_token обязателен для YoutubeExtractor")

        self.massbots_token = massbots_token
        self.massbots_bot_id = massbots_bot_id
        self.proxy = proxy
        self.request_timeout = request_timeout

        # Cookie и proxy больше не нужны для YouTube (API берёт это на себя),
        # но оставляем для совместимости интерфейса
        self.cookies_path = cookie_path

        self.unsupported_types: List[ContentType] = [
            ContentType.POST,
            ContentType.LIVE,
            ContentType.ACCOUNT,
            ContentType.PLAYLIST,
        ]

        self._data: Optional[YoutubeData] = None
        self._last_result: Optional[YoutubeResult] = None

        logger.info(
            "YoutubeExtractor (massbots API) ready. bot_id=%s",
            self.massbots_bot_id or "default",
        )

    # ─── HTTP helpers ───────────────────────────────────────────────

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "X-Token": self.massbots_token,
            "Accept": "application/json",
            "User-Agent": "MediaDownloadBot/1.0",
        }
        if self.massbots_bot_id:
            headers["X-Bot-Id"] = self.massbots_bot_id
        return headers

    def _api_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        """GET-запрос к massbots.dl API. Возвращает распарсённый JSON."""
        url = f"{self.BASE_URL}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items() if v)
            if query:
                url = f"{url}?{query}"

        req = urllib.request.Request(url, headers=self._build_headers(), method="GET")

        if self.proxy:
            proxy_handler = urllib.request.ProxyHandler({
                "http": self.proxy,
                "https": self.proxy,
            })
            opener = urllib.request.build_opener(proxy_handler)
        else:
            opener = urllib.request.build_opener()

        try:
            with opener.open(req, timeout=self.request_timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            logger.error("Massbots API HTTP %s: %s — %s", e.code, path, body[:300])
            raise MassbotsApiError(e.code, body)
        except urllib.error.URLError as e:
            logger.error("Massbots API URL error: %s — %s", path, e)
            raise MassbotsApiError(0, str(e))

    # ─── URL utilities ──────────────────────────────────────────────

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """Извлекает video_id из различных форматов YouTube URL."""
        if not url:
            return None

        # youtu.be/VIDEO_ID
        match = re.match(r"https?://youtu\.be/([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)

        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()

        if "youtube.com" not in host and "youtu.be" not in host:
            return None

        path = parsed.path or ""

        # /shorts/VIDEO_ID
        match = re.match(r"/shorts/([a-zA-Z0-9_-]{11})", path)
        if match:
            return match.group(1)

        # /embed/VIDEO_ID или /v/VIDEO_ID
        match = re.match(r"/(embed|v)/([a-zA-Z0-9_-]{11})", path)
        if match:
            return match.group(2)

        # ?v=VIDEO_ID
        qs = parse_qs(parsed.query)
        v = qs.get("v")
        if v and len(v[0]) == 11:
            return v[0]

        return None

    def _classify_url(self, url: str) -> Optional[ContentType]:
        try:
            parsed = urlparse(url)
            path = (parsed.path or "").lower()

            if "/shorts/" in path:
                return ContentType.SHORTS
            if "/playlist" in path:
                return ContentType.PLAYLIST
            if "/live" in path:
                return ContentType.LIVE
            if "/post" in path:
                return ContentType.POST

            parts = path.strip("/").split("/")
            if parts and parts[-1].startswith("@"):
                return ContentType.ACCOUNT

            return ContentType.VIDEO
        except Exception as e:
            logger.error("URL classify error: %s", e)
            return None

    def _validate_youtube_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
            return any(d in host for d in ("youtube.com", "youtu.be"))
        except Exception:
            return False

    # ─── Error builder ──────────────────────────────────────────────

    def _build_error(self, code: YoutubeErrorCode, ctx: str) -> YoutubeResult:
        return YoutubeResult(status="error", data=self._data, context=ctx, code=code)

    # ─── Main extraction ────────────────────────────────────────────

    def extract_info(self, url: str) -> YoutubeResult:
        """
        Извлекает информацию о YouTube видео через massbots.dl API.

        Выполняет два запроса:
        1. GET /video/{id} — метаданные (title, author, thumbnails)
        2. GET /video/{id}/formats — доступные форматы для скачивания
        """
        logger.info("YouTube extract_info (massbots): %s", url)
        self._data = YoutubeData(url=url)

        # --- валидация URL ---
        if not url or not isinstance(url, str):
            self._last_result = self._build_error(
                YoutubeErrorCode.EMPTY_URL, "Предоставлен неверный URL"
            )
            return self._last_result

        if not self._validate_youtube_url(url):
            self._last_result = self._build_error(
                YoutubeErrorCode.INVALID_URL, "Неверный или неподдерживаемый URL YouTube"
            )
            return self._last_result

        content_type = self._classify_url(url)
        if not content_type:
            self._last_result = self._build_error(
                YoutubeErrorCode.UNSUPPORTED_CONTENT_TYPE,
                "Не удалось классифицировать тип контента URL",
            )
            return self._last_result

        if content_type in self.unsupported_types:
            self._last_result = self._build_error(
                YoutubeErrorCode.UNSUPPORTED_CONTENT_TYPE,
                f"Неподдерживаемый тип контента: {content_type.value}",
            )
            return self._last_result

        # --- извлечение video_id ---
        video_id = self._extract_video_id(url)
        if not video_id:
            self._last_result = self._build_error(
                YoutubeErrorCode.INVALID_URL,
                "Не удалось извлечь ID видео из URL",
            )
            return self._last_result

        # --- запрос метаданных через API ---
        try:
            video_data = self._api_get(f"/video/{video_id}")
        except MassbotsApiError as e:
            if e.status_code == 404:
                self._last_result = self._build_error(
                    YoutubeErrorCode.EXTRACTOR_ERROR,
                    "Видео не найдено или недоступно",
                )
            elif e.status_code == 500:
                self._last_result = self._build_error(
                    YoutubeErrorCode.EXTRACTOR_ERROR,
                    f"Ошибка сервера massbots API: {e.message[:200]}",
                )
            else:
                self._last_result = self._build_error(
                    YoutubeErrorCode.CONNECTION_ERROR,
                    f"Ошибка API ({e.status_code}): {e.message[:200]}",
                )
            return self._last_result
        except Exception as e:
            self._last_result = self._build_error(
                YoutubeErrorCode.UNEXPECTED_ERROR,
                f"Неожиданная ошибка при получении метаданных: {e}",
            )
            return self._last_result

        # --- заполняем метаданные ---
        self._data.is_video = True
        self._data.title = video_data.get("title")
        self._data.author_name = video_data.get("channel_title")
        self._data.description = video_data.get("description")

        # --- thumbnails ---
        thumb_count = 0
        thumbnails = video_data.get("thumbnails") or {}
        thumb_order = ["default", "medium", "high", "standard", "maxres"]
        for key in thumb_order:
            t = thumbnails.get(key)
            if t and t.get("url"):
                self._data.thumbnails.append(
                    YoutubeImage(
                        id=str(uuid4()),
                        url=t["url"],
                        name=key,
                        width=t.get("width"),
                        height=t.get("height"),
                    )
                )
                thumb_count += 1

        # --- запрос форматов ---
        try:
            formats_data = self._api_get(f"/video/{video_id}/formats")
        except MassbotsApiError as e:
            if e.status_code == 404:
                self._last_result = self._build_error(
                    YoutubeErrorCode.NO_MEDIA_FORMATS_FOUND,
                    "Форматы для видео не найдены",
                )
            else:
                self._last_result = self._build_error(
                    YoutubeErrorCode.EXTRACTOR_ERROR,
                    f"Ошибка получения форматов: {e.message[:200]}",
                )
            return self._last_result
        except Exception as e:
            self._last_result = self._build_error(
                YoutubeErrorCode.UNEXPECTED_ERROR,
                f"Неожиданная ошибка при получении форматов: {e}",
            )
            return self._last_result

        # --- парсим форматы ---
        # formats_data — dict вида:
        # {"240p": {"format": "240p", "cached": false, "file_size": 12345, "voices": {...}},
        #  "720p": {"format": "720p", "cached": true}, ...}

        video_count = 0
        if isinstance(formats_data, dict):
            for format_key, fmt in formats_data.items():
                if not isinstance(fmt, dict):
                    continue

                format_str = fmt.get("format", format_key)  # e.g. "720p"
                file_size = fmt.get("file_size")

                height = self._parse_height(format_str)
                if not height:
                    continue

                # 144p часто возвращает 500, пропускаем
                if height < 240:
                    logger.debug("Skipping low format: %s", format_str)
                    continue

                # Ширина неизвестна до скачивания (Shorts = 9:16, обычное = 16:9)
                # Реальные размеры определяются через ffprobe после скачивания
                width = None

                self._data.videos.append(
                    YoutubeVideo(
                        id=str(uuid4()),
                        url=url,
                        name=format_str,  # "720p" — используется как format для download API
                        has_audio=True,   # massbots отдаёт видео со звуком
                        fps=None,
                        width=width,
                        height=height,
                        language=None,
                        total_bitrate=file_size or 0,
                        language_preference=0,
                    )
                )
                video_count += 1

        # --- аудио: bestaudio заглушка для кнопки 🎵 Audio ---
        self._data.audios.append(
            YoutubeAudio(
                id=str(uuid4()),
                url=url,
                name="bestaudio",
                language=None,
                language_preference=0,
            )
        )

        if video_count == 0:
            self._last_result = self._build_error(
                YoutubeErrorCode.NO_MEDIA_FORMATS_FOUND,
                "Не найдено поддерживаемых видео форматов.",
            )
            return self._last_result

        logger.info(
            "Extracted (massbots): videos=%s audios=%s thumbs=%s",
            video_count, len(self._data.audios), thumb_count,
        )
        self._last_result = YoutubeResult(data=self._data)
        return self._last_result

    # ─── Format helpers ─────────────────────────────────────────────

    @staticmethod
    def _parse_height(format_str: str) -> Optional[int]:
        """Парсит высоту из строки формата: '720p' -> 720, '1080p' -> 1080."""
        match = re.match(r"^(\d+)p$", format_str.strip())
        if match:
            return int(match.group(1))
        return None

    # ─── API Download helpers (используются из Downloader) ──────────

    def get_formats(self, video_id: str) -> Dict[str, Any]:
        """Получает доступные форматы для video_id."""
        return self._api_get(f"/video/{video_id}/formats")

    def start_download(
        self, video_id: str, format_str: str, lang: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Инициирует скачивание или проверяет статус.

        Returns:
            {"status": "queued|downloading|ready|failed", "file_id": "..."}
        """
        params = {}
        if lang:
            params["lang"] = lang
        return self._api_get(f"/video/{video_id}/download/{format_str}", params)

    # ─── Error description ──────────────────────────────────────────

    def get_error_description(self, code: YoutubeErrorCode) -> str:
        descriptions = {
            YoutubeErrorCode.SUCCESS.value: "Операция успешно завершена",
            YoutubeErrorCode.INVALID_URL.value: "Предоставленный URL YouTube неверен или не поддерживается",
            YoutubeErrorCode.EMPTY_URL.value: "Предоставлен пустой или неверный URL",
            YoutubeErrorCode.UNSUPPORTED_CONTENT_TYPE.value: "Тип контента YouTube не поддерживается",
            YoutubeErrorCode.UNSUPPORTED_MEDIA_TYPE.value: "Тип медиа не поддерживается",
            YoutubeErrorCode.CONNECTION_ERROR.value: "Произошла ошибка сетевого соединения",
            YoutubeErrorCode.DOWNLOAD_ERROR.value: "Не удалось загрузить медиа",
            YoutubeErrorCode.EXTRACTOR_ERROR.value: "Не удалось извлечь медиа",
            YoutubeErrorCode.PROXY_ERROR.value: "Ошибка подключения к прокси",
            YoutubeErrorCode.LIVE_STREAM_NOT_SUPPORTED.value: "Прямые трансляции не поддерживаются",
            YoutubeErrorCode.PLAYLIST_NOT_SUPPORTED.value: "Плейлисты не поддерживаются",
            YoutubeErrorCode.ACCOUNT_NOT_SUPPORTED.value: "Контент аккаунта/канала не поддерживается",
            YoutubeErrorCode.SHORTS_NOT_SUPPORTED.value: "YouTube Shorts не поддерживаются",
            YoutubeErrorCode.POST_NOT_SUPPORTED.value: "Сообщества не поддерживаются",
            YoutubeErrorCode.NO_VIDEO_FORMATS_FOUND.value: "Поддерживаемые видео форматы не найдены",
            YoutubeErrorCode.NO_AUDIO_FORMATS_FOUND.value: "Поддерживаемые аудио форматы не найдены",
            YoutubeErrorCode.NO_THUMBNAILS_FOUND.value: "Миниатюры не найдены",
            YoutubeErrorCode.NO_MEDIA_FORMATS_FOUND.value: "Поддерживаемые медиа форматы не найдены",
            YoutubeErrorCode.COOKIE_FILE_NOT_FOUND.value: "Файл cookie не найден",
            YoutubeErrorCode.OUTPUT_PATH_ERROR.value: "Ошибка выходного пути",
            YoutubeErrorCode.FILE_WRITE_ERROR.value: "Ошибка записи файла",
            YoutubeErrorCode.UNEXPECTED_ERROR.value: "Произошла непредвиденная ошибка",
            YoutubeErrorCode.INITIALIZATION_ERROR.value: "Не удалось инициализировать загрузчик",
            YoutubeErrorCode.EXTRACT_INFO_NOT_CALLED.value: "extract_info() должен быть вызван перед загрузкой",
            YoutubeErrorCode.YT_DLP_ERROR.value: "Произошла внутренняя ошибка API",
            YoutubeErrorCode.API_ERROR.value: "Ошибка massbots.dl API",
            YoutubeErrorCode.API_TIMEOUT.value: "Превышено время ожидания ответа API",
            YoutubeErrorCode.DOWNLOAD_POLLING_TIMEOUT.value: "Превышено время ожидания скачивания",
            YoutubeErrorCode.DOWNLOAD_FAILED.value: "Скачивание завершилось с ошибкой на стороне API",
        }
        return descriptions.get(code, "Неизвестная ошибка")