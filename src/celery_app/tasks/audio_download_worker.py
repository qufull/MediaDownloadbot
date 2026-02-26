import logging
import json
import urllib.request
import urllib.parse
import urllib.error

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender

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

from src.utils.telegram_anim import send_waiting, send_error

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

    try:
        # Анимация ожидания (load.mp4)
        waiting_msg = await send_waiting(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_STARTED,
            reply_to_message_id=message_id,
        )

        async with ChatActionSender(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.RECORD_VOICE,
        ):
            if not direct:
                result = downloader.download_audio(
                    url=url,
                    audio_format_id=audio_id,
                    service=service,
                )
            else:
                result = downloader.download_direct_media(
                    url=url,
                    file_extension="mp3",
                )

        cache_url = original_url if original_url else url
        info = media_cache_storage.get_media(url=cache_url)

        # ── YouTube через massbots — отправляем file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            if result.status != "success" or not result.file_id:
                logger.error(
                    f"[async_download_audio] massbots error: {result.context}"
                )
                if waiting_msg:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
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
                    await send_vpn_ad(chat_id)
                    # Удаляем ожидание ПОСЛЕ успешной отправки
                    if waiting_msg:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
                        except Exception:
                            pass
                else:
                    logger.error(
                        f"[async_download_audio] send failed: {body}"
                    )
                    if waiting_msg:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
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
                if waiting_msg:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
                    except Exception:
                        pass
                await send_error(
                    chat_id=chat_id,
                    text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
                )
            return

        # ── Обычный путь (yt-dlp) для остальных сервисов ──
        await handle_download_result(
            result=result,
            media_info=info,
            chat_id=chat_id,
            service=service,
            message_id=waiting_msg.message_id if waiting_msg else None,
        )

        logger.info(
            f"[async_download_audio] Завершено для chat_id={chat_id}, "
            f"status={result.status}"
        )

    except Exception as e:
        logger.exception(
            f"[async_download_audio] Ошибка при скачивании аудио: {e}"
        )
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
    message_id: int | None,
    media_info: dict,
    result: AbstractResultModel,
):
    logger.info(
        f"[handle_download_result] Обработка результата: chat_id={chat_id}, "
        f"status={result.status}"
    )

    if result.status != "success":
        logger.error(
            f"[handle_download_result] Ошибка загрузки: status={result.status}, "
            f"chat_id={chat_id}"
        )
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_AUDIO_ERROR,
        )
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
                audio=FSInputFile(path=result.data.path),
            )
            logger.info(
                "[handle_download_result] Аудио успешно отправлено "
                f"(chat_id={chat_id})"
            )
        await send_vpn_ad(chat_id)

        # Удаляем ожидание ПОСЛЕ успешной отправки аудио
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
    except Exception as e:
        logger.exception(
            f"[handle_download_result] Ошибка при отправке аудио: {e}"
        )
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
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