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


class VKExtractor:
    """
    Экстрактор для ВКонтакте через yt-dlp.

    Поддерживает:
    - Видео (vk.com/video, vk.com/clip, vk.com/wall с видео)
    - Фото из постов (через og:image / thumbnails)

    payload совместим с UI/handler:
      - videos[*]: id, name, height, has_audio, total_bitrate, language_preference
      - thumbnails[*]: id, url
      - images[*]: id, url
      - audios[*]: id, name
    """

    def __init__(
        self,
        cookie_path: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.cookie_path = cookie_path
        self.proxy = proxy

    def extract_info(self, url: str):
        result = self._extract(url=url)
        return _ResultAdapter(result)

    def get_error_description(self, code: Any) -> str:
        descriptions = {
            "VK_EXTRACT_ERROR": "Не удалось извлечь медиа из ВКонтакте",
            "VK_UNEXPECTED_ERROR": "Произошла непредвиденная ошибка при обработке ВКонтакте",
            "VK_NO_MEDIA": "Медиа не найдено в публикации ВКонтакте",
            "VK_PRIVATE_CONTENT": "Контент недоступен (возможно, приватный)",
        }
        return descriptions.get(_safe_str(code), _safe_str(code, default="VK_EXTRACT_ERROR"))

    @staticmethod
    def _validate_vk_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
            return any(
                host == d or host.endswith("." + d)
                for d in ("vk.com", "vk.ru", "vkvideo.ru")
            )
        except Exception:
            return False

    def _extract(self, url: str) -> Dict[str, Any]:
        if not self._validate_vk_url(url):
            return {
                "status": "error",
                "code": "VK_EXTRACT_ERROR",
                "context": "Неверный URL ВКонтакте",
                "data": None,
            }

        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": False,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "headers": {
                "Referer": "https://vkvideo.ru/", # Чтобы ВК думал, что мы на сайте
                "Origin": "https://vkvideo.ru",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            }
        }

        if self.cookie_path:
            ydl_opts["cookiefile"] = self.cookie_path
        if self.proxy:
            ydl_opts["proxy"] = self.proxy

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                return {
                    "status": "error",
                    "code": "VK_NO_MEDIA",
                    "context": "yt-dlp не вернул данные",
                    "data": None,
                }

            # Если вернулся плейлист/альбом — берём первый
            if isinstance(info, dict) and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return {
                        "status": "error",
                        "code": "VK_NO_MEDIA",
                        "context": "Пустой плейлист",
                        "data": None,
                    }
                info = entries[0]

            title = _safe_str(
                info.get("title") or info.get("description", "")[:50],
                default="VK media",
            )
            author_name = _safe_str(
                info.get("uploader")
                or info.get("channel")
                or info.get("uploader_id")
                or info.get("creator"),
                default="Unknown",
            )

            # ---------- thumbnails ----------
            thumbnails: List[Dict[str, Any]] = []
            raw_thumbs = info.get("thumbnails") or []
            for i, t in enumerate(raw_thumbs):
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
                        "tbr": f.get("tbr"),
                        "filesize": f.get("filesize") or f.get("filesize_approx"),
                        "has_audio": has_audio,
                        "language": language or None,
                        "language_preference": language_preference,
                        "total_bitrate": total_bitrate,
                    })
                    continue

                # Чистое аудио
                if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                    aid = fmt_id or "bestaudio"
                    audios.append({
                        "id": aid,
                        "name": aid,
                        "format_id": aid,
                        "ext": ext,
                        "url": direct_url,
                        "abr": f.get("abr"),
                        "filesize": f.get("filesize") or f.get("filesize_approx"),
                        "language": language or None,
                        "language_preference": language_preference,
                        "total_bitrate": total_bitrate,
                    })

            # fallback: если formats пустые, но есть url
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

            # ---------- images (фото из поста) ----------
            images: List[Dict[str, Any]] = []

            # Если нет видео форматов — это может быть фото-пост
            # yt-dlp для VK иногда возвращает thumbnail как основной контент
            if not videos and not audios and thumbnails:
                for i, t in enumerate(thumbnails):
                    images.append({
                        "id": f"image_{i}",
                        "url": t["url"],
                        "width": t.get("width"),
                        "height": t.get("height"),
                        "name": f"Photo_{i}",
                    })
                # Если изображения извлечены из thumbnails, очищаем thumbnails
                thumbnails = []

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

            # Страховка: id/name обязаны быть
            for i, v in enumerate(data["videos"]):
                v.setdefault("id", v.get("format_id") or f"video_{i}")
                v.setdefault("name", v.get("id"))
            for i, a in enumerate(data["audios"]):
                a.setdefault("id", a.get("format_id") or f"audio_{i}")
                a.setdefault("name", a.get("id"))
            for i, t in enumerate(data["thumbnails"]):
                t.setdefault("id", f"thumb_{i}")

            if not videos and not images:
                return {
                    "status": "error",
                    "code": "VK_NO_MEDIA",
                    "context": "Медиа не найдено в публикации",
                    "data": None,
                }

            logger.info(
                "VK extracted: %d videos, %d images, %d audios",
                len(videos), len(images), len(audios),
            )
            return {"status": "success", "code": 0, "context": None, "data": data}

        except DownloadError as e:
            err = str(e)
            logger.warning("VKExtractor DownloadError: %s", err)

            if "login" in err.lower() or "private" in err.lower():
                return {
                    "status": "error",
                    "code": "VK_PRIVATE_CONTENT",
                    "context": str(e),
                    "data": None,
                }

            return {
                "status": "error",
                "code": "VK_EXTRACT_ERROR",
                "context": str(e),
                "data": None,
            }
        except Exception as e:
            logger.exception("VKExtractor Exception: %s", e)
            return {
                "status": "error",
                "code": "VK_UNEXPECTED_ERROR",
                "context": str(e),
                "data": None,
            }


class _ResultAdapter:
    def __init__(self, d: Dict[str, Any]):
        self._d = d

    def to_dict(self) -> Dict[str, Any]:
        return self._d
