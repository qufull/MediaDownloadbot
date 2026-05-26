import asyncio
import logging
import json
import threading
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
import concurrent.futures

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender

from celery.exceptions import SoftTimeLimitExceeded  # <-- ДОБАВЛЕН ИМПОРТ

from src.utils.ads import send_vpn_ad
from ..app import celery_app
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
from src.utils.telegram_anim import send_error, send_waiting
from src.celery_app.app import shared_loop

logger = logging.getLogger(__name__)


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


# ─── Telegram API helpers ──────────────────────────────────────────

def _send_video_via_telegram_api(
        bot_token: str,
        chat_id: int,
        file_id: str,
        caption: str = "",
        width: int = 0,
        height: int = 0,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    payload = {
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
        "supports_streaming": "true",
        "parse_mode": "HTML",
    }
    if width and width > 0: payload["width"] = width
    if height and height > 0: payload["height"] = height

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Error in _send_video_via_telegram_api: {e}")
        raise


# ─── File helpers ────────────────────────────────────────────────────

def _is_nonempty_file(path: str) -> bool:
    try:
        p = Path(path)
        return p.exists() and p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


def _get_standard_quality(width: int, height: int) -> int:
    w, h = (int(width) if width else 0), (int(height) if height else 0)
    raw_q = min(w, h) if w > 0 and h > 0 else (h or w)
    for std in [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]:
        if abs(raw_q - std) <= 50: return std
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
    try:
        is_fragment = "|" in video_id

        # 1. Проверка кэша (только если качаем видео целиком)
        if not is_fragment:
            cached_file_id = file_id_cache.get_file_id(url=url, height=height)
            if cached_file_id:
                await send_cached_video(url, width, height, chat_id, service, cached_file_id)
                return

        # 2. Инициализация уведомлений
        if service == "youtube":
            waiting_msg = await send_waiting(
                chat_id=chat_id,
                text=MessageTemplates.DOWNLOAD_STARTED,
                reply_to_message_id=message_id,
            )
        else:
            waiting_msg = await bot.send_message(
                chat_id=chat_id,
                text=MessageTemplates.DOWNLOAD_STARTED,
                reply_to_message_id=message_id,
            )

        # 3. Настройка прогресс-бара
        progress_state = {
            "percent": 0.0, "downloaded": 0, "total": 0, "speed": 0.0,
            "done": False, "lock": threading.Lock(),
        }

        def _progress_callback(percent, downloaded, total, speed):
            with progress_state["lock"]:
                progress_state["percent"] = percent
                progress_state["downloaded"] = downloaded
                progress_state["total"] = total
                progress_state["speed"] = float(speed or 0)
                if percent >= 99.9: progress_state["done"] = True

        progress_msg = await bot.send_message(
            chat_id=chat_id,
            text=_make_progress_caption(0.0, 0, 0, 0),
            parse_mode="HTML",
            reply_to_message_id=waiting_msg.message_id if waiting_msg else message_id,
        )

        async def _progress_updater():
            last_pct = -1.0
            while not progress_state["done"]:
                await asyncio.sleep(1.5)
                with progress_state["lock"]:
                    pct, d, t, spd = progress_state["percent"], progress_state["downloaded"], progress_state["total"], \
                        progress_state["speed"]

                if pct >= 99.9 or abs(pct - last_pct) >= 5.0:
                    last_pct = pct
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id, message_id=progress_msg.message_id,
                            text=_make_progress_caption(pct, d, t, spd), parse_mode="HTML"
                        )
                    except Exception:
                        pass

        # 4. Запуск скачивания
        updater_task = asyncio.create_task(_progress_updater())
        loop = asyncio.get_event_loop()

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: downloader.download_video(
                url=url, merge_audio=merge_audio,
                video_format_id=video_id, service=service,
                on_progress=_progress_callback
            )
        )

        progress_state["done"] = True
        await updater_task

        # 5. Обработка результата
        if isinstance(result, YoutubeDownloadResult):
            if result.status != "success" or not result.file_id:
                error_context = result.context if result.context else "Неизвестная ошибка Massbots"
                raise Exception(f"Massbots download failed: {error_context}")

            info = media_cache_storage.get_media(url=url) or {}
            caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
                width=width or "?", height=_get_standard_quality(width, height),
                service=service, url=url, botname=settings.telegram.name,
                author_name=info.get("data", {}).get("author_name", "Unknown")
            )

            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: _send_video_via_telegram_api(
                    settings.telegram.token, chat_id, result.file_id, caption, width, height
                )
            )

            if resp.get("ok"):
                media_rate_limiter.increment(chat_id, service=service)
                if not is_fragment:
                    file_id_cache.store_file_id(url=url, file_id=result.file_id, width=width, height=height)

                for m in [waiting_msg, progress_msg]:
                    if m: await bot.delete_message(chat_id, m.message_id)
                await send_vpn_ad(chat_id)
            else:
                raise Exception(f"API send failed: {resp}")
        else:
            await handle_download_result(
                url=url, width=width, height=height, result=result,
                media_info=media_cache_storage.get_media(url=url),
                chat_id=chat_id, service=service,
                message_ids=[waiting_msg.message_id, progress_msg.message_id],
                is_fragment=is_fragment
            )

    except Exception as e:
        logger.exception(f"Download error: {e}")
        await send_error(chat_id, MessageTemplates.DOWNLOAD_VIDEO_ERROR)
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


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
        is_fragment: bool = False
):
    logger.info(f"[handle_download_result] chat_id={chat_id}, status={result.status}")

    async def _cleanup():
        for mid in (message_ids or []):
            if mid:
                try:
                    await bot.delete_message(chat_id, mid)
                except Exception:
                    pass

    if result.status != "success" or not result.data or not getattr(result.data, "path", None) or not _is_nonempty_file(
            result.data.path):
        await _cleanup()
        await send_error(chat_id, MessageTemplates.DOWNLOAD_VIDEO_ERROR)
        return

    video_path = result.data.path
    caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
        width=width or "?", height=_get_standard_quality(width, height),
        service=service, url=url, botname=settings.telegram.name,
        author_name=media_info.get("data", {}).get("author_name", "Unknown") if media_info else "Unknown"
    )

    try:
        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO):
            thumb_path = make_video_thumbnail(video_path, str(Path(video_path).parent))
            sent_message = await bot.send_video(
                chat_id=chat_id, video=FSInputFile(video_path), caption=caption,
                width=width, height=height,
                thumbnail=FSInputFile(str(thumb_path)) if thumb_path else None,
                supports_streaming=True, request_timeout=1200
            )
            if sent_message.video:
                if not is_fragment:
                    file_id_cache.store_file_id(url=url, file_id=sent_message.video.file_id, width=width, height=height)
                media_rate_limiter.increment(chat_id, service=service)
                await send_vpn_ad(chat_id)

        await _cleanup()
    except Exception as e:
        logger.error(f"Send failed: {e}")
        await _cleanup()
        await send_error(chat_id, MessageTemplates.DOWNLOAD_VIDEO_ERROR)

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

            try:
                await bot.send_video(**send_kwargs)
            except Exception as bot_err:
                if "wrong file identifier" in str(bot_err).lower():
                    logger.warning("[send_cached_video] Локальный API не знает этот file_id. Пробуем через публичный API...")
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
                    raise bot_err

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
        # Запускаем задачу в фоновом цикле
        future = asyncio.run_coroutine_threadsafe(
            async_download_video(
                url=url,
                width=width,
                height=height,
                chat_id=chat_id,
                service=service,
                video_id=video_id,
                message_id=message_id,
                merge_audio=merge_audio,
            ),
            shared_loop
        )

        # === УСТАНАВЛИВАЕМ РАЗНЫЕ ТАЙМАУТЫ ===
        # Для YouTube: 180 секунд (3 минуты)
        # Для остальных: 1200 секунд (20 минут)
        timeout_limit = 180 if service == "youtube" else 1200

        # Поток будет ждать завершения задачи ровно timeout_limit секунд
        future.result(timeout=timeout_limit)

    except concurrent.futures.TimeoutError:
        # Если время вышло, принудительно отменяем задачу
        future.cancel()
        logger.error(f"[{service}] Превышен лимит времени ({timeout_limit} сек) для задачи: {url}")

        # Очищаем очередь пользователя
        user_activity_queue.delete_download(chat_id=chat_id)

        # Безопасно отправляем извинение пользователю
        asyncio.run_coroutine_threadsafe(
            bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Видео слишком тяжелое!</b>\nСкачивание заняло больше {timeout_limit // 60} минут и было прервано. Пожалуйста, выберите видео покороче.",
                parse_mode="HTML",
                reply_to_message_id=message_id
            ),
            shared_loop
        )

    except Exception as e:
        logger.exception(f"[{service}] Ошибка celery-задачи: {e}")
        user_activity_queue.delete_download(chat_id=chat_id)

@celery_app.task(name="download_twitter_video", queue="download_twitter_queue")
def download_twitter_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("twitter", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_youtube_video", queue="download_youtube_queue")
def download_youtube_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("youtube", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_rutube_video", queue="download_rutube_queue")
def download_rutube_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("rutube", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_reddit_video", queue="download_reddit_queue")
def download_reddit_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("reddit", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_tiktok_video", queue="download_tiktok_queue")
def download_tiktok_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("tiktok", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_instagram_video", queue="download_instagram_queue")
def download_instagram_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("instagram", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_vk_video", queue="download_vk_queue")
def download_vk_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("vk", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_pinterest_video", queue="download_pinterest_queue")
def download_pinterest_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("pinterest", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_vimeo_video", queue="download_generic_queue")
def download_vimeo_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("vimeo", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_dailymotion_video", queue="download_generic_queue")
def download_dailymotion_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("dailymotion", url, width, height, chat_id, video_id, message_id, merge_audio)


@celery_app.task(name="download_likee_video", queue="download_generic_queue")
def download_likee_video(url, width, height, chat_id, video_id, message_id, merge_audio):
    _run_video_task("likee", url, width, height, chat_id, video_id, message_id, merge_audio)

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