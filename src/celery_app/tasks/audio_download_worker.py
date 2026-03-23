import asyncio
import logging
import json
import threading
import time as _time
import urllib.request
import urllib.parse
import urllib.error

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender
from src.celery_app.tasks.video_download_worker import _video_input

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
from src.config import media_rate_limiter

from src.utils.telegram_anim import send_waiting, send_error
from .video_download_worker import _fmt_bytes, _make_progress_caption

logger = logging.getLogger(__name__)


async def async_download_audio(
    url: str,
    chat_id: int,
    service: str,
    audio_id: str,
    message_id: int,
    direct: bool = False,
    original_url: str = None,
) -> None:
    logger.info(
        f"[async_download_audio] Запуск: chat_id={chat_id}, "
        f"service={service}, url={url}, direct={direct}"
    )

    waiting_msg = None
    progress_msg = None
    try:
        waiting_msg = await send_waiting(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_STARTED,
            reply_to_message_id=message_id,
        )

        if not direct:
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
        else:
            loop = asyncio.get_event_loop()
            updater_task = None

        if not direct:
            result = await loop.run_in_executor(
                None,
                lambda: downloader.download_audio(
                    url=url,
                    audio_format_id=audio_id,
                    service=service,
                    on_progress=_progress_callback,
                ),
            )
        else:
            result = await loop.run_in_executor(
                None,
                lambda: downloader.download_direct_media(
                    url=url,
                    file_extension="mp3",
                ),
            )

        if updater_task:
            with progress_state["lock"]:
                progress_state["done"] = True
            try:
                await asyncio.wait_for(updater_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                updater_task.cancel()

        cache_url = original_url if original_url else url
        info = media_cache_storage.get_media(url=cache_url)

        # ── YouTube через massbots — отправляем file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            if result.status != "success" or not result.file_id:
                logger.error(
                    f"[async_download_audio] massbots error: {result.context}"
                )
                for m in (waiting_msg, progress_msg):
                    if m:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                        except Exception:
                            pass
                await send_error(
                    chat_id=chat_id,
                    text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
                )
                return

            if info and info.get("data"):
                audio_url = info["data"].get("url", "")
                author_name = info["data"].get("author_name", "Unknown")
            else:
                audio_url = url
                author_name = "Unknown"

            caption = MessageTemplates.DOWNLOAD_AUDIO_CAPTION.format(
                service=service,
                url=audio_url,
                botname=settings.telegram.name,
                author_name=author_name,
            )

            try:
                body = _send_massbots_audio(
                    bot_token=settings.telegram.token,
                    chat_id=chat_id,
                    file_id=result.file_id,
                    caption=caption,
                )
                if body.get("ok"):
                    logger.info(
                        "[async_download_audio] massbots audio sent via "
                        "api.telegram.org"
                    )
                    media_rate_limiter.increment(chat_id, service=service)
                    await send_vpn_ad(chat_id)
                    for m in (waiting_msg, progress_msg):
                        if m:
                            try:
                                await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                            except Exception:
                                pass
                else:
                    logger.error(
                        f"[async_download_audio] send failed: {body}"
                    )
                    for m in (waiting_msg, progress_msg):
                        if m:
                            try:
                                await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                            except Exception:
                                pass
                    await send_error(
                        chat_id=chat_id,
                        text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
                    )
            except Exception as e:
                logger.exception(
                    "[async_download_audio] send audio via api.telegram.org "
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
                    text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
                )
            return

        # ── Обычный путь (yt-dlp) для остальных сервисов ──
        msg_ids = [waiting_msg.message_id]
        if progress_msg:
            msg_ids.append(progress_msg.message_id)
        await handle_download_result(
            result=result,
            media_info=info,
            chat_id=chat_id,
            service=service,
            message_ids=msg_ids,
        )

        logger.info(
            f"[async_download_audio] Завершено для chat_id={chat_id}, "
            f"status={result.status}"
        )

    except Exception as e:
        logger.exception(
            f"[async_download_audio] Ошибка при скачивании аудио: {e}"
        )
        for m in (waiting_msg, progress_msg):
            if m:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=m.message_id)
                except Exception:
                    pass
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
        )
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


# ─── Telegram API: sendAudio с fallback на sendVideo ────────────────

def _send_massbots_audio(
    bot_token: str,
    chat_id: int,
    file_id: str,
    caption: str = "",
) -> dict:
    """
    Сначала пробует sendAudio (аудиоплеер в Telegram).
    Если 400 (massbots отдаёт видео-file_id) — fallback на sendVideo.
    """
    # --- попытка 1: sendAudio ---
    tg_url = f"https://api.telegram.org/bot{bot_token}/sendAudio"
    payload = {
        "chat_id": chat_id,
        "audio": file_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(tg_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        logger.info("sendAudio ok for chat_id=%s", chat_id)
        return body
    except urllib.error.HTTPError as he:
        error_body = ""
        try:
            error_body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.warning(
            "sendAudio HTTP %s: %s — fallback to sendVideo",
            he.code, error_body[:300],
        )

    # --- попытка 2: fallback sendVideo ---
    tg_url_fb = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    payload_fb = {
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    data_fb = urllib.parse.urlencode(payload_fb).encode("utf-8")
    req_fb = urllib.request.Request(tg_url_fb, data=data_fb, method="POST")
    req_fb.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req_fb, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    logger.info("sendVideo fallback ok for chat_id=%s", chat_id)
    return body


# ─── handle_download_result (yt-dlp path) ───────────────────────────

async def handle_download_result(
    chat_id: int,
    service: str,
    message_ids: list[int],
    media_info: dict,
    result: AbstractResultModel,
):
    logger.info(
        f"[handle_download_result] Обработка результата: chat_id={chat_id}, "
        f"status={result.status}"
    )

    async def _delete_waiting():
        for mid in (message_ids or []):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass

    if result.status != "success":
        logger.error(
            f"[handle_download_result] Ошибка загрузки: status={result.status}, "
            f"chat_id={chat_id}"
        )
        await _delete_waiting()
        ctx = getattr(result, "context", None)
        text = (ctx if ctx and len(str(ctx)) < 300 else MessageTemplates.DOWNLOAD_AUDIO_ERROR)
        await send_error(chat_id=chat_id, text=text)
        return

    logger.info(
        "[handle_download_result] Загрузка успешна, отправляю аудио"
    )

    if media_info and media_info.get("data"):
        url = media_info["data"].get("url", "")
        author_name = media_info["data"].get("author_name", "Unknown")
    else:
        url = ""
        author_name = "Unknown"

    caption = MessageTemplates.DOWNLOAD_AUDIO_CAPTION.format(
        service=service,
        url=url,
        botname=settings.telegram.name,
        author_name=author_name,
    )

    try:
        async with ChatActionSender(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VOICE,
        ):
            await bot.send_audio(
                chat_id=chat_id,
                caption=caption,
                audio=_video_input(result.data.path),
            )
            media_rate_limiter.increment(chat_id, service=service)
            logger.info(
                "[handle_download_result] Аудио успешно отправлено "
                f"(chat_id={chat_id})"
            )

        await send_vpn_ad(chat_id)
        await _delete_waiting()
    except Exception as e:
        logger.exception(
            f"[handle_download_result] Ошибка при отправке аудио: {e}"
        )
        await _delete_waiting()
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
        )


@celery_app.task(name="download_audio", queue="download_audio_queue")
def download_audio(
    url: str,
    chat_id: int,
    service: str,
    audio_id: str,
    message_id: int,
    direct: bool = False,
    original_url: str = None,
) -> None:
    logger.info(
        "[download_audio] Celery-задача запущена: "
        f"chat_id={chat_id}, service={service}, url={url}, direct={direct}"
    )

    try:
        celery_event_loop.run_until_complete(
            async_download_audio(
                url=url,
                chat_id=chat_id,
                service=service,
                audio_id=audio_id,
                message_id=message_id,
                direct=direct,
                original_url=original_url,
            )
        )
        logger.info(
            f"[download_audio] Celery-задача завершена: chat_id={chat_id}"
        )
    except Exception as e:
        logger.exception(
            f"[download_audio] Ошибка выполнения celery-задачи: {e}"
        )