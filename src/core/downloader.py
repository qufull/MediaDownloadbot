import hashlib
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Union, Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .extractors.abstractions import (
    AbstractDataModel,
    AbstractResultModel,
    AbstractErrorCodeModel,
)
from .extractors.youtube.service import YoutubeExtractor, MassbotsApiError

logger = logging.getLogger("downloader")


# ──────────────────────────────────────────────────────────────────────
# YouTube-specific result model: содержит file_id вместо path
# ──────────────────────────────────────────────────────────────────────

class YoutubeDownloadResult:
    """
    Результат скачивания YouTube через massbots.dl API.

    В отличие от обычного AbstractResultModel, содержит file_id
    (Telegram file identifier), который можно сразу отправить
    через bot.send_video(video=file_id).
    """

    def __init__(
        self,
        status: str = "success",
        file_id: Optional[str] = None,
        context: Optional[str] = None,
        url: str = "",
    ):
        self.status = status
        self.file_id = file_id
        self.context = context
        self.url = url

    @property
    def is_massbots(self) -> bool:
        """Маркер для воркеров: результат из massbots API."""
        return True


# ──────────────────────────────────────────────────────────────────────

def _assert_nonempty_file(path: Path) -> None:
    """Проверяет, что файл реально создан и не пустой."""
    if not path.exists():
        raise FileNotFoundError(f"Файл не создан: {path}")
    if path.stat().st_size <= 0:
        raise IOError(f"Файл пустой (0 байт): {path}")


class Downloader:
    """
    Downloader с двойным бэкендом:
    - YouTube → massbots.dl API (возвращает file_id, без скачивания на диск)
    - Остальные сервисы → yt-dlp (как было)
    """

    def __init__(
        self,
        retries_count: int = 10,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
        rutube_proxy: Optional[str] = None,
        concurrent_download_count: int = 2,
        output_path: Union[str, Path] = "./downloads/",
        youtube_extractor: Optional[YoutubeExtractor] = None,
        youtube_poll_interval: float = 3.0,
        youtube_poll_timeout: float = 300.0,
        bot_token: Optional[str] = None,
        massbots_token: Optional[str] = None,
        massbots_bot_id: Optional[str] = None,
    ) -> None:
        self.proxy = proxy
        self.rutube_proxy = rutube_proxy
        self.cookies_path = Path(cookie_path) if cookie_path else None
        self.output_path = Path(output_path) if isinstance(output_path, str) else output_path
        self.output_path.mkdir(exist_ok=True, parents=True)

        # massbots API для YouTube
        self.youtube_extractor = youtube_extractor
        self.youtube_poll_interval = youtube_poll_interval
        self.youtube_poll_timeout = youtube_poll_timeout
        # Токен бота для скачивания файлов через Telegram API (getFile)
        self.bot_token = bot_token

        # massbots SDK для download (гарантирует правильный X-Bot-Id)
        self._massbots_api = None
        if massbots_token:
            try:
                import massbots
                self._massbots_api = massbots.Api(
                    token=massbots_token,
                    bot_id=massbots_bot_id,
                )
                logger.info("massbots SDK initialized: bot_id=%s", massbots_bot_id or "default")
            except Exception as e:
                logger.error("Failed to init massbots SDK: %s", e)

        if self.cookies_path and not self.cookies_path.exists():
            logger.warning("Cookies file not found: %s (ignore)", self.cookies_path)
            self.cookies_path = None

        # Базовые опции yt-dlp (для НЕ-YouTube сервисов)
        self.base_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "playlistend": 1,
            "retries": retries_count,
            "fragment_retries": retries_count,
            "concurrent_fragment_downloads": concurrent_download_count,
            "proxy": self.proxy,
            # cookiefile НЕ в base_opts — передаётся per-service через _yt_opts_for_service
        }

        logger.info(
            "Downloader init. cookies=%s proxy=%s rutube_proxy=%s out=%s youtube_api=%s",
            str(self.cookies_path) if self.cookies_path else "disabled",
            self.proxy,
            self.rutube_proxy,
            self.output_path,
            "massbots" if self.youtube_extractor else "disabled",
        )

    # ─── helpers ────────────────────────────────────────────────────

    # Стандартный порядок форматов от лучшего к худшему
    _FORMAT_PRIORITY = ["1080p", "720p", "480p", "360p", "240p"]

    def _generate_safe_filename(self, url: str, format_id: str) -> str:
        h = hashlib.sha256(f"{url}_{format_id}".encode()).hexdigest()[:16]
        return h

    @staticmethod
    def _build_fallback_formats(requested: str) -> List[str]:
        """
        Строит список fallback форматов для retry.
        Если запрошен 720p — fallback: 480p, 360p, 1080p.
        Если запрошен 144p — fallback: 240p, 360p, 480p.
        """
        priority = ["1080p", "720p", "480p", "360p", "240p"]
        clean = requested.split(":")[0] if ":" in requested else requested
        fallbacks = [f for f in priority if f != clean]
        return fallbacks

    def _download_telegram_file(self, file_id: str, save_as: Path) -> bool:
        """
        Скачивает файл из Telegram по file_id через официальный Bot API.

        1. GET https://api.telegram.org/bot{token}/getFile?file_id=...
        2. GET https://api.telegram.org/file/bot{token}/{file_path}
        3. Сохраняет на диск

        Returns:
            True если файл скачан, False при ошибке
        """
        if not self.bot_token:
            logger.error("bot_token не задан — невозможно скачать файл по file_id")
            return False

        try:
            # 1. getFile
            get_file_url = f"https://api.telegram.org/bot{self.bot_token}/getFile?file_id={file_id}"
            logger.info("getFile request: file_id=%s...", file_id[:30] if file_id else "None")
            req = urllib.request.Request(get_file_url)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                body = he.read().decode("utf-8", errors="replace")
                logger.error("getFile HTTP %s: %s", he.code, body)
                return False

            if not data.get("ok"):
                logger.error("getFile failed: %s", data)
                return False

            file_path = data["result"]["file_path"]
            file_size = data["result"].get("file_size", 0)
            logger.info("getFile OK: file_path=%s size=%s", file_path, file_size)

            # 2. Скачиваем файл
            download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            req2 = urllib.request.Request(download_url)
            with urllib.request.urlopen(req2, timeout=300) as resp2:
                save_as.parent.mkdir(parents=True, exist_ok=True)
                with open(save_as, "wb") as f:
                    while True:
                        chunk = resp2.read(1024 * 1024)  # 1MB chunks
                        if not chunk:
                            break
                        f.write(chunk)

            if save_as.exists() and save_as.stat().st_size > 0:
                logger.info("Telegram file downloaded: %s (%s bytes)", save_as, save_as.stat().st_size)
                return True
            else:
                logger.error("Downloaded file is empty: %s", save_as)
                return False

        except Exception as e:
            logger.exception("Failed to download Telegram file by file_id: %s", e)
            return False

    def _yt_opts_for_service(self, service: Optional[str]) -> Dict[str, Any]:
        """
        Базовые опции + service-specific патчи.

        Куки передаются только сервисам, которым они нужны при download:
        - tiktok, twitter: требуют авторизации для скачивания
        - rutube: использует отдельный proxy
        - reddit: обычно не требует кук для скачивания
        """
        opts = dict(self.base_opts)

        if service == "rutube":
            if self.rutube_proxy:
                opts["proxy"] = self.rutube_proxy

        # Куки только для сервисов, которые требуют авторизации при download
        if service in ("tiktok", "twitter") and self.cookies_path:
            opts["cookiefile"] = str(self.cookies_path)

        return opts

    # ─── YouTube через massbots SDK ────────────────────────────────

    def _youtube_download_via_sdk(
        self,
        url: str,
        format_str: str,
        fallback_formats: Optional[List[str]] = None,
    ) -> YoutubeDownloadResult:
        """
        Скачивает YouTube видео через massbots SDK.

        SDK сам обрабатывает headers (X-Token, X-Bot-Id) и поллинг.
        При ошибке пробует fallback форматы.
        """
        if not self._massbots_api:
            return YoutubeDownloadResult(
                status="error",
                context="massbots SDK не инициализирован",
                url=url,
            )

        video_id = YoutubeExtractor._extract_video_id(url)
        if not video_id:
            return YoutubeDownloadResult(
                status="error",
                context="Не удалось извлечь video_id из URL",
                url=url,
            )

        # Собираем очередь форматов: основной + fallback
        formats_to_try = [format_str]
        if fallback_formats:
            for fb in fallback_formats:
                if fb != format_str and fb not in formats_to_try:
                    formats_to_try.append(fb)

        last_error = ""

        for current_format in formats_to_try:
            # Парсим format_str — может быть "720p" или "720p:ru"
            actual_format = current_format
            if ":" in current_format:
                actual_format = current_format.split(":", 1)[0]

            logger.info(
                "YouTube download via massbots SDK: video_id=%s format=%s",
                video_id, actual_format,
            )

            try:
                download_result = self._massbots_api.download(video_id, actual_format)
                result = download_result.wait_until_ready(delay=self.youtube_poll_interval)

                if result.status == "ready" and result.file_id:
                    logger.info(
                        "YouTube download ready (SDK): video_id=%s format=%s file_id=%s...",
                        video_id, actual_format, result.file_id[:30],
                    )
                    return YoutubeDownloadResult(
                        status="success",
                        file_id=result.file_id,
                        url=url,
                    )

                last_error = f"status={result.status} for {actual_format}"
                logger.warning("YouTube download %s: %s. Trying next...", video_id, last_error)

            except Exception as e:
                last_error = str(e)[:200]
                logger.warning(
                    "YouTube download error for %s format=%s: %s. Trying next...",
                    video_id, actual_format, last_error,
                )

        return YoutubeDownloadResult(
            status="error",
            context=f"Все форматы не удались. Последняя ошибка: {last_error}",
            url=url,
        )

    # ─── download_video ─────────────────────────────────────────────

    def download_video(
        self,
        url: str,
        video_format_id: str,
        merge_audio: bool = False,
        service: str = None,
    ) -> Union[AbstractResultModel, YoutubeDownloadResult]:
        """
        Скачивает видео.

        Для YouTube — через massbots SDK (возвращает YoutubeDownloadResult с file_id).
        Для остальных — через yt-dlp (возвращает AbstractResultModel с path).
        """
        logger.info(
            "download_video: url=%s fmt=%s service=%s merge_audio=%s",
            url, video_format_id, service, merge_audio,
        )

        # ── YouTube → massbots SDK ──
        if service == "youtube" and self._massbots_api:
            # Fallback: если запрошенный формат не сработал, пробуем ближайшие
            fallbacks = self._build_fallback_formats(video_format_id)
            yt_result = self._youtube_download_via_sdk(
                url=url,
                format_str=video_format_id,
                fallback_formats=fallbacks,
            )

            if yt_result.status != "success" or not yt_result.file_id:
                return AbstractResultModel(
                    status="error",
                    context=yt_result.context or "massbots API error",
                    code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                    data=AbstractDataModel(url=url),
                )

            # Возвращаем YoutubeDownloadResult с file_id
            # Воркер отправит через api.telegram.org напрямую
            return yt_result

        # ── Остальные сервисы → yt-dlp ──
        safe = self._generate_safe_filename(url, video_format_id)
        file_path = self.output_path / f"{safe}.mp4"

        if service == "reddit":
            base = video_format_id or "bestvideo[ext=mp4]/best"
            format_str = base
        else:
            format_str = video_format_id

        if merge_audio:
            format_str = f"{format_str}+bestaudio/best"

        opts = self._yt_opts_for_service(service)
        opts.update(
            {
                "outtmpl": str(file_path),
                "format": format_str,
                "merge_output_format": "mp4",
            }
        )

        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

            _assert_nonempty_file(file_path)

            return AbstractResultModel(
                data=AbstractDataModel(url=url, path=str(file_path), is_video=True)
            )

        except DownloadError as e:
            return AbstractResultModel(
                status="error",
                context=f"Ошибка загрузки видео: {e}",
                code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                data=AbstractDataModel(url=url),
            )

        except Exception as e:
            return AbstractResultModel(
                status="error",
                context=f"Неожиданная ошибка загрузки видео: {e}",
                code=AbstractErrorCodeModel.UNEXPECTED_ERROR,
                data=AbstractDataModel(url=url),
            )

    # ─── download_audio ─────────────────────────────────────────────

    def download_audio(
        self,
        url: str,
        audio_format_id: str,
        service: str = None,
    ) -> Union[AbstractResultModel, YoutubeDownloadResult]:
        """
        Скачивает аудио.

        Для YouTube — через massbots SDK (возвращает YoutubeDownloadResult с file_id).
        Для остальных — через yt-dlp.
        """
        logger.info("download_audio: url=%s fmt=%s service=%s", url, audio_format_id, service)

        # ── YouTube → massbots SDK (берём наименьший доступный формат ≥240p) ──
        if service == "youtube" and self._massbots_api:
            video_id = YoutubeExtractor._extract_video_id(url)
            best_format = "360p"
            fallbacks = ["480p", "720p", "240p"]

            if video_id:
                try:
                    formats = self.youtube_extractor.get_formats(video_id)
                    available = []
                    for fmt_key, fmt_val in formats.items():
                        if isinstance(fmt_val, dict):
                            fmt_str = fmt_val.get("format", fmt_key)
                            h = YoutubeExtractor._parse_height(fmt_str)
                            if h and h >= 240:
                                available.append((h, fmt_str))
                    available.sort(key=lambda x: x[0])

                    if available:
                        best_format = available[0][1]
                        fallbacks = [f for _, f in available[1:]]
                except Exception as e:
                    logger.warning("YouTube audio format lookup failed: %s", e)

            yt_result = self._youtube_download_via_sdk(
                url=url,
                format_str=best_format,
                fallback_formats=fallbacks,
            )

            if yt_result.status != "success" or not yt_result.file_id:
                return AbstractResultModel(
                    status="error",
                    context=yt_result.context or "massbots API error",
                    code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                    data=AbstractDataModel(url=url),
                )

            # Возвращаем YoutubeDownloadResult с file_id
            return yt_result

        # ── Остальные сервисы → yt-dlp ──
        safe = self._generate_safe_filename(url, audio_format_id)
        mp3_path = self.output_path / f"{safe}.mp3"

        if audio_format_id in ("bestaudio", "music", "", None):
            format_str = "bestaudio/best"
        else:
            format_str = str(audio_format_id)

        opts = self._yt_opts_for_service(service)
        opts.update(
            {
                "outtmpl": str(self.output_path / f"{safe}.%(ext)s"),
                "format": format_str,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        )

        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

            _assert_nonempty_file(mp3_path)

            return AbstractResultModel(
                data=AbstractDataModel(url=url, path=str(mp3_path), is_video=False)
            )

        except DownloadError as e:
            return AbstractResultModel(
                status="error",
                context=f"Ошибка загрузки аудио: {e}",
                code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                data=AbstractDataModel(url=url),
            )

        except Exception as e:
            return AbstractResultModel(
                status="error",
                context=f"Неожиданная ошибка при загрузке аудио: {e}",
                code=AbstractErrorCodeModel.UNEXPECTED_ERROR,
                data=AbstractDataModel(url=url),
            )

    # ─── download_direct_media (без изменений, YouTube сюда не попадает) ─

    def download_direct_media(self, url: str, file_extension: str) -> AbstractResultModel:
        logger.info("download_direct_media: url=%s ext=%s", url, file_extension)

        safe = self._generate_safe_filename(url, "direct")
        file_path = self.output_path / f"{safe}.{file_extension}"

        opts = dict(self.base_opts)
        opts.update(
            {
                "outtmpl": str(file_path),
                "merge_output_format": file_extension,
                "format": "best",
            }
        )

        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

            _assert_nonempty_file(file_path)

            return AbstractResultModel(
                data=AbstractDataModel(url=url, path=str(file_path))
            )

        except DownloadError as e:
            return AbstractResultModel(
                status="error",
                context=f"Ошибка загрузки: {e}",
                code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                data=AbstractDataModel(url=url),
            )

        except Exception as e:
            return AbstractResultModel(
                status="error",
                context=f"Неожиданная ошибка загрузки: {e}",
                code=AbstractErrorCodeModel.UNEXPECTED_ERROR,
                data=AbstractDataModel(url=url),
            )
