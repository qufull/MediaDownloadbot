import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)


def _safe_str(x: Any, default: str = "") -> str:
    if x is None:
        return default
    s = str(x).strip()
    return s if s else default


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


class FacebookExtractor:
    """
    Экстрактор медиа-контента из Facebook через yt-dlp.

    Поддерживает:
    - Видео из постов (facebook.com/watch, /videos/, /reel/)
    - Reels
    - Фото (через thumbnails)

    Совместим с форматом данных остальных экстракторов проекта.
    """

    SUPPORTED_DOMAINS = (
        "facebook.com",
        "fb.com",
        "fb.watch",
        "m.facebook.com",
        "web.facebook.com",
        "www.facebook.com",
    )

    def __init__(
        self,
        cookie_path: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.cookie_path = cookie_path
        self.proxy = proxy

    # ------------------------------------------------------------------ #
    # Публичный интерфейс (совместим с AbstractExtractor через адаптер)   #
    # ------------------------------------------------------------------ #

    def extract_info(self, url: str) -> "_ResultAdapter":
        result = self._extract(url=url)
        return _ResultAdapter(result)

    def get_error_description(self, code: Any) -> str:
        descriptions = {
            "FB_INVALID_URL": "Неверный или неподдерживаемый URL Facebook",
            "FB_EXTRACT_ERROR": "Не удалось извлечь медиа из Facebook",
            "FB_NO_MEDIA": "Медиа не найдено в публикации Facebook",
            "FB_PRIVATE_CONTENT": "Контент недоступен (возможно, приватный или требует авторизации)",
            "FB_UNEXPECTED_ERROR": "Непредвиденная ошибка при обработке Facebook",
            "FB_LOGIN_REQUIRED": "Для скачивания этого контента требуется авторизация Facebook",
        }
        return descriptions.get(_safe_str(code), _safe_str(code, default="FB_EXTRACT_ERROR"))

    # ------------------------------------------------------------------ #
    # Внутренняя логика                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower().lstrip("www.")
            return any(
                host == d or host.endswith("." + d)
                for d in FacebookExtractor.SUPPORTED_DOMAINS
            )
        except Exception:
            return False

    def _build_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if self.cookie_path:
            opts["cookiefile"] = self.cookie_path
        if self.proxy:
            opts["proxy"] = self.proxy
        return opts

    def _extract(self, url: str) -> Dict[str, Any]:
        if not self._validate_url(url):
            return {
                "status": "error",
                "code": "FB_INVALID_URL",
                "context": f"URL не является поддерживаемым URL Facebook: {url}",
                "data": None,
            }

        try:
            with YoutubeDL(self._build_ydl_opts()) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return {
                    "status": "error",
                    "code": "FB_NO_MEDIA",
                    "context": "yt-dlp не вернул данные",
                    "data": None,
                }

            # Если плейлист — берём первый элемент
            if info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return {
                        "status": "error",
                        "code": "FB_NO_MEDIA",
                        "context": "Пустой плейлист",
                        "data": None,
                    }
                info = entries[0]

            title = _safe_str(
                info.get("title") or info.get("description", "")[:60],
                default="Facebook media",
            )
            author_name = _safe_str(
                info.get("uploader")
                or info.get("channel")
                or info.get("uploader_id"),
                default="Unknown",
            )

            # ---------- thumbnails ----------
            thumbnails: List[Dict[str, Any]] = []
            for i, t in enumerate(info.get("thumbnails") or []):
                u = t.get("url")
                if not u:
                    continue
                thumbnails.append({
                    "id": f"thumb_{i}",
                    "url": u,
                    "width": t.get("width"),
                    "height": t.get("height"),
                    "name": f"Thumbnail_{i}",
                })
            if not thumbnails and info.get("thumbnail"):
                thumbnails.append({
                    "id": "thumb_0",
                    "url": info["thumbnail"],
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "name": "Thumbnail_0",
                })

            # ---------- formats → videos / audios ----------
            formats = info.get("formats") or []
            videos: List[Dict[str, Any]] = []
            audios: List[Dict[str, Any]] = []

            for f in formats:
                if not f:
                    continue
                direct_url = f.get("url")
                if not direct_url:
                    continue

                fmt_id = _safe_str(f.get("format_id"), default="")
                ext = _safe_str(f.get("ext"), default="mp4")
                vcodec = f.get("vcodec")
                acodec = f.get("acodec")
                height = _safe_int(f.get("height"), 0)
                width = _safe_int(f.get("width"), 0)
                total_bitrate = _safe_int(f.get("tbr"), 0)
                language = _safe_str(f.get("language"), default="").lower()
                language_preference = _safe_int(f.get("language_preference"), 0)
                has_audio = bool(acodec and acodec != "none")

                if vcodec and vcodec != "none":
                    vid = fmt_id or (f"{height}p" if height else "best")
                    videos.append({
                        "id": vid,
                        "name": vid,
                        "format_id": vid,
                        "ext": ext,
                        "url": direct_url,
                        "width": width or None,
                        "height": height or None,
                        "fps": f.get("fps"),
                        "has_audio": has_audio,
                        "language": language or None,
                        "language_preference": language_preference,
                        "total_bitrate": total_bitrate,
                    })
                elif acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                    aid = fmt_id or "bestaudio"
                    audios.append({
                        "id": aid,
                        "name": aid,
                        "format_id": aid,
                        "ext": ext,
                        "url": direct_url,
                        "language": language or None,
                        "language_preference": language_preference,
                        "total_bitrate": total_bitrate,
                    })

            # Fallback: прямая ссылка без форматов
            if not videos and info.get("url"):
                videos.append({
                    "id": "best",
                    "name": "best",
                    "format_id": "best",
                    "ext": _safe_str(info.get("ext"), default="mp4"),
                    "url": info["url"],
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "has_audio": True,
                    "language": None,
                    "language_preference": 0,
                    "total_bitrate": 0,
                })

            # ---------- images ----------
            images: List[Dict[str, Any]] = []
            if not videos and not audios and thumbnails:
                for i, t in enumerate(thumbnails):
                    images.append({
                        "id": f"image_{i}",
                        "url": t["url"],
                        "width": t.get("width"),
                        "height": t.get("height"),
                        "name": f"Photo_{i}",
                    })
                thumbnails = []

            if not videos and not images:
                return {
                    "status": "error",
                    "code": "FB_NO_MEDIA",
                    "context": "Медиа не найдено в публикации",
                    "data": None,
                }

            # Страховка: id/name обязаны быть
            for i, v in enumerate(videos):
                v.setdefault("id", v.get("format_id") or f"video_{i}")
                v.setdefault("name", v.get("id"))
            for i, a in enumerate(audios):
                a.setdefault("id", a.get("format_id") or f"audio_{i}")
                a.setdefault("name", a.get("id"))
            for i, t in enumerate(thumbnails):
                t.setdefault("id", f"thumb_{i}")

            data = {
                "url": url,
                "author_name": author_name,
                "title": title,
                "description": info.get("description"),
                "is_video": bool(videos),
                "is_image": bool(images),
                "videos": videos,
                "audios": audios,
                "images": images,
                "thumbnails": thumbnails,
            }

            logger.info(
                "Facebook extracted: %d videos, %d images, %d audios",
                len(videos), len(images), len(audios),
            )
            return {"status": "success", "code": 0, "context": None, "data": data}

        except DownloadError as e:
            err = str(e).lower()
            logger.warning("FacebookExtractor DownloadError: %s", e)

            if "login" in err or "log in" in err or "sign in" in err:
                return {
                    "status": "error",
                    "code": "FB_LOGIN_REQUIRED",
                    "context": str(e),
                    "data": None,
                }
            if "private" in err or "unavailable" in err:
                return {
                    "status": "error",
                    "code": "FB_PRIVATE_CONTENT",
                    "context": str(e),
                    "data": None,
                }
            return {
                "status": "error",
                "code": "FB_EXTRACT_ERROR",
                "context": str(e),
                "data": None,
            }
        except Exception as e:
            logger.exception("FacebookExtractor Exception: %s", e)
            return {
                "status": "error",
                "code": "FB_UNEXPECTED_ERROR",
                "context": str(e),
                "data": None,
            }


class _ResultAdapter:
    def __init__(self, d: Dict[str, Any]):
        self._d = d

    def to_dict(self) -> Dict[str, Any]:
        return self._d