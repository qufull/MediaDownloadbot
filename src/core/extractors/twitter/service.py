import logging
from typing import Any, Dict, List, Optional

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


class TwitterExtractor:
    """
    Экстрактор для Twitter/X через yt-dlp.
    ВАЖНО: payload под твой UI/handler:
      - videos[*] обязаны иметь: id, name, height, has_audio, total_bitrate, language_preference
      - thumbnails[*] обязаны иметь: id, url
      - audios[*] желательно иметь: id, name (на всякий)
    """

    def __init__(self, cookie_path: Optional[str] = None, proxy: Optional[str] = None):
        self.cookie_path = cookie_path
        self.proxy = proxy

    def extract_info(self, url: str):
        result = self._extract(url=url)
        return _ResultAdapter(result)

    def get_error_description(self, code: Any) -> str:
        return _safe_str(code, default="TWITTER_EXTRACT_ERROR")

    def _extract(self, url: str) -> Dict[str, Any]:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
        }

        if self.cookie_path:
            ydl_opts["cookiefile"] = self.cookie_path
        if self.proxy:
            ydl_opts["proxy"] = self.proxy

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            # Если вернулся список entries (тред/плейлист) — берём первый нормальный
            if isinstance(info, dict) and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if entries:
                    info = entries[0]

            title = _safe_str(info.get("title"), default="Twitter media")
            author_name = _safe_str(
                info.get("uploader") or info.get("creator") or info.get("uploader_id") or info.get("channel"),
                default="Unknown"
            )

            # ---------- thumbnails (обязательно id) ----------
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
                })

            if not thumbnails and info.get("thumbnail"):
                thumbnails.append({
                    "id": "thumb_0",
                    "url": info.get("thumbnail"),
                    "width": info.get("width"),
                    "height": info.get("height"),
                })

            # ---------- formats -> videos/audios ----------
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

                # в твоём parse_videos есть поля для сортировки
                total_bitrate = _safe_int(f.get("tbr"), 0)  # total bitrate
                language = _safe_str(f.get("language"), default="").lower()
                language_preference = _safe_int(f.get("language_preference"), 0)

                has_audio = bool(acodec and acodec != "none")

                if vcodec and vcodec != "none":
                    vid = fmt_id or f"{height}p" if height else "best"
                    videos.append({
                        "id": vid,                 # 🔥 нужно для callback_data video:{id}
                        "name": vid,               # 🔥 у тебя handler берёт video["name"]
                        "format_id": vid,          # на будущее/удобно
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

                # чистое аудио
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

            # fallback если formats пустые, но есть url
            if not videos and info.get("url"):
                videos.append({
                    "id": "best",
                    "name": "best",
                    "format_id": "best",
                    "ext": _safe_str(info.get("ext"), default="mp4"),
                    "url": info.get("url"),
                    "width": info.get("width"),
                    "height": info.get("height"),

                    "has_audio": True,
                    "language": None,
                    "language_preference": 0,
                    "total_bitrate": 0,
                })

            data = {
                "author_name": author_name,
                "title": title,
                "videos": videos,
                "audios": audios,
                "images": [],
                "thumbnails": thumbnails,
            }

            # страховка: id/name обязаны быть
            for i, v in enumerate(data["videos"]):
                v.setdefault("id", v.get("format_id") or f"video_{i}")
                v.setdefault("name", v.get("id"))
            for i, a in enumerate(data["audios"]):
                a.setdefault("id", a.get("format_id") or f"audio_{i}")
                a.setdefault("name", a.get("id"))
            for i, t in enumerate(data["thumbnails"]):
                t.setdefault("id", f"thumb_{i}")

            return {"status": "success", "code": 0, "context": None, "data": data}

        except DownloadError as e:
            logger.warning("TwitterExtractor DownloadError: %s", e)
            return {"status": "error", "code": "TWITTER_EXTRACT_ERROR", "context": str(e), "data": None}
        except Exception as e:
            logger.exception("TwitterExtractor Exception: %s", e)
            return {"status": "error", "code": "TWITTER_UNEXPECTED_ERROR", "context": str(e), "data": None}


class _ResultAdapter:
    def __init__(self, d: Dict[str, Any]):
        self._d = d

    def to_dict(self) -> Dict[str, Any]:
        return self._d
