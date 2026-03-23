# src/celery_app/tasks/video_download_worker.py
import asyncio
import logging
import json
import threading
import time as _time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.ads import send_vpn_ad
from ..app import celery_app
from ..app import celery_event_loop
from .texts import MessageTemplates

from src.config import bot
from src.config import settings
from src.config import downloader
from src.core import AbstractResultModel
from src.core.downloader import YoutubeDownloadResult
from src.config import user_activity_queue
from src.config import media_cache_storage
from src.config import file_id_cache
from src.config import media_rate_limiter

from src.utils.video_thumbnail import make_video_thumbnail
from src.utils.telegram_anim import send_waiting, send_error

logger = logging.getLogger(__name__)

import redis as _redis_sync

# Файлы < DELIVERY_ASK_THRESHOLD → спрашиваем куда отправить
# Файлы ≥ DELIVERY_ASK_THRESHOLD → автоматически через Drive ссылку
DELIVERY_ASK_THRESHOLD = 1 * 1024 * 1024 * 1024       # 1 ГБ
TELEGRAM_VIDEO_LIMIT_BYTES = 2 * 1024 * 1024 * 1024   # 2 ГБ (локальный Bot API)


class _DeliveryStore:
    """
    Временное хранилище информации о скачанном файле, пока пользователь выбирает
    куда отправить (Telegram / Drive). TTL = 15 мин.
    """
    TTL = 900  # секунд

    def __init__(self):
        self._client: _redis_sync.Redis | None = None

    def _get(self) -> _redis_sync.Redis:
        if self._client is None:
            from src.settings import AppSettings
            s = AppSettings()
            self._client = _redis_sync.Redis(
                host=s.redis.host, port=s.redis.port,
                db=s.redis.user_session_db,
                decode_responses=True,
            )
        return self._client

    def _key(self, chat_id: int) -> str:
        return f"pending_delivery:{chat_id}"

    def save(self, chat_id: int, data: dict) -> None:
        import json
        self._get().setex(self._key(chat_id), self.TTL, json.dumps(data, default=str))

    def get(self, chat_id: int) -> dict | None:
        import json
        raw = self._get().get(self._key(chat_id))
        return json.loads(raw) if raw else None

    def delete(self, chat_id: int) -> None:
        self._get().delete(self._key(chat_id))


delivery_store = _DeliveryStore()


# ─── Progress bar helpers ────────────────────────────────────────────

def _fmt_bytes(b: int) -> str:
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} ГБ"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} МБ"
    if b >= 1 << 10:
        return f"{b / (1 << 10):.1f} КБ"
    return f"{b} Б"


def _make_progress_caption(percent: float, downloaded: int, total: int, speed: float) -> str:
    filled = max(0, min(20, round(percent / 5)))
    bar = "█" * filled + "░" * (20 - filled)
    text = (
        f"⬇️ <b>Загрузка...</b>\n\n"
        f"<code>[{bar}]</code>  <b>{percent:.1f}%</b>"
    )
    if total > 0:
        text += f"\n📦 {_fmt_bytes(downloaded)} / {_fmt_bytes(total)}"
    if speed > 0:
        text += f"\n⚡️ {_fmt_bytes(int(speed))}/с"
    return text


# ─── Telegram API helpers (обход локального Bot API server) ──────────

def _send_video_via_telegram_api(
    bot_token: str,
    chat_id: int,
    file_id: str,
    caption: str = "",
    width: int = 0,
    height: int = 0,
) -> dict:
    """
    Отправляет видео через api.telegram.org напрямую.
    Нужно потому что file_id от massbots привязан к обычному Telegram API,
    а локальный Bot API server использует несовместимые file_id.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    logger.info(
        "sendVideo via api.telegram.org: chat_id=%s file_id=%s... w=%s h=%s",
        chat_id,
        file_id[:40] if file_id else "None",
        width,
        height,
    )

    payload = {
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
        "supports_streaming": "true",
        "parse_mode": "HTML",
    }
    if width and width > 0:
        payload["width"] = width
    if height and height > 0:
        payload["height"] = height

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as he:
        error_body = ""
        try:
            error_body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.error("sendVideo HTTP %s: %s", he.code, error_body[:500])
        raise


# ─── File helpers ────────────────────────────────────────────────────

def _is_nonempty_file(path: str) -> bool:
    try:
        p = Path(path)
        return p.exists() and p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


def _resolve_media(value: str):
    p = Path(value)
    return FSInputFile(str(p)) if p.exists() else value


def _video_input(path: str):
    """
    Возвращает file:// URL для local Bot API (сервер читает файл прямо с диска,
    без HTTP-передачи внутри Docker) или FSInputFile как fallback для облачного API.
    """
    server_url = getattr(settings.telegram, "server_url", "https://api.telegram.org")
    if "api.telegram.org" not in server_url:
        return f"file://{path}"
    return FSInputFile(path)


def _get_standard_quality(width: int, height: int) -> int:
    """Превращает любые кривые размеры в красивый стандарт (1080, 720, 480)."""
    w = int(width) if width else 0
    h = int(height) if height else 0

    if w > 0 and h > 0:
        raw_q = min(w, h)
    elif h > 0:
        mapping = {1920: 1080, 1280: 720, 854: 480, 640: 360, 2560: 1440, 3840: 2160}
        raw_q = mapping.get(h, h)
    else:
        raw_q = w

    for std in [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]:
        if abs(raw_q - std) <= 50:
            return std
    return raw_q

# ─── Main download function ─────────────────────────────────────────

async def async_download_video(
    url: str,
    width: int,
    height: int,
    chat_id: int,
    service: str,
    video_id: str,
    message_id: int,
    merge_audio: bool,
) -> None:
    logger.info(
        f"[async_download_video] Запуск: chat_id={chat_id}, "
        f"service={service}, url={url}, height={height}p"
    )

    try:
        # Проверяем кэш file_id
        cached_file_id = file_id_cache.get_file_id(url=url, height=height)
        if cached_file_id:
            logger.info("[async_download_video] Найден file_id в кэше")
            await send_cached_video(
                url=url,
                width=width,
                height=height,
                chat_id=chat_id,
                service=service,
                file_id=cached_file_id,
            )
            return

        waiting_msg = None
        progress_msg = None
        # Показать анимацию ожидания загрузки (load.mp4)
        waiting_msg = await send_waiting(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_STARTED,
            reply_to_message_id=message_id,
        )

        # ── Прогресс-бар: отдельное текстовое сообщение (edit_message_text надёжнее) ──
        progress_state = {
            "percent": 0.0,
            "downloaded": 0,
            "total": 0,
            "speed": 0.0,
            "done": False,
            "lock": threading.Lock(),
        }

        def _progress_callback(percent: float, downloaded: int, total: int, speed: float) -> None:
            with progress_state["lock"]:
                progress_state["percent"] = percent
                progress_state["downloaded"] = downloaded
                progress_state["total"] = total
                progress_state["speed"] = float(speed or 0)
                if percent >= 99.9:
                    progress_state["done"] = True

        # Отдельное сообщение для прогресса — edit_message_text работает стабильно
        progress_msg = await bot.send_message(
            chat_id=chat_id,
            text=_make_progress_caption(0.0, 0, 0, 0),
            parse_mode="HTML",
            reply_to_message_id=waiting_msg.message_id if waiting_msg else message_id,
        )

        async def _progress_updater() -> None:
            last_pct = -1.0
            while True:
                await asyncio.sleep(1.5)
                with progress_state["lock"]:
                    pct = progress_state["percent"]
                    d = progress_state["downloaded"]
                    t = progress_state["total"]
                    spd = progress_state["speed"]
                    done = progress_state["done"]
                # Обновляем при изменении ≥5% или при 100%
                if pct >= 99.9 or abs(pct - last_pct) >= 5.0 or last_pct < 0:
                    last_pct = pct
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=progress_msg.message_id,
                            text=_make_progress_caption(pct, d, t, spd),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                if done:
                    break

        loop = asyncio.get_event_loop()
        updater_task = asyncio.create_task(_progress_updater())

        result = await loop.run_in_executor(
            None,
            lambda: downloader.download_video(
                url=url,
                merge_audio=merge_audio,
                video_format_id=video_id,
                service=service,
                on_progress=_progress_callback,
            ),
        )

        logger.info(
            "[async_download_video] download done: service=%s chat_id=%s status=%s path=%s",
            service, chat_id, getattr(result, "status", "?"),
            getattr(getattr(result, "data", None), "path", None),
        )

        with progress_state["lock"]:
            progress_state["done"] = True
        try:
            await asyncio.wait_for(updater_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            updater_task.cancel()

        # ── YouTube через massbots — file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            # NB: YoutubeDownloadResult.status == "success" (не "ready")
            if result.status != "success" or not result.file_id:
                logger.error(
                    f"[async_download_video] massbots error: {result.context}"
                )
                for m in (waiting_msg, progress_msg):
                    if m:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                        except Exception:
                            pass
                await send_error(
                    chat_id=chat_id,
                    text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
                )
                return

            # Собираем caption
            info = media_cache_storage.get_media(url=url)
            if info and info.get("data"):
                original_url = info["data"].get("url", "")
                author_name = info["data"].get("author_name", "Unknown")
            else:
                original_url = url
                author_name = "Unknown"

            display_quality = _get_standard_quality(width, height)
            caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
                width=width or "?",
                height=display_quality or "?",
                service=service,
                url=original_url,
                botname=settings.telegram.name,
                author_name=author_name,
            )

            try:
                resp = _send_video_via_telegram_api(
                    bot_token=settings.telegram.token,
                    chat_id=chat_id,
                    file_id=result.file_id,
                    caption=caption,
                    width=width or 0,
                    height=height or 0,
                )
                if resp.get("ok"):
                    logger.info(
                        "[async_download_video] massbots video "
                        "sent via api.telegram.org"
                    )
                    media_rate_limiter.increment(chat_id, service=service)
                    # Кэшируем file_id
                    file_id_cache.store_file_id(
                        url=url,
                        height=height,
                        width=width,
                        file_id=result.file_id,
                    )
                    # Удаляем ожидание ПОСЛЕ успешной отправки видео
                    for m in (waiting_msg, progress_msg):
                        if m:
                            try:
                                await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                            except Exception:
                                pass
                    await send_vpn_ad(chat_id)
                else:
                    logger.error(
                        f"[async_download_video] sendVideo response not ok: {resp}"
                    )
                    for m in (waiting_msg, progress_msg):
                        if m:
                            try:
                                await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                            except Exception:
                                pass
                    await send_error(
                        chat_id=chat_id,
                        text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
                    )
            except Exception as e:
                logger.exception(
                    f"[async_download_video] sendVideo via api.telegram.org "
                    f"failed: {e}"
                )
                for m in (waiting_msg, progress_msg):
                    if m:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                        except Exception:
                            pass
                await send_error(
                    chat_id=chat_id,
                    text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
                )
            return

        # ── Обычные сервисы (yt-dlp) ──
        info = media_cache_storage.get_media(url=url)
        await handle_download_result(
            url=url,
            width=width,
            height=height,
            result=result,
            media_info=info,
            chat_id=chat_id,
            service=service,
            message_ids=[waiting_msg.message_id, progress_msg.message_id] if waiting_msg and progress_msg else None,
        )
        logger.info(
            f"[async_download_video] Завершено: chat_id={chat_id}, "
            f"status={result.status}"
        )

    except Exception as e:
        logger.exception(f"[async_download_video] Ошибка при скачивании видео: {e}")
        for m in (waiting_msg, progress_msg):
            if m:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                except Exception:
                    pass
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


# ─── Cached video ───────────────────────────────────────────────────

async def send_cached_video(
    url: str,
    width: int,
    height: int,
    chat_id: int,
    service: str,
    file_id: str,
) -> None:
    logger.info(
        f"[send_cached_video] Отправка из кэша: chat_id={chat_id}, height={height}p"
    )
    try:
        info = media_cache_storage.get_media(url=url)
        if info and info.get("data"):
            original_url = info["data"].get("url", "")
            author_name = info["data"].get("author_name", "Unknown")
        else:
            original_url = url
            author_name = "Unknown"

        display_quality = _get_standard_quality(width, height)
        caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
            width=width or "?",
            height=display_quality or "?",
            service=service,
            url=original_url,
            botname=settings.telegram.name,
            author_name=author_name,
        )

        async with ChatActionSender(
                bot=bot,
                chat_id=chat_id,
                action=ChatAction.UPLOAD_VIDEO,
        ):
            send_kwargs = dict(
                chat_id=chat_id,
                video=file_id,
                caption=caption,
                supports_streaming=True,
            )
            if width and width > 0:
                send_kwargs["width"] = width
            if height and height > 0:
                send_kwargs["height"] = height

            # --- ИСПРАВЛЕНИЕ ОШИБКИ КЭША ---
            try:
                # Пытаемся отправить через локальный сервер (для TikTok, IG, ВК и т.д.)
                await bot.send_video(**send_kwargs)
            except Exception as bot_err:
                if "wrong file identifier" in str(bot_err).lower():
                    logger.warning(
                        "[send_cached_video] Локальный API не знает этот file_id. Пробуем через публичный API...")
                    # Отправляем через публичный API (для закэшированного YouTube)
                    resp = _send_video_via_telegram_api(
                        bot_token=settings.telegram.token,
                        chat_id=chat_id,
                        file_id=file_id,
                        caption=caption,
                        width=width or 0,
                        height=height or 0,
                    )
                    if not resp.get("ok"):
                        raise Exception(f"Ошибка публичного API при отправке кэша: {resp}")
                else:
                    # Если ошибка другая (например, юзер заблокировал бота), прокидываем её дальше
                    raise bot_err
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

            media_rate_limiter.increment(chat_id, service=service)

        logger.info("[send_cached_video] Видео отправлено из кэша")
        await send_vpn_ad(chat_id)
    except Exception as e:
        logger.exception(f"[send_cached_video] Ошибка: {e}")
        file_id_cache.delete_cached(url=url, height=height)
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


# ─── Delivery helpers ───────────────────────────────────────────────

async def _deliver_via_drive(
    chat_id: int,
    video_path: str,
    file_size: int,
    service: str,
    delete_waiting=None,
) -> None:
    """Загружает файл на Google Drive и отправляет ссылку пользователю."""
    try:
        from src.integrations.drive_storage import upload_file_to_drive, is_drive_configured
        if is_drive_configured():
            logger.info("[deliver_drive] Uploading to Drive: %s", video_path)
            info = upload_file_to_drive(video_path)
            link = info.get("webContentLink") or info.get("webViewLink") or ""
            if link:
                size_str = _fmt_bytes(file_size)
                text = MessageTemplates.DOWNLOAD_VIDEO_DRIVE_LINK.format(size=size_str, link=link)
                if delete_waiting:
                    await delete_waiting()
                await bot.send_message(chat_id=chat_id, text=text)
                media_rate_limiter.increment(chat_id, service=service)
                logger.info("[deliver_drive] Drive link sent: %s", link[:50])

                # Удаляем файл с Drive сразу после отправки ссылки
                drive_file_id = info.get("id")
                if drive_file_id:
                    try:
                        from src.integrations.drive_storage import delete_drive_file
                        delete_drive_file(drive_file_id)
                        logger.info("[deliver_drive] Drive file deleted: %s", drive_file_id)
                    except Exception as del_err:
                        logger.warning("[deliver_drive] Could not delete Drive file: %s", del_err)

                # Удаляем локальный файл
                try:
                    Path(video_path).unlink(missing_ok=True)
                    logger.info("[deliver_drive] Local file deleted: %s", video_path)
                except Exception as del_err:
                    logger.warning("[deliver_drive] Could not delete local file: %s", del_err)

                return
            logger.warning("[deliver_drive] Drive ok but no link")
        else:
            logger.warning("[deliver_drive] Drive not configured")
    except Exception as e:
        logger.warning("[deliver_drive] Failed: %s", e, exc_info=True)

    if delete_waiting:
        await delete_waiting()
    await send_error(
        chat_id=chat_id,
        text="Не удалось загрузить файл на Google Drive. Попробуйте ещё раз или выберите меньшее качество.",
    )


async def deliver_to_telegram(
    chat_id: int,
    video_path: str,
    file_size: int,
    url: str,
    service: str,
    width: int,
    height: int,
    original_url: str = "",
    author_name: str = "Unknown",
    ask_message_id: int | None = None,
) -> None:
    """Отправляет видео напрямую в Telegram чат."""
    if ask_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=ask_message_id)
        except Exception:
            pass

    if not width or not height:
        from src.utils.video_thumbnail import get_video_dimensions
        probe_w, probe_h = get_video_dimensions(video_path)
        if probe_w and probe_h:
            width, height = probe_w, probe_h

    display_quality = _get_standard_quality(width, height)
    caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
        width=width or "?",
        height=display_quality or "?",
        service=service,
        url=original_url or url,
        botname=settings.telegram.name,
        author_name=author_name,
    )

    thumb_file = None
    try:
        from src.utils.video_thumbnail import make_video_thumbnail
        thumb_path = make_video_thumbnail(
            video_path=video_path,
            out_dir=str(Path(video_path).parent),
        )
        if thumb_path and thumb_path.exists():
            thumb_file = _video_input(str(thumb_path))
    except Exception as e:
        logger.warning("[deliver_tg] Thumbnail failed: %s", e)

    try:
        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO):
            kwargs = dict(
                chat_id=chat_id,
                caption=caption,
                request_timeout=1200,
                supports_streaming=True,
                video=_video_input(video_path),
            )
            if width and width > 0:
                kwargs["width"] = width
            if height and height > 0:
                kwargs["height"] = height
            if thumb_file:
                kwargs["thumbnail"] = thumb_file

            try:
                sent_message = await bot.send_video(**kwargs)
            except TypeError:
                kwargs.pop("thumbnail", None)
                sent_message = await bot.send_video(**kwargs)

            if sent_message and sent_message.video:
                media_rate_limiter.increment(chat_id, service=service)
                file_id_cache.store_file_id(
                    url=url,
                    height=height,
                    width=width,
                    file_id=sent_message.video.file_id,
                )
                await send_vpn_ad(chat_id)
                logger.info("[deliver_tg] Video sent, file_id cached")

            # Удаляем локальный файл после успешной отправки
            try:
                Path(video_path).unlink(missing_ok=True)
                logger.info("[deliver_tg] Local file deleted: %s", video_path)
            except Exception as del_err:
                logger.warning("[deliver_tg] Could not delete local file: %s", del_err)
    except Exception as e:
        logger.exception("[deliver_tg] Send failed: %s", e)
        await send_error(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)


# ─── handle_download_result (yt-dlp path) ───────────────────────────

async def handle_download_result(
    url: str,
    width: int,
    height: int,
    chat_id: int,
    service: str,
    message_ids: list[int] | None,
    media_info: dict,
    result: AbstractResultModel,
):
    logger.info(f"[handle_download_result] chat_id={chat_id}, status={result.status}")

    async def _delete_waiting():
        for mid in (message_ids or []):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass

    if result.status != "success":
        await _delete_waiting()
        ctx = getattr(result, "context", None)
        text = (ctx if ctx and len(str(ctx)) < 300 else MessageTemplates.DOWNLOAD_VIDEO_ERROR)
        await send_error(chat_id=chat_id, text=text)
        return

    if (
        not result.data
        or not getattr(result.data, "path", None)
        or not _is_nonempty_file(result.data.path)
    ):
        logger.error("[handle_download_result] success без файла")
        await _delete_waiting()
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )
        return

    if media_info and media_info.get("data"):
        original_url = media_info["data"].get("url", "")
        author_name = media_info["data"].get("author_name", "Unknown")
    else:
        original_url = ""
        author_name = "Unknown"

    video_path = result.data.path
    file_size = Path(video_path).stat().st_size

    logger.info(
        "[handle_download_result] file_size=%s (%s)",
        file_size, _fmt_bytes(file_size),
    )

    # ≥ 1 ГБ → автоматически Drive ссылка, без вопросов
    if file_size >= DELIVERY_ASK_THRESHOLD:
        await _deliver_via_drive(
            chat_id=chat_id,
            video_path=video_path,
            file_size=file_size,
            service=service,
            delete_waiting=_delete_waiting,
        )
        return

    # < 1 ГБ → спрашиваем пользователя куда отправить
    from src.bot.keyboards import get_delivery_keyboard
    delivery_store.save(chat_id, {
        "video_path": video_path,
        "file_size": file_size,
        "url": url,
        "service": service,
        "width": width,
        "height": height,
        "original_url": original_url,
        "author_name": author_name,
    })
    size_str = _fmt_bytes(file_size)
    await _delete_waiting()
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📦 <b>Видео готово ({size_str})</b>\n\n"
            f"Куда отправить?\n"
            f"<i>💡 Рекомендуем по ссылке — быстрее и без ограничений Telegram</i>"
        ),
        parse_mode="HTML",
        reply_markup=get_delivery_keyboard(),
    )
    return


# ─── Celery tasks ───────────────────────────────────────────────────

def _run_video_task(
    service,
    url,
    width,
    height,
    chat_id,
    video_id,
    message_id,
    merge_audio,
):
    logger.info(f"[{service}] Celery-задача: chat_id={chat_id}, url={url}")
    try:
        celery_event_loop.run_until_complete(
            async_download_video(
                url=url,
                width=width,
                height=height,
                chat_id=chat_id,
                service=service,
                video_id=video_id,
                message_id=message_id,
                merge_audio=merge_audio,
            )
        )
    except Exception as e:
        logger.exception(f"[{service}] Ошибка celery-задачи: {e}")


@celery_app.task(name="download_twitter_video", queue="download_twitter_queue")
def download_twitter_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "twitter",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_youtube_video", queue="download_youtube_queue")
def download_youtube_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "youtube",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_rutube_video", queue="download_rutube_queue")
def download_rutube_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "rutube",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_reddit_video", queue="download_reddit_queue")
def download_reddit_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "reddit",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_tiktok_video", queue="download_tiktok_queue")
def download_tiktok_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "tiktok",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_instagram_video", queue="download_instagram_queue")
def download_instagram_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "instagram",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


@celery_app.task(name="download_vk_video", queue="download_vk_queue")
def download_vk_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "vk",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )
@celery_app.task(name="download_pinterest_video", queue="download_pinterest_queue")
def download_pinterest_video(
    url, width, height, chat_id, video_id, message_id, merge_audio
):
    _run_video_task(
        "pinterest",
        url,
        width,
        height,
        chat_id,
        video_id,
        message_id,
        merge_audio,
    )


# ─── Новые платформы (generic yt-dlp queue) ─────────────────────────

@celery_app.task(name="download_vimeo_video", queue="download_generic_queue")
def download_vimeo_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("vimeo", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_dailymotion_video", queue="download_generic_queue")
def download_dailymotion_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("dailymotion", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_facebook_video", queue="download_generic_queue")
def download_facebook_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("facebook", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_okru_video", queue="download_generic_queue")
def download_okru_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("okru", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_twitch_video", queue="download_generic_queue")
def download_twitch_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("twitch", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_kick_video", queue="download_generic_queue")
def download_kick_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("kick", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_rumble_video", queue="download_generic_queue")
def download_rumble_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("rumble", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_coub_video", queue="download_generic_queue")
def download_coub_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("coub", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_soundcloud_video", queue="download_generic_queue")
def download_soundcloud_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("soundcloud", url, width, height, chat_id, video_id, message_id, merge_audio)