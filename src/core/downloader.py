import hashlib
import logging
import os
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


def _find_downloaded_file(output_dir: Path, base_name: str, extensions: tuple = (".mp4", ".mkv", ".webm", ".m4a")) -> Path | None:
    """Ищет скачанный файл по базовому имени (yt-dlp может добавить суффикс формата)."""
    for ext in extensions:
        candidate = output_dir / f"{base_name}{ext}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
        # yt-dlp может создать file.f123.mp4
        for f in output_dir.glob(f"{base_name}.*{ext}"):
            if f.stat().st_size > 0:
                return f
    return None


def _is_fragment_file(path: Path, base_name: str) -> bool:
    """Проверяет, является ли файл фрагментом yt-dlp (базовое_имя.fNNN.ext)."""
    import re
    stem = path.stem  # e.g. "fa3d219761dae782.f251"
    return bool(re.search(r'\.f\d+$', stem)) and stem.startswith(base_name)


def _merge_youtube_fragments(output_dir: Path, base_name: str, out_path: Path,
                              timeout_sec: int = 300) -> bool:
    """
    Запасной план когда yt-dlp не смог смёрджить фрагменты сам.
    Находит видео- и аудио-фрагменты, запускает ffmpeg вручную с -c:a aac.
    """
    import subprocess, re

    # Ищем ВСЕ фрагменты с паттерном base_name.fNNN.*
    all_frags = [
        f for f in output_dir.glob(f"{base_name}.f*")
        if re.search(r'\.f\d+', f.stem) and f.stat().st_size > 0
    ]
    logger.info(
        "_merge_youtube_fragments: найдено %d фрагментов: %s",
        len(all_frags), [f.name for f in all_frags],
    )
    if len(all_frags) < 2:
        logger.warning("_merge_youtube_fragments: найдено %d фрагментов, нужно ≥2", len(all_frags))
        return False

    video_frags, audio_frags = [], []
    for f in all_frags:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-show_entries", "stream=codec_type",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 str(f)],
                capture_output=True, text=True, timeout=10,
            )
            types = r.stdout.strip().lower()
            if "video" in types:
                video_frags.append(f)
            elif "audio" in types:
                audio_frags.append(f)
        except Exception:
            pass

    if not video_frags:
        logger.warning("_merge_youtube_fragments: нет видео-фрагмента")
        return False
    if not audio_frags:
        logger.warning("_merge_youtube_fragments: нет аудио-фрагмента")
        return False

    video = max(video_frags, key=lambda x: x.stat().st_size)
    audio = max(audio_frags, key=lambda x: x.stat().st_size)
    logger.info("_merge_youtube_fragments: video=%s audio=%s → %s", video.name, audio.name, out_path.name)

    r = subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video),
         "-i", str(audio),
         "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart",
         str(out_path)],
        capture_output=True, timeout=timeout_sec,
    )
    if r.returncode != 0:
        logger.error("_merge_youtube_fragments ffmpeg failed: %s",
                     r.stderr.decode(errors="replace")[-500:])
        return False
    if not out_path.exists() or out_path.stat().st_size <= 0:
        logger.error("_merge_youtube_fragments: output empty")
        return False

    for f in video_frags + audio_frags:
        f.unlink(missing_ok=True)
    logger.info("_merge_youtube_fragments: успешно → %s (%.1f МБ)",
                out_path.name, out_path.stat().st_size / 1024 / 1024)
    return True


def _ensure_h264(file_path: Path, timeout_sec: int = 300) -> Path:
    """
    Проверяет кодек видео и перекодирует в H.264 + AAC если нужно.
    VP9/AV1/HEVC → H.264 MP4 для совместимости.
    HDR (smpte2084/HLG) → SDR с tone-mapping (zscale+tonemap), иначе чёрный экран.
    timeout_sec: для больших файлов (8K) — увеличь (например 7200).
    """
    import subprocess

    file_path = Path(file_path)
    if not file_path.exists() or file_path.stat().st_size <= 0:
        return file_path

    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,height,color_transfer",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        lines = [l.strip() for l in (result.stdout.strip() or "").split("\n") if l.strip()]
        codec = (lines[0] if lines else "").lower()
        try:
            height = int(lines[1]) if len(lines) > 1 else 0
        except (ValueError, IndexError, TypeError):
            height = 0
        color_transfer = (lines[2] if len(lines) > 2 else "").lower()

        # HDR: smpte2084 = HDR10/Dolby Vision PQ, arib-std-b67 = HLG
        is_hdr = color_transfer in ("smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12")

        logger.info(
            "_ensure_h264 probe: file=%s codec=%s height=%d color_transfer=%s is_hdr=%s",
            file_path.name, codec or "(none)", height, color_transfer or "(none)", is_hdr,
        )

        def _fast_remux(src: Path, reason: str) -> Path:
            """Быстрый ремукс: копирует стримы как есть, добавляет faststart."""
            out = src.parent / (src.stem + ".remux.mp4")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src),
                     "-c", "copy", "-movflags", "+faststart", str(out)],
                    check=True, timeout=300,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if out.exists() and out.stat().st_size > 0:
                    src.unlink(missing_ok=True)
                    final = src.parent / (src.stem + ".mp4")
                    out.rename(final)
                    logger.info("remux ok [%s] → %s", reason, final.name)
                    return final
            except Exception as e:
                logger.warning("remux failed [%s]: %s — using original", reason, e)
            out.unlink(missing_ok=True)
            return src

        # Уже H.264 SDR — просто добавляем faststart
        if codec in ("h264", "avc1") and not is_hdr:
            return _fast_remux(file_path, "already-h264")

        # 4K/8K (height ≥ 1440): ремукс без перекодирования.
        # VP9/AV1 в MP4 контейнере воспроизводится в Drive/браузерах/Telegram.
        # Перекодирование 4K → H.264 занимает 30-60+ мин — неприемлемо для бота.
        if height >= 1440:
            logger.info(
                "height=%d ≥ 1440 → ремукс без перекодирования (codec=%s, hdr=%s)",
                height, codec, is_hdr,
            )
            return _fast_remux(file_path, f"4k-remux-{codec}")

        # Нет видеопотока = аудио-фрагмент от упавшего мёрджа — не перекодировать
        if not codec:
            logger.error(
                "_ensure_h264: нет видеопотока в %s (аудио-фрагмент или битый файл) — пропускаем",
                file_path,
            )
            return file_path

        crf = "20" if height >= 2160 else "23"
        out_path = file_path.parent / (file_path.stem + ".h264.mp4")

        if is_hdr:
            logger.info(
                "HDR видео (color_transfer=%s, codec=%s, %dp) → tone-mapping → SDR H.264 (CRF=%s)",
                color_transfer, codec, height, crf,
            )
            # Попытка 1: zscale + tonemap (требует libzimg)
            vf_tonemap = (
                "zscale=t=linear:npl=100,"
                "format=gbrpf32le,"
                "zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,"
                "format=yuv420p"
            )
            encode_cmd = [
                "ffmpeg", "-y",
                "-i", str(file_path),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", crf,
                "-vf", vf_tonemap,
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                "-colorspace", "bt709",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(out_path),
            ]
            proc = subprocess.run(
                encode_cmd, timeout=timeout_sec,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if proc.returncode != 0:
                # Попытка 2: без zscale, простое снятие HDR-метаданных (качество хуже, но работает везде)
                logger.warning(
                    "zscale/tonemap недоступен (ffmpeg error), применяем упрощённый HDR→SDR: %s",
                    proc.stderr.decode(errors="replace")[-300:],
                )
                out_path.unlink(missing_ok=True)
                encode_cmd_simple = [
                    "ffmpeg", "-y",
                    "-i", str(file_path),
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", crf,
                    "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709",
                    "-colorspace", "bt709",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(out_path),
                ]
                subprocess.run(encode_cmd_simple, check=True, timeout=timeout_sec,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            logger.info(
                "Видео кодек '%s' (%dp), перекодирование в H.264 (CRF=%s, timeout=%ds)",
                codec, height, crf, timeout_sec,
            )
            encode_cmd = [
                "ffmpeg", "-y",
                "-i", str(file_path),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", crf,
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(out_path),
            ]
            subprocess.run(encode_cmd, check=True, timeout=timeout_sec,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if out_path.exists() and out_path.stat().st_size > 0:
            file_path.unlink(missing_ok=True)
            final_path = file_path.parent / (file_path.stem + ".mp4")
            out_path.rename(final_path)
            logger.info("Re-encoded → H.264 MP4 (HDR=%s): %s", is_hdr, final_path)
            return final_path
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


# Маппинг качества YouTube → format-строки yt-dlp.
# Приоритеты (через /):
#   1. H.264 видео + AAC m4a аудио (идеальная совместимость с MP4 контейнером)
#   2. VP9 видео + AAC m4a аудио   (opus убран — opus+mp4 даёт broken файл в ряде версий ffmpeg)
#   3. VP9 видео + любое аудио     (fallback без best[height<=Np] — он даёт combined 360p)
# ─── QUALITY_MAP ────────────────────────────────────────────────────────────
# YouTube не отдаёт avc1 (H.264) выше 1080p — VP9/AV1 начинаются с 1440p+.
# Поэтому для 4320p/2160p/1440p НЕ указываем vcodec^=avc1 — иначе получим 1080p!
# Для 1080p и ниже avc1 предпочтителен (не нужна конвертация через _ensure_h264).
# Аудио: сначала m4a (AAC, совместим с MP4), затем любое (Opus будет перекодирован Merger).
QUALITY_MAP: Dict[str, str] = {
    # bestvideo[height<=N] — yt-dlp сортирует по высоте (desc), затем по битрейту.
    # Это всегда даёт самое высокое доступное разрешение ≤N, независимо от кодека.
    # VP9/AV1 → _ensure_h264 конвертирует в H.264.  avc1 → быстрый faststart remux.
    # Аудио: m4a (AAC, совместим с MP4) → не нужна конвертация при merge.
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

# REENCODE_LARGE_TO_H264=false — не перекодировать файлы >2 ГБ (экономия времени для 8K)
REENCODE_LARGE_TO_H264 = os.environ.get("REENCODE_LARGE_TO_H264", "true").lower() in ("true", "1", "yes")


class YoutubeErrorCode:
    """Классификация ошибок YouTube для диагностики и алертов."""
    RUNTIME_MISSING = "youtube_runtime_missing"
    EJS_BROKEN = "youtube_ejs_broken"
    CHALLENGE_FAILED = "youtube_challenge_failed"
    FORMATS_TRUNCATED = "youtube_formats_truncated"
    AUTH_REQUIRED = "youtube_auth_required"
    VIDEO_UNAVAILABLE = "youtube_video_unavailable"
    QUALITY_UNAVAILABLE = "youtube_quality_unavailable"


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
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
        vk_cookie_path: Optional[str] = None,
        rutube_proxy: Optional[str] = None,
        concurrent_download_count: int = 2,
        output_path: Union[str, Path] = "./downloads/",
        massbots_token: Optional[str] = None,
        massbots_bot_id: Optional[str] = None,
        bot_token: Optional[str] = None,
        youtube_poll_interval: float = 3.0,
        youtube_player_client: str = "default",
        youtube_player_js_variant: Optional[str] = None,
    ) -> None:
        self.proxy = proxy
        self.youtube_player_client = youtube_player_client
        self.youtube_player_js_variant = youtube_player_js_variant
        self.rutube_proxy = rutube_proxy
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
            "proxy": self.proxy,
            "cookiefile": str(self.cookies_path) if self.cookies_path else None,
            # max_filesize убран: не ограничиваем поток по размеру.
            # Ограничение 2 ГБ обрезало видео-поток → последние минуты только аудио.
            # Файлы идут на Google Drive (лимит 5 ТБ), не через Telegram напрямую.
            "nopart": False,
            "overwrites": True,  # перезаписывать стale-файлы от упавших предыдущих запусков
            # player_client из .env (YOUTUBE_PLAYER_CLIENT)
            "extractor_args": {"youtube": self._youtube_extractor_args()},
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
            self.proxy,
            self.rutube_proxy,
            self.output_path,
            "massbots" if self._massbots_enabled else "yt-dlp",
        )

    # ─── helpers ────────────────────────────────────────────────────

    def _youtube_extractor_args(self) -> Dict[str, Any]:
        """Аргументы для YouTube extractor из .env (YOUTUBE_PLAYER_CLIENT, YOUTUBE_PLAYER_JS_VARIANT)."""
        args: Dict[str, Any] = {"player_client": [self.youtube_player_client]}
        if self.youtube_player_js_variant:
            args["player_js_variant"] = self.youtube_player_js_variant
        return args

    def _generate_safe_filename(self, url: str, format_id: str) -> str:
        return hashlib.sha256(f"{url}_{format_id}".encode()).hexdigest()[:16]

    def _yt_opts_for_service(self, service: Optional[str]) -> Dict[str, Any]:
        opts = dict(self.base_opts)
        if service == "rutube" and self.rutube_proxy:
            opts["proxy"] = self.rutube_proxy
        if service == "vk" and self.vk_cookies_path:
            opts["cookiefile"] = str(self.vk_cookies_path)
        if service in ("reddit", "kick"):
            opts["http_headers"] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
        return opts

    def _looks_like_yt_bot_block(self, err: Exception) -> bool:
        s = str(err)
        return ("confirm you're not a bot" in s) or ("Sign in to confirm" in s)

    @staticmethod
    def _build_fallback_formats(requested: str) -> List[str]:
        """Для massbots SDK — список качеств для fallback (720p, 480p, …)."""
        priority = ["1080p", "720p", "480p", "360p", "240p"]
        clean = requested.split(":")[0] if ":" in requested else requested
        if clean not in priority:
            return [f for f in priority if f != clean]
        idx = priority.index(clean)
        return priority[idx + 1:]

    @staticmethod
    def _build_youtube_format_chain(requested: str) -> List[str]:
        """
        Цепочка format-строк для yt-dlp: от запрошенного качества вниз по QUALITY_MAP.
        Управляемое понижение вместо абстрактного fallback.
        """
        clean = (requested or "best").split(":")[0].strip().lower()
        if clean == "best":
            return ["bestvideo+bestaudio/best", "best"]
        if clean not in QUALITY_MAP:
            return ["bestvideo+bestaudio/best", "best"]
        idx = QUALITY_ORDER.index(clean)
        chain = [QUALITY_MAP[q] for q in QUALITY_ORDER[idx:]]
        chain.append("bestvideo+bestaudio/best")
        chain.append("best")
        return chain

    @staticmethod
    def _parse_height(format_str: str) -> Optional[int]:
        import re
        match = re.match(r"^(\d+)p$", str(format_str).strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def _classify_youtube_error(err: Exception) -> tuple[str, str]:
        """Возвращает (error_code, user_message)."""
        s = str(err).lower()
        if "404" in str(err) or "not found" in s:
            return YoutubeErrorCode.VIDEO_UNAVAILABLE, "Видео удалено или недоступно"
        if "403" in str(err) or "blocked" in s or "forbidden" in s:
            return YoutubeErrorCode.AUTH_REQUIRED, "Доступ заблокирован. Попробуйте позже или используйте VPN."
        if "only images" in s:
            return YoutubeErrorCode.FORMATS_TRUNCATED, "Доступны только превью (видео недоступно). Попробуйте позже или другую ссылку."
        if "origin" in s and "undefined" in s:
            return YoutubeErrorCode.CHALLENGE_FAILED, "YouTube временно нестабилен. Попробуйте позже."
        if "format is not available" in s or "requested format" in s:
            return YoutubeErrorCode.QUALITY_UNAVAILABLE, "Выбранное качество недоступно. Скачиваем в доступном качестве."
        if "no video formats" in s:
            return YoutubeErrorCode.FORMATS_TRUNCATED, "Видео недоступно для скачивания. Попробуйте позже."
        return YoutubeErrorCode.VIDEO_UNAVAILABLE, f"Ошибка загрузки: {err}"

    @staticmethod
    def _get_download_error_context(err: Exception, service: str) -> str:
        """Универсальное сообщение об ошибке. Для YouTube — через классификацию."""
        if service == "youtube":
            _, msg = Downloader._classify_youtube_error(err)
            return msg
        s = str(err).lower()
        if "404" in str(err) or "not found" in s:
            return "Видео удалено или недоступно"
        if "403" in str(err) or "blocked" in s or "forbidden" in s:
            return "Доступ заблокирован. Попробуйте позже или используйте VPN."
        if "only images" in s:
            return "Доступны только превью (видео недоступно). Попробуйте позже или другую ссылку."
        if "format is not available" in s or "requested format" in s:
            return "Выбранное качество недоступно для этого видео. Попробуйте другое качество."
        return f"Ошибка загрузки: {err}"

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

        # massbots для video отключён: file_id привязан к чужому боту и
        # несовместим с локальным Telegram Bot API сервером.
        # Все YouTube видео обрабатываются через yt-dlp и загружаются
        # через локальный сервер (до 2 ГБ).
        if False and service == "youtube" and self._massbots_enabled:
            req_height = self._parse_height(video_format_id)
            if req_height is None or req_height <= 1080:
                fallbacks = self._build_fallback_formats(video_format_id)
                return self._youtube_download_via_sdk(
                    url=url, format_str=video_format_id, fallback_formats=fallbacks
                )
            logger.info(
                "YouTube %s > 1080p — bypassing massbots, using yt-dlp directly",
                video_format_id,
            )

        # ── Разбираем таймкоды из video_format_id (формат "1080p|90-300") ──
        tc_start_sec: float | None = None
        tc_end_sec: float | None = None
        base_format_id = video_format_id
        if "|" in video_format_id:
            base_format_id, tc_raw = video_format_id.split("|", 1)
            if "-" in tc_raw:
                try:
                    s_str, e_str = tc_raw.split("-", 1)
                    tc_start_sec = float(s_str)
                    tc_end_sec = float(e_str)
                    logger.info(
                        "download_video: timecode fragment %.1f–%.1f sec", tc_start_sec, tc_end_sec
                    )
                except (ValueError, TypeError):
                    tc_start_sec = tc_end_sec = None

        # ── Остальные → yt-dlp  (+ YouTube 2K/4K) ──
        safe = self._generate_safe_filename(url, base_format_id)
        file_ext = "mp3" if service == "soundcloud" else "mp4"
        file_path = self.output_path / f"{safe}.{file_ext}"

        if on_progress:
            def _yt_progress_hook(d):
                try:
                    status = d.get("status")
                    if status == "downloading":
                        downloaded = d.get("downloaded_bytes") or 0
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        speed = d.get("speed") or 0
                        frag_idx = d.get("fragment_index")
                        frag_cnt = d.get("fragment_count")
                        if total and total > 0:
                            percent = min(99.0, downloaded / total * 100.0)
                        elif frag_cnt and frag_cnt > 0 and frag_idx is not None:
                            percent = min(99.0, (frag_idx + 1) / frag_cnt * 100.0)
                        else:
                            percent = 0.0 if not downloaded else 1.0
                        on_progress(percent, downloaded, total, speed)
                    elif status == "finished":
                        on_progress(100.0, 0, 0, 0)
                except Exception:
                    pass
        else:
            _yt_progress_hook = None

        if service == "youtube":
            # Цепочка по QUALITY_MAP — управляемое понижение качества
            yt_fallbacks = self._build_youtube_format_chain(base_format_id)
            opts = self._yt_opts_for_service(service)
            opts.update({
                "outtmpl": str(file_path),    # всегда {safe}.mp4 — единое имя для поиска
                "format": yt_fallbacks[0],
                "merge_output_format": "mp4",
                "remux_video": "mp4",
                # Сохранить фрагменты если merge не удался — нужно для ручного склеивания
                "keep_fragments": True,
                # Конвертировать opus → AAC во время мёрджа:
                # VP9+opus в MP4 — несовместимо, ffmpeg молча падает, оставляет только аудио-фрагмент.
                # ВАЖНО: yt-dlp ищет ключ по PP_NAME (регистр зависит от версии) → передаём все варианты.
                "postprocessor_args": {
                    "merger": ["-c:a", "aac", "-b:a", "192k"],
                    "Merger": ["-c:a", "aac", "-b:a", "192k"],
                    "ffmpegmerger": ["-c:a", "aac", "-b:a", "192k"],
                    "FFmpegMerger": ["-c:a", "aac", "-b:a", "192k"],
                },
            })
            if tc_start_sec is not None and tc_end_sec is not None:
                from yt_dlp.utils import download_range_func
                opts["download_ranges"] = download_range_func(None, [(tc_start_sec, tc_end_sec)])
                opts["force_keyframes_at_cuts"] = True
            if _yt_progress_hook:
                opts["progress_hooks"] = [_yt_progress_hook]
            for yt_attempt, fmt in enumerate(yt_fallbacks):
                try:
                    opts["format"] = fmt
                    with YoutubeDL(opts) as ydl:
                        ydl.download([url])
                    # Диагностика: логируем все файлы с этим базовым именем
                    post_files = sorted(self.output_path.glob(f"{safe}*"))
                    logger.info(
                        "YouTube post-download files [%s]: %s",
                        safe,
                        [f.name for f in post_files] or "(пусто)",
                    )
                    actual_path = file_path
                    if not file_path.exists() or file_path.stat().st_size <= 0:
                        found = _find_downloaded_file(self.output_path, safe)
                        if found:
                            logger.warning("YouTube: итоговый MP4 не найден, найден fallback: %s", found.name)
                            # Проверяем — это фрагмент (fa3d219761dae782.f251.mp4)?
                            if _is_fragment_file(found, safe):
                                # Мёрдж yt-dlp упал — пробуем склеить вручную
                                logger.warning(
                                    "YouTube: fallback является фрагментом %s — пробуем ручной merge", found.name
                                )
                                merged = _merge_youtube_fragments(
                                    self.output_path, safe, file_path,
                                    timeout_sec=300,
                                )
                                if merged:
                                    actual_path = file_path
                                    logger.info("YouTube: ручной merge успешен → %s", actual_path)
                                else:
                                    logger.error(
                                        "YouTube: ручной merge не удался. "
                                        "Проверьте что ffmpeg установлен (apt list --installed | grep ffmpeg)."
                                    )
                                    raise RuntimeError(
                                        f"yt-dlp merge failed and manual merge also failed "
                                        f"(fragment: {found.name})"
                                    )
                            else:
                                actual_path = found
                        else:
                            _assert_nonempty_file(file_path)
                    else:
                        _assert_nonempty_file(file_path)
                    logger.info("YouTube downloaded via yt-dlp: %s (attempt %d)", actual_path, yt_attempt + 1)
                    actual_path = Path(actual_path)
                    file_size = actual_path.stat().st_size
                    if file_size >= 2 * 1024 * 1024 * 1024 and not REENCODE_LARGE_TO_H264:
                        pass  # Пропуск перекодирования для >2 ГБ (REENCODE_LARGE_TO_H264=false)
                    else:
                        timeout = 7200 if file_size >= 2 * 1024 * 1024 * 1024 else 300
                        actual_path = _ensure_h264(actual_path, timeout_sec=timeout)
                    return AbstractResultModel(data=AbstractDataModel(url=url, path=str(actual_path), is_video=True))
                except DownloadError as e:
                    err_lower = str(e).lower()
                    is_format_error = "format is not available" in err_lower or "requested format" in err_lower
                    if is_format_error and yt_attempt < len(yt_fallbacks) - 1:
                        next_fmt = yt_fallbacks[yt_attempt + 1][:50]
                        logger.warning("YouTube format failed (attempt %d), retry with %s: %s", yt_attempt + 1, next_fmt, e)
                        continue
                    err_code, ctx = self._classify_youtube_error(e)
                    logger.warning("YouTube download failed: %s — %s", err_code, e)
                    return AbstractResultModel(status="error", context=ctx,
                                               code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))
                except Exception as e:
                    err_lower = str(e).lower()
                    is_format_error = "format" in err_lower and "not available" in err_lower
                    if is_format_error and yt_attempt < len(yt_fallbacks) - 1:
                        logger.warning("YouTube format failed (attempt %d), retry: %s", yt_attempt + 1, e)
                        continue
                    err_code, ctx = self._classify_youtube_error(e)
                    logger.warning("YouTube download failed: %s — %s", err_code, e)
                    return AbstractResultModel(status="error", context=ctx,
                                               code=AbstractErrorCodeModel.UNEXPECTED_ERROR, data=AbstractDataModel(url=url))

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
        elif service in ("vimeo", "dailymotion", "facebook", "okru",
                         "twitch", "kick", "rumble", "coub"):
            # Для обобщённых платформ: сначала пробуем конкретный format_id,
            # если не получается — падаем на лучшее качество с аудио
            if base_format_id and base_format_id not in ("best", "bestvideo"):
                format_str = (
                    f"{base_format_id}+bestaudio/"
                    f"bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo+bestaudio/best[ext=mp4]/best"
                )
            else:
                format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        elif service == "soundcloud":
            # SoundCloud — только аудио, но если попал сюда (edge-case) — берём best
            format_str = "bestaudio/best"
        else:
            format_str = base_format_id

        if merge_audio and service != "instagram":
            format_str = f"{format_str}+bestaudio/best"

        opts = self._yt_opts_for_service(service)
        merge_fmt = "mp3" if service == "soundcloud" else "mp4"
        opts.update({"outtmpl": str(file_path), "format": format_str, "merge_output_format": merge_fmt})
        if tc_start_sec is not None and tc_end_sec is not None:
            from yt_dlp.utils import download_range_func
            opts["download_ranges"] = download_range_func(None, [(tc_start_sec, tc_end_sec)])
            opts["force_keyframes_at_cuts"] = True
        if _yt_progress_hook:
            opts["progress_hooks"] = [_yt_progress_hook]

        # Fallback при "format is not available" — для Coub, Kick, Reddit и др.
        fallback_fmt = "bestvideo+bestaudio/best" if service != "soundcloud" else "bestaudio/best"
        for attempt, fmt in enumerate([format_str, fallback_fmt]):
            try:
                opts["format"] = fmt
                with YoutubeDL(opts) as ydl:
                    ydl.download([url])
                actual_path = file_path
                if not file_path.exists() or file_path.stat().st_size <= 0:
                    found = _find_downloaded_file(self.output_path, safe)
                    actual_path = found if found else file_path
                _assert_nonempty_file(actual_path)
                if service != "soundcloud":
                    actual_path = Path(actual_path)
                    actual_path = _ensure_h264(actual_path)
                return AbstractResultModel(data=AbstractDataModel(url=url, path=str(actual_path), is_video=True))
            except DownloadError as e:
                if service == "youtube" and self._looks_like_yt_bot_block(e):
                    if self.rutube_proxy and opts.get("proxy") != self.rutube_proxy:
                        opts["proxy"] = self.rutube_proxy
                        continue
                err_lower = str(e).lower()
                if attempt == 0 and ("format is not available" in err_lower or "requested format" in err_lower):
                    logger.warning("%s format failed, retry with %s: %s", service, fallback_fmt[:30], e)
                    continue
                ctx = self._get_download_error_context(e, service)
                return AbstractResultModel(status="error", context=ctx,
                                           code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))
            except Exception as e:
                err_lower = str(e).lower()
                if attempt == 0 and "format" in err_lower and "not available" in err_lower:
                    continue
                ctx = self._get_download_error_context(e, service)
                return AbstractResultModel(status="error", context=ctx,
                                           code=AbstractErrorCodeModel.UNEXPECTED_ERROR, data=AbstractDataModel(url=url))

        return AbstractResultModel(status="error", context="Исчерпаны попытки",
                                   code=AbstractErrorCodeModel.DOWNLOAD_ERROR, data=AbstractDataModel(url=url))

    # ─── download_audio ─────────────────────────────────────────────

    def download_audio(
        self,
        url: str,
        audio_format_id: str,
        service: str = None,
        on_progress=None,
    ) -> Union[AbstractResultModel, YoutubeDownloadResult]:
        logger.info("download_audio: url=%s fmt=%s service=%s", url, audio_format_id, service)

        # massbots для аудио отключён: file_id привязан к чужому боту,
        # getFile с нашим токеном возвращает "wrong file identifier".
        # Всё аудио идёт напрямую через yt-dlp.
        if False and service == "youtube" and self._massbots_enabled:
            # 1. Получаем file_id через massbots (минимальный формат)
            sdk_result = self._youtube_download_via_sdk(
                url=url,
                format_str="240p",
                fallback_formats=["360p", "480p"],
            )

            if sdk_result.status != "success" or not sdk_result.file_id:
                logger.warning("massbots audio: SDK failed (%s), fallback to yt-dlp", sdk_result.context)
                # fallback на yt-dlp ниже
            else:
                # 2. Скачиваем видео через api.telegram.org/getFile
                video_path = self._download_file_from_telegram(sdk_result.file_id)
                if video_path:
                    # 3. Конвертируем в mp3
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

        # ── yt-dlp (все сервисы + YouTube fallback) ──
        safe = self._generate_safe_filename(url, audio_format_id)
        mp3_path = self.output_path / f"{safe}.mp3"
        format_str = "bestaudio/best" if audio_format_id in ("bestaudio", "music", "", None) else str(audio_format_id)

        if on_progress:
            def _yt_audio_progress_hook(d):
                try:
                    status = d.get("status")
                    if status == "downloading":
                        downloaded = d.get("downloaded_bytes") or 0
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        speed = d.get("speed") or 0
                        frag_idx = d.get("fragment_index")
                        frag_cnt = d.get("fragment_count")
                        if total and total > 0:
                            percent = min(99.0, downloaded / total * 100.0)
                        elif frag_cnt and frag_cnt > 0 and frag_idx is not None:
                            percent = min(99.0, (frag_idx + 1) / frag_cnt * 100.0)
                        else:
                            percent = 0.0 if not downloaded else 1.0
                        on_progress(percent, downloaded, total, speed)
                    elif status == "finished":
                        on_progress(100.0, 0, 0, 0)
                except Exception:
                    pass
        else:
            _yt_audio_progress_hook = None

        opts = self._yt_opts_for_service(service)
        opts.update({
            "outtmpl": str(self.output_path / f"{safe}.%(ext)s"),
            "format": format_str,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })
        if _yt_audio_progress_hook:
            opts["progress_hooks"] = [_yt_audio_progress_hook]

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
                                           code=AbstractErrorCodeModel.UNEXPECTED_ERROR, data=AbstractDataModel(url=url))

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