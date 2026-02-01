# src/celery_app/tasks/download_video.py
import logging
import json
import urllib.request
import urllib.parse
import urllib.error

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender

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

logger = logging.getLogger(__name__)

from pathlib import Path
from aiogram.types import FSInputFile

from src.utils.video_thumbnail import make_video_thumbnail


def _send_video_via_telegram_api(
    bot_token: str,
    chat_id: int,
    file_id: str,
    caption: str = "",
    width: int = 0,
    height: int = 0,
) -> dict:
    """
    Отправляет видео через официальный api.telegram.org (не через локальный Bot API).
    Нужно потому что file_id от massbots привязан к обычному Telegram API,
    а локальный Bot API server использует свои file_id.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
        "width": width,
        "height": height,
        "supports_streaming": "true",
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def _send_audio_via_telegram_api(
    bot_token: str,
    chat_id: int,
    file_id: str,
    caption: str = "",
) -> dict:
    """Отправляет аудио (как видео) через api.telegram.org."""
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "video": file_id,
        "caption": caption,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def _is_nonempty_file(path: str) -> bool:
    try:
        p = Path(path)
        return p.exists() and p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


def _resolve_media(value: str):
    """file_id/url => str, path => FSInputFile"""
    p = Path(value)
    return FSInputFile(str(p)) if p.exists() else value


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
    logger.info(f"[async_download_video] Запуск: chat_id={chat_id}, service={service}, url={url}, height={height}p")

    try:
        # 🚀 Проверяем кэш file_id
        cached_file_id = file_id_cache.get_file_id(url=url, height=height)

        if cached_file_id:
            logger.info(f"[async_download_video] 🚀 Найден file_id в кэше для {height}p, отправляем напрямую")
            await send_cached_video(
                url=url,
                width=width,
                height=height,
                chat_id=chat_id,
                service=service,
                file_id=cached_file_id,
            )
            return

        # Нет в кэше - скачиваем как обычно
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        waiting_msg = None
        wa = "/app/src/assets/load.mp4"
        if wa:
            waiting_msg = await bot.send_animation(
                chat_id=chat_id,
                animation=_resolve_media(wa),
                caption=MessageTemplates.DOWNLOAD_STARTED,
            )
        else:
            waiting_msg = await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_STARTED)

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.RECORD_VIDEO):
            result = downloader.download_video(
                url=url,
                merge_audio=merge_audio,
                video_format_id=video_id,
                service=service,
            )

        # ── YouTube через massbots — отправляем file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            if waiting_msg:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
                except Exception:
                    pass

            if result.status != "success" or not result.file_id:
                ea = "/app/src/assets/Error.mp4"
                await bot.send_animation(
                    chat_id=chat_id,
                    animation=_resolve_media(ea),
                    caption=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
                )
                logger.error(f"[async_download_video] massbots error: {result.context}")
                return

            info = media_cache_storage.get_media(url=url)
            if info and info.get("data"):
                original_url = info["data"].get("url", "")
                author_name = info["data"].get("author_name", "Unknown")
            else:
                original_url = url
                author_name = "Unknown"

            caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
                width=width,
                height=height,
                service=service,
                url=original_url,
                botname=settings.telegram.name,
                author_name=author_name,
            )

            try:
                # Отправляем через api.telegram.org напрямую (не через локальный Bot API)
                resp = _send_video_via_telegram_api(
                    bot_token=settings.telegram.token,
                    chat_id=chat_id,
                    file_id=result.file_id,
                    caption=caption,
                    width=width,
                    height=height,
                )
                if resp.get("ok"):
                    logger.info(f"[async_download_video] massbots video sent via api.telegram.org")
                else:
                    logger.error(f"[async_download_video] sendVideo failed: {resp}")
                    await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)
            except Exception as e:
                logger.exception(f"[async_download_video] sendVideo via api.telegram.org failed: {e}")
                await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)

            return

        # ── Обычный путь (yt-dlp) для остальных сервисов ──
        info = media_cache_storage.get_media(url=url)

        await handle_download_result(
            url=url,
            width=width,
            height=height,
            result=result,
            media_info=info,
            chat_id=chat_id,
            service=service,
            message_id=waiting_msg.message_id if waiting_msg else None,
        )

        logger.info(f"[async_download_video] Завершено: chat_id={chat_id}, status={result.status}")


    except Exception as e:

        logger.exception(f"[async_download_video] Ошибка при скачивании видео: {e}")

        ea = "/app/src/assets/Error.mp4"

        if ea:

            await bot.send_animation(

                chat_id=chat_id,

                animation=_resolve_media(ea),

                caption=MessageTemplates.DOWNLOAD_VIDEO_ERROR,

            )

        else:

            await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


async def send_cached_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        service: str,
        file_id: str,
) -> None:
    """
    🚀 Отправка видео из кэша по file_id (мгновенно, без скачивания).
    """
    logger.info(f"[send_cached_video] 🚀 Отправка из кэша: chat_id={chat_id}, height={height}p")

    try:
        info = media_cache_storage.get_media(url=url)

        if info and info.get("data"):
            original_url = info["data"].get("url", "")
            author_name = info["data"].get("author_name", "Unknown")
        else:
            original_url = url
            author_name = "Unknown"

        caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
            width=width,
            height=height,
            service=service,
            url=original_url,
            botname=settings.telegram.name,
            author_name=author_name,
        )

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO):
            await bot.send_video(
                chat_id=chat_id,
                video=file_id,  # 🚀 Отправляем по file_id
                caption=caption,
                width=width,
                height=height,
                supports_streaming=True,
            )

        logger.info(f"[send_cached_video] 🚀 Видео отправлено из кэша: chat_id={chat_id}, height={height}p")

    except Exception as e:
        logger.exception(f"[send_cached_video] Ошибка отправки из кэша: {e}")
        # Если file_id протух - удаляем из кэша
        file_id_cache.delete_cached(url=url, height=height)
        # Fallback: скачиваем заново (рекурсия не нужна, просто сообщаем об ошибке)
        await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


async def handle_download_result(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        service: str,
        message_id: int | None,
        media_info: dict,
        result: AbstractResultModel,
):
    logger.info(
        f"[handle_download_result] Обработка результата: chat_id={chat_id}, status={result.status}, service={service}")

    if message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.warning(f"[handle_download_result] Не удалось удалить waiting message {message_id}: {e}")

    if result.status != "success":
        ea = "/app/src/assets/Error.mp4"
        if ea:
            await bot.send_animation(
                chat_id=chat_id,
                animation=_resolve_media(ea),
                caption=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
            )
        else:
            await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)
        return

    if not result.data or not getattr(result.data, "path", None) or not _is_nonempty_file(result.data.path):
        logger.error(
            "[handle_download_result] success без файла: path=%s chat_id=%s service=%s",
            getattr(getattr(result, "data", None), "path", None),
            chat_id,
            service,
        )
        ea = "/app/src/assets/Error.mp4"
        if ea:
            await bot.send_animation(
                chat_id=chat_id,
                animation=_resolve_media(ea),
                caption=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
            )
        else:
            await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)
        return

    sending_msg = await bot.send_message(chat_id=chat_id, text=MessageTemplates.SENDING_VIDEO)

    if media_info and media_info.get("data"):
        original_url = media_info["data"].get("url", "")
        author_name = media_info["data"].get("author_name", "Unknown")
    else:
        original_url = ""
        author_name = "Unknown"

    caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
        width=width,
        height=height,
        service=service,
        url=original_url,
        botname=settings.telegram.name,
        author_name=author_name,
    )

    video_path = result.data.path

    thumb_file = None
    try:
        thumb_path = make_video_thumbnail(video_path=video_path, out_dir=str(Path(video_path).parent))
        if thumb_path and thumb_path.exists():
            thumb_file = FSInputFile(str(thumb_path))
    except Exception as e:
        logger.warning(f"[handle_download_result] Не смог сделать thumbnail: {e}")

    try:
        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO):
            kwargs = dict(
                width=width,
                height=height,
                chat_id=chat_id,
                caption=caption,
                request_timeout=1200,
                supports_streaming=True,
                video=FSInputFile(path=video_path),
            )

            if thumb_file:
                kwargs["thumbnail"] = thumb_file

            try:
                sent_message = await bot.send_video(**kwargs)

                # 🚀 Сохраняем file_id в кэш после успешной отправки
                if sent_message and sent_message.video:
                    file_id = sent_message.video.file_id
                    file_id_cache.store_file_id(
                        url=url,
                        height=height,
                        width=width,
                        file_id=file_id,
                    )
                    logger.info(f"[handle_download_result] 🚀 file_id сохранён в кэш: height={height}p")

            except TypeError:
                kwargs.pop("thumbnail", None)
                sent_message = await bot.send_video(**kwargs)

                # 🚀 Сохраняем file_id даже при fallback
                if sent_message and sent_message.video:
                    file_id = sent_message.video.file_id
                    file_id_cache.store_file_id(
                        url=url,
                        height=height,
                        width=width,
                        file_id=file_id,
                    )

        try:
            await bot.delete_message(chat_id=chat_id, message_id=sending_msg.message_id)
        except Exception as e:
            logger.warning(f"[handle_download_result] Не удалось удалить SENDING_VIDEO message: {e}")

    except Exception as e:
        logger.exception(f"[handle_download_result] Ошибка при отправке видео: {e}")
        await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_VIDEO_ERROR)


# ======================= CELERY TASKS =======================

@celery_app.task(name="download_twitter_video", queue="download_twitter_queue")
def download_twitter_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    _run_video_task(
        service="twitter",
        url=url,
        width=width,
        height=height,
        chat_id=chat_id,
        video_id=video_id,
        message_id=message_id,
        merge_audio=merge_audio,
    )


@celery_app.task(name="download_youtube_video", queue="download_youtube_queue")
def download_youtube_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    _run_video_task(
        service="youtube",
        url=url,
        width=width,
        height=height,
        chat_id=chat_id,
        video_id=video_id,
        message_id=message_id,
        merge_audio=merge_audio,
    )


@celery_app.task(name="download_rutube_video", queue="download_rutube_queue")
def download_rutube_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    _run_video_task(
        service="rutube",
        url=url,
        width=width,
        height=height,
        chat_id=chat_id,
        video_id=video_id,
        message_id=message_id,
        merge_audio=merge_audio,
    )


@celery_app.task(name="download_reddit_video", queue="download_reddit_queue")
def download_reddit_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    _run_video_task(
        service="reddit",
        url=url,
        width=width,
        height=height,
        chat_id=chat_id,
        video_id=video_id,
        message_id=message_id,
        merge_audio=merge_audio,
    )


@celery_app.task(name="download_tiktok_video", queue="download_tiktok_queue")
def download_tiktok_video(
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    _run_video_task(
        service="tiktok",
        url=url,
        width=width,
        height=height,
        chat_id=chat_id,
        video_id=video_id,
        message_id=message_id,
        merge_audio=merge_audio,
    )


# ======================= ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ =======================

def _run_video_task(
        service: str,
        url: str,
        width: int,
        height: int,
        chat_id: int,
        video_id: str,
        message_id: int,
        merge_audio: bool,
) -> None:
    logger.info(f"[{service}] Celery-задача запущена: chat_id={chat_id}, url={url}, merge_audio={merge_audio}")
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
        logger.info(f"[{service}] Celery-задача успешно завершена (chat_id={chat_id})")
    except Exception as e:
        logger.exception(f"[{service}] Ошибка выполнения celery-задачи: {e}")