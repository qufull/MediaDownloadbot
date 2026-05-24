import hashlib
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Union, Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .extractors.abstractions import (
    AbstractDataModel,
    AbstractResultModel,
    AbstractErrorCodeModel,
)

logger = logging.getLogger("downloader")


# ──────────────────────────────────────────────────────────────────────
# YouTube-specific result model: содержит file_id вместо path
# ──────────────────────────────────────────────────────────────────────

class YoutubeDownloadResult:
    """
    Результат скачивания YouTube через massbots SDK.

    Содержит Telegram file_id, который нужно отправить через
    api.telegram.org (не через локальный Bot API server).
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
        return True


# ──────────────────────────────────────────────────────────────────────


def _assert_nonempty_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Файл не создан: {path}")
    if path.stat().st_size <= 0:
        raise IOError(f"Файл пустой (0 байт): {path}")


def _ensure_h264(file_path: Path) -> Path:
    """
    Проверяет кодек видео и перекодирует в H.264 + AAC если нужно.
    Instagram и другие сервисы могут отдавать HEVC/VP9 которые
    не проигрываются на мобильных клиентах Telegram.
    """
    import subprocess

    try:
        # Определяем кодек
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "csv=s=x:p=0",
            str(file_path),
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        codec = result.stdout.strip().lower()

        if codec in ("h264", ""):
            # Уже H.264 или не удалось определить — ничего не делаем
            return file_path

        logger.info("Video codec is '%s', re-encoding to H.264 for mobile compatibility", codec)

        out_path = file_path.with_suffix(".h264.mp4")
        encode_cmd = [
            "ffmpeg", "-y",
            "-i", str(file_path),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        subprocess.run(encode_cmd, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if out_path.exists() and out_path.stat().st_size > 0:
            # Заменяем оригинал
            file_path.unlink(missing_ok=True)
            out_path.rename(file_path)
            logger.info("Re-encoded to H.264: %s", file_path)
            return file_path
        else:
            logger.warning("Re-encode output empty, keeping original")
            out_path.unlink(missing_ok=True)
            return file_path

    except subprocess.TimeoutExpired:
        logger.warning("Re-encode timed out, keeping original")
        return file_path
    except Exception as e:
        logger.warning("Re-encode failed: %s, keeping original", e)
        return file_path


QUALITY_MAP: Dict[str, str] = {
    "4320p": "bestvideo[height<=4320]+bestaudio[ext=m4a]/bestvideo[height<=4320]+bestaudio",
    "2160p": "bestvideo[height<=2160]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio",
    "1440p": "bestvideo[height<=1440]+bestaudio[ext=m4a]/bestvideo[height<=1440]+bestaudio",
    "1080p": "bestvideo[height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio",
    "720p":  "bestvideo[height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio",
    "480p":  "bestvideo[height<=480]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio",
    "360p":  "bestvideo[height<=360]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio",
    "240p":  "bestvideo[height<=240]+bestaudio[ext=m4a]/bestvideo[height<=240]+bestaudio",
}
QUALITY_ORDER = ["4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]

class Downloader:
    """
    Downloader с двойным бэкендом:
    - YouTube → massbots SDK (возвращает YoutubeDownloadResult с file_id)
    - Остальные сервисы → yt-dlp (возвращает AbstractResultModel с path)
    """

    _FORMAT_PRIORITY = ["1080p", "720p", "480p", "360p", "240p"]

    def __init__(
        self,
        retries_count: int = 10,
        proxies: Optional[Dict[str, str]] = None,
        cookie_path: Optional[str] = None,
        vk_cookie_path: Optional[str] = None,
        concurrent_download_count: int = 2,
        output_path: Union[str, Path] = "./downloads/",
        massbots_token: Optional[str] = None,
        massbots_bot_id: Optional[str] = None,
        bot_token: Optional[str] = None,
        youtube_poll_interval: float = 3.0,
    ) -> None:
        self.proxies = proxies or {}
        self.bot_token = bot_token
        self.cookies_path = Path(cookie_path) if cookie_path else None
        self.vk_cookies_path = Path(vk_cookie_path) if vk_cookie_path else None
        self.output_path = Path(output_path) if isinstance(output_path, str) else output_path
        self.output_path.mkdir(exist_ok=True, parents=True)
        self.youtube_poll_interval = youtube_poll_interval

        if self.cookies_path and not self.cookies_path.exists():
            logger.warning("Cookies file not found: %s (ignore)", self.cookies_path)
            self.cookies_path = None

        if self.vk_cookies_path and not self.vk_cookies_path.exists():
            logger.warning("VK cookies file not found: %s (ignore)", self.vk_cookies_path)
            self.vk_cookies_path = None

        self.base_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "playlistend": 1,
            "retries": retries_count,
            "fragment_retries": retries_count,
            "concurrent_fragment_downloads": concurrent_download_count,
            "cookiefile": str(self.cookies_path) if self.cookies_path else None,
            "max_filesize": 2 * 1024 * 1024 * 1024,  # Лимит 2 ГБ (в байтах)
            "nopart": False,
            "socket_timeout": 30,
        }

        # massbots SDK для YouTube
        self._massbots_api = None
        self._massbots_enabled = False
        if massbots_token:
            try:
                import massbots
                self._massbots_api = massbots.Api(
                    token=massbots_token,
                    bot_id=massbots_bot_id,
                )
                self._massbots_enabled = True
                logger.info("massbots SDK initialized: bot_id=%s", massbots_bot_id or "default")
            except Exception as e:
                logger.error("Failed to init massbots SDK: %s", e)

        logger.info(
            "Downloader init. cookies=%s proxy=%s rutube_proxy=%s out=%s youtube=%s",
            str(self.cookies_path) if self.cookies_path else "disabled",
            self.output_path,
            "massbots" if self._massbots_enabled else "yt-dlp",
        )

    # ─── helpers ────────────────────────────────────────────────────

    def _generate_safe_filename(self, url: str, format_id: str) -> str:
        return hashlib.sha256(f"{url}_{format_id}".encode()).hexdigest()[:16]

    def _yt_opts_for_service(self, service: Optional[str]) -> Dict[str, Any]:
        opts = dict(self.base_opts)

        # Динамически подставляем прокси для сервиса
        if service and service in self.proxies and self.proxies[service]:
            opts["proxy"] = self.proxies[service]
        else:
            opts.pop("proxy", None)

        if service == "vk" and self.vk_cookies_path:
            opts["cookiefile"] = str(self.vk_cookies_path)

        return opts

    def _looks_like_yt_bot_block(self, err: Exception) -> bool:
        s = str(err)
        return ("confirm you're not a bot" in s) or ("Sign in to confirm" in s)

    @staticmethod
    def _build_fallback_formats(requested: str) -> List[str]:
        priority = ["1080p", "720p", "480p", "360p", "240p"]
        clean = requested.split(":")[0] if ":" in requested else requested

        if clean not in priority:
            return [f for f in priority if f != clean]

        idx = priority.index(clean)

        return priority[idx + 1:]

    @staticmethod
    def _parse_height(format_str: str) -> Optional[int]:
        import re
        match = re.match(r"^(\d+)p$", str(format_str).strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _extract_youtube_video_id(url: str) -> Optional[str]:
        import re
        from urllib.parse import urlparse, parse_qs

        if not url:
            return None
        match = re.match(r"https?://youtu\.be/([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "youtube.com" not in host and "youtu.be" not in host:
            return None
        path = parsed.path or ""
        match = re.match(r"/shorts/([a-zA-Z0-9_-]{11})", path)
        if match:
            return match.group(1)
        match = re.match(r"/(embed|v)/([a-zA-Z0-9_-]{11})", path)
        if match:
            return match.group(2)
        qs = parse_qs(parsed.query)
        v = qs.get("v")
        if v and len(v[0]) == 11:
            return v[0]
        return None

    # ─── YouTube через massbots SDK ────────────────────────────────

    def _youtube_download_via_sdk(
        self,
        url: str,
        format_str: str,
        fallback_formats: Optional[List[str]] = None,
    ) -> YoutubeDownloadResult:
        """
        Скачивает YouTube видео через massbots SDK.
        При ошибке пробует fallback форматы.
        """
        if not self._massbots_api:
            return YoutubeDownloadResult(status="error", context="massbots SDK не инициализирован", url=url)

        video_id = self._extract_youtube_video_id(url)
        if not video_id:
            return YoutubeDownloadResult(status="error", context="Не удалось извлечь video_id", url=url)

        formats_to_try = [format_str]
        if fallback_formats:
            for fb in fallback_formats:
                if fb != format_str and fb not in formats_to_try:
                    formats_to_try.append(fb)

        last_error = ""

        for current_format in formats_to_try:
            actual_format = current_format.split(":", 1)[0] if ":" in current_format else current_format
            logger.info("YouTube download via massbots SDK: video_id=%s format=%s", video_id, actual_format)

            try:
                download_result = self._massbots_api.download(video_id, actual_format)
                result = download_result.wait_until_ready(delay=self.youtube_poll_interval)

                if result.status == "ready" and result.file_id:
                    logger.info(
                        "YouTube download ready: video_id=%s format=%s file_id_len=%s file_id=%s",
                        video_id, actual_format, len(result.file_id), result.file_id,
                    )
                    return YoutubeDownloadResult(status="success", file_id=result.file_id, url=url)

                last_error = f"status={result.status} for {actual_format}"
                logger.warning("YouTube download %s: %s. Trying next...", video_id, last_error)

            except Exception as e:
                last_error = str(e)[:200]
                logger.warning("YouTube download error %s format=%s: %s", video_id, actual_format, last_error)

        return YoutubeDownloadResult(status="error", context=f"Все форматы провалились: {last_error}", url=url)

    # ─── Скачать файл через api.telegram.org и конвертировать в mp3 ───

    def _download_file_from_telegram(self, file_id: str) -> Optional[Path]:
        """
        Скачивает файл по file_id через api.telegram.org/getFile.
        Возвращает путь к скачанному файлу или None.
        """
        import urllib.request
        import json

        if not self.bot_token:
            logger.error("bot_token not set, cannot download from Telegram")
            return None

        try:
            # 1. getFile → file_path
            get_url = f"https://api.telegram.org/bot{self.bot_token}/getFile"
            data = urllib.parse.urlencode({"file_id": file_id}).encode("utf-8")
            req = urllib.request.Request(get_url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            if not body.get("ok"):
                logger.error("getFile failed: %s", body)
                return None

            tg_file_path = body["result"]["file_path"]
            logger.info("getFile ok: file_path=%s", tg_file_path)

            # 2. Скачать файл
            download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{tg_file_path}"
            ext = Path(tg_file_path).suffix or ".mp4"
            safe = hashlib.sha256(file_id.encode()).hexdigest()[:16]
            local_path = self.output_path / f"{safe}_tg{ext}"

            urllib.request.urlretrieve(download_url, str(local_path))

            if local_path.exists() and local_path.stat().st_size > 0:
                logger.info("Downloaded from Telegram: %s (%s bytes)", local_path, local_path.stat().st_size)
                return local_path
            else:
                logger.error("Downloaded file is empty: %s", local_path)
                return None

        except Exception as e:
            logger.exception("Failed to download file from Telegram: %s", e)
            return None

    def _convert_video_to_mp3(self, video_path: Path) -> Optional[Path]:
        """
        Извлекает аудио из видео через ffmpeg → mp3.
        Возвращает путь к mp3 или None.
        """
        import subprocess

        mp3_path = video_path.with_suffix(".mp3")

        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "libmp3lame",
                "-ab", "192k",
                "-ar", "44100",
                str(mp3_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)

            if proc.returncode != 0:
                logger.error("ffmpeg failed (rc=%s): %s", proc.returncode, proc.stderr.decode("utf-8", errors="replace")[:500])
                return None

            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                logger.info("Converted to mp3: %s (%s bytes)", mp3_path, mp3_path.stat().st_size)
                # Удаляем видео-исходник
                try:
                    video_path.unlink()
                except Exception:
                    pass
                return mp3_path
            else:
                logger.error("ffmpeg produced empty mp3: %s", mp3_path)
                return None

        except Exception as e:
            logger.exception("ffmpeg conversion failed: %s", e)
            return None

        # ─── download_video ─────────────────────────────────────────────

    def download_video(
            self,
            url: str,
            video_format_id: str,
            merge_audio: bool = False,
            service: str = None,
            on_progress=None,
    ) -> Union[AbstractResultModel, YoutubeDownloadResult]:
        logger.info("download_video: url=%s fmt=%s service=%s", url, video_format_id, service)

        # ─── 1. ПАРСИМ ТАЙМКОДЫ ───
        base_format_id = video_format_id
        tc_start_sec = None
        tc_end_sec = None

        if "|" in video_format_id:
            parts = video_format_id.split("|", 1)
            base_format_id = parts[0]
            if len(parts) > 1 and "-" in parts[1]:
                try:
                    s_str, e_str = parts[1].split("-", 1)
                    tc_start_sec = float(s_str)
                    tc_end_sec = float(e_str)
                except ValueError:
                    pass

        # ── YouTube → massbots SDK ──
        # ВАЖНО: Если запрошен фрагмент (есть таймкоды), отключаем massbots и качаем через yt-dlp
        if service == "youtube" and self._massbots_enabled and tc_start_sec is None:
            fallbacks = self._build_fallback_formats(base_format_id)
            sdk_result = self._youtube_download_via_sdk(url=url, format_str=base_format_id, fallback_formats=fallbacks)

            if sdk_result.status == "success":
                return sdk_result
            else:
                logger.warning(
                    f"Massbots не справился ({sdk_result.context}). Включаем запасной план: локальный yt-dlp!")
        # ── Логика работы с прогрессом ──
        if on_progress:
            def _yt_progress_hook(d):
                try:
                    if d.get("status") == "downloading":
                        downloaded = d.get("downloaded_bytes", 0)
                        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                        speed = d.get("speed", 0)

                        if total > 0:
                            percent = (downloaded / total) * 100.0
                        else:
                            frag_idx = d.get("fragment_index", 0)
                            frag_cnt = d.get("fragment_count")
                            percent = (frag_idx / frag_cnt * 100.0) if frag_cnt else 0.0

                        on_progress(min(percent, 99.9), downloaded, total, speed)
                    elif d.get("status") == "finished":
                        on_progress(100.0, 0, 0, 0)
                except Exception:
                    pass
        else:
            _yt_progress_hook = None

        # ── Остальные → yt-dlp ──
        # Хэш генерируем из оригинального format_id (с таймкодами), чтобы фрагменты не затирали фулл видео в кэше
        safe = self._generate_safe_filename(url, video_format_id)
        file_path = self.output_path / f"{safe}.mp4"

        # Везде дальше используем очищенный base_format_id
        if service == "instagram":
            format_str = "best[ext=mp4]/best"
        elif service == "pinterest":
            if base_format_id == "best":
                format_str = "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best"
            else:
                format_str = "best"
        elif service == "vk":
            format_str = base_format_id or "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best"
        elif service == "reddit":
            if base_format_id and base_format_id != "best":
                format_str = f"{base_format_id}+bestaudio/bestvideo+bestaudio/best"
            else:
                format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            # Если к нам пришло "720p", переводим в формат yt-dlp
            if service == "youtube" and base_format_id in QUALITY_MAP:
                format_str = QUALITY_MAP[base_format_id]
            else:
                format_str = base_format_id

        if merge_audio and service != "instagram":
            format_str = f"{format_str}+bestaudio/best"

        opts = self._yt_opts_for_service(service)
        opts.update({"outtmpl": str(file_path), "format": format_str, "merge_output_format": "mp4"})

        # ─── 2. ПРИМЕНЯЕМ ОБРЕЗКУ ДЛЯ YT-DLP ───
        if tc_start_sec is not None and tc_end_sec is not None:
            from yt_dlp.utils import download_range_func
            opts["download_ranges"] = download_range_func(None, [(tc_start_sec, tc_end_sec)])
            opts["force_keyframes_at_cuts"] = True  # Улучшает точность обрезки

        # Подключаем прогресс-бар к yt-dlp
        if _yt_progress_hook:
            opts["progress_hooks"] = [_yt_progress_hook]

        for attempt in (1, 2):
            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([url])
                _assert_nonempty_file(file_path)

                # Если скачали фрагмент, конвертируем в h264 для надежности на iPhone
                if service in ("instagram", "tiktok", "vk") or tc_start_sec is not None:
                    _ensure_h264(file_path)

                return AbstractResultModel(data=AbstractDataModel(url=url, path=str(file_path), is_video=True))
            except DownloadError as e:
                if service == "youtube" and self._looks_like_yt_bot_block(e):
                    fallback_proxy = self.proxies.get("rutube")
                    if fallback_proxy and opts.get("proxy") != fallback_proxy:
                        opts["proxy"] = fallback_proxy
                        continue
                return AbstractResultModel(status="error", context=f"Ошибка загрузки: {e}",
                                           code=AbstractErrorCodeModel.DOWNLOAD_ERROR,
                                           data=AbstractDataModel(url=url))
            except Exception as e:
                return AbstractResultModel(status="error", context=f"Неожиданная ошибка: {e}",
                                           code=AbstractErrorCodeModel.UNEXPECTED_ERROR,
                                           data=AbstractDataModel(url=url))

        return AbstractResultModel(status="error", context="Исчерпаны попытки",
                                   code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))

    def download_audio(
            self,
            url: str,
            audio_format_id: str,
            service: str = None,
            on_progress=None,  # <-- ДОБАВЛЕНО
    ) -> Union[AbstractResultModel, YoutubeDownloadResult]:
        logger.info("download_audio: url=%s fmt=%s service=%s", url, audio_format_id, service)

        # ── YouTube → massbots SDK → скачать видео → ffmpeg mp3 ──
        if service == "youtube" and self._massbots_enabled:
            sdk_result = self._youtube_download_via_sdk(
                url=url,
                format_str="240p",
                fallback_formats=["360p", "480p"],
            )

            if sdk_result.status != "success" or not sdk_result.file_id:
                logger.warning("massbots audio: SDK failed (%s), fallback to yt-dlp", sdk_result.context)
            else:
                video_path = self._download_file_from_telegram(sdk_result.file_id)
                if video_path:
                    mp3_path = self._convert_video_to_mp3(video_path)
                    if mp3_path:
                        logger.info("YouTube audio ready via massbots+ffmpeg: %s", mp3_path)
                        return AbstractResultModel(
                            data=AbstractDataModel(url=url, path=str(mp3_path), is_video=False)
                        )
                    else:
                        logger.warning("ffmpeg conversion failed, fallback to yt-dlp")
                else:
                    logger.warning("getFile download failed, fallback to yt-dlp")

        # ── Логика работы с прогрессом ──
        if on_progress:
            def _yt_audio_hook(d):
                try:
                    if d.get("status") == "downloading":
                        downloaded = d.get("downloaded_bytes", 0)
                        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                        speed = d.get("speed", 0)
                        percent = (downloaded / total * 100.0) if total > 0 else 0.0
                        on_progress(min(percent, 99.9), downloaded, total, speed)
                    elif d.get("status") == "finished":
                        on_progress(100.0, 0, 0, 0)
                except Exception:
                    pass
        else:
            _yt_audio_hook = None

        # ── yt-dlp (все сервисы + YouTube fallback) ──
        safe = self._generate_safe_filename(url, audio_format_id)
        mp3_path = self.output_path / f"{safe}.mp3"
        if audio_format_id in ("bestaudio", "music", "", None):
            format_str = "bestaudio/best"
        elif service == "youtube" and audio_format_id in QUALITY_MAP:
            # Для аудио берем просто лучшее аудио, игнорируя видео-часть из QUALITY_MAP
            format_str = "bestaudio/best"
        else:
            format_str = str(audio_format_id)
        opts = self._yt_opts_for_service(service)
        opts.update({
            "outtmpl": str(self.output_path / f"{safe}.%(ext)s"),
            "format": format_str,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })

        # Подключаем прогресс-бар к yt-dlp
        if _yt_audio_hook:
            opts["progress_hooks"] = [_yt_audio_hook]

        for attempt in (1, 2):
            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([url])
                _assert_nonempty_file(mp3_path)
                return AbstractResultModel(data=AbstractDataModel(url=url, path=str(mp3_path), is_video=False))
            except DownloadError as e:
                if service == "youtube" and self._looks_like_yt_bot_block(e):
                    if self.rutube_proxy and opts.get("proxy") != self.rutube_proxy:
                        opts["proxy"] = self.rutube_proxy
                        continue
                return AbstractResultModel(status="error", context=f"Ошибка загрузки аудио: {e}",
                                           code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))
            except Exception as e:
                return AbstractResultModel(status="error", context=f"Неожиданная ошибка аудио: {e}",
                                           code=AbstractErrorCodeModel.UNEXPECTED_ERROR,
                                           data=AbstractDataModel(url=url))

        return AbstractResultModel(status="error", context="Исчерпаны попытки аудио",
                                   code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))
    # ─── download_direct_media ──────────────────────────────────────

    def download_direct_media(self, url: str, file_extension: str) -> AbstractResultModel:
        logger.info("download_direct_media: url=%s ext=%s", url, file_extension)
        safe = self._generate_safe_filename(url, "direct")
        file_path = self.output_path / f"{safe}.{file_extension}"

        opts = dict(self.base_opts)
        opts.update({"outtmpl": str(file_path), "merge_output_format": file_extension, "format": "best"})

        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
            _assert_nonempty_file(file_path)
            return AbstractResultModel(data=AbstractDataModel(url=url, path=str(file_path)))
        except DownloadError as e:
            return AbstractResultModel(status="error", context=f"Ошибка: {e}",
                                       code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))
        except Exception as e:
            return AbstractResultModel(status="error", context=f"Неожиданная ошибка: {e}",
                                       code=AbstractErrorCodeModel.UNEXPECTED_ERROR, data=AbstractDataModel(url=url))