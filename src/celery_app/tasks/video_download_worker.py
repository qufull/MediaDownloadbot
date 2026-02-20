# src/celery_app/tasks/video_download_worker.py
import logging
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

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

from src.utils.video_thumbnail import make_video_thumbnail
from src.utils.telegram_anim import send_waiting, send_error

logger = logging.getLogger(__name__)


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

        # Показать анимацию ожидания загрузки (load.mp4)
        waiting_msg = await send_waiting(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_STARTED,
            reply_to_message_id=message_id,
        )

        async with ChatActionSender(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.RECORD_VIDEO,
        ):
            result = downloader.download_video(
                url=url,
                merge_audio=merge_audio,
                video_format_id=video_id,
                service=service,
            )

        # ── YouTube через massbots — file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            # NB: YoutubeDownloadResult.status == "success" (не "ready")
            if result.status != "success" or not result.file_id:
                logger.error(
                    f"[async_download_video] massbots error: {result.context}"
                )
                if waiting_msg:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
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

            caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
                width=width or "?",
                height=height or "?",
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
                    # Кэшируем file_id
                    file_id_cache.store_file_id(
                        url=url,
                        height=height,
                        width=width,
                        file_id=result.file_id,
                    )
                    # Удаляем ожидание ПОСЛЕ успешной отправки видео
                    if waiting_msg:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
                        except Exception:
                            pass
                else:
                    logger.error(
                        f"[async_download_video] sendVideo response not ok: {resp}"
                    )
                    if waiting_msg:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
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
                if waiting_msg:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=waiting_msg.message_id)
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
            message_id=waiting_msg.message_id if waiting_msg else None,
        )
        logger.info(
            f"[async_download_video] Завершено: chat_id={chat_id}, "
            f"status={result.status}"
        )

    except Exception as e:
        logger.exception(f"[async_download_video] Ошибка при скачивании видео: {e}")
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

        caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
            width=width,
            height=height,
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
            await bot.send_video(**send_kwargs)
        logger.info("[send_cached_video] Видео отправлено из кэша")
    except Exception as e:
        logger.exception(f"[send_cached_video] Ошибка: {e}")
        file_id_cache.delete_cached(url=url, height=height)
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)


# ─── handle_download_result (yt-dlp path) ───────────────────────────

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
    logger.info(f"[handle_download_result] chat_id={chat_id}, status={result.status}")

    if result.status != "success":
        # Удаляем ожидание при ошибке
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )
        return

    if (
        not result.data
        or not getattr(result.data, "path", None)
        or not _is_nonempty_file(result.data.path)
    ):
        logger.error("[handle_download_result] success без файла")
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
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

    # Определяем реальные размеры через ffprobe (если экстрактор не определил)
    if not width or not height:
        from src.utils.video_thumbnail import get_video_dimensions
        probe_w, probe_h = get_video_dimensions(video_path)
        if probe_w and probe_h:
            width = probe_w
            height = probe_h
            logger.info(f"[handle_download_result] ffprobe dimensions: {width}x{height}")

    caption = MessageTemplates.DOWNLOAD_VIDEO_CAPTION.format(
        width=width or "?",
        height=height or "?",
        service=service,
        url=original_url,
        botname=settings.telegram.name,
        author_name=author_name,
    )

    thumb_file = None
    try:
        thumb_path = make_video_thumbnail(
            video_path=video_path,
            out_dir=str(Path(video_path).parent),
        )
        if thumb_path and thumb_path.exists():
            thumb_file = FSInputFile(str(thumb_path))
    except Exception as e:
        logger.warning(f"[handle_download_result] Thumbnail failed: {e}")

    try:
        async with ChatActionSender(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO,
        ):
            kwargs = dict(
                chat_id=chat_id,
                caption=caption,
                request_timeout=1200,
                supports_streaming=True,
                video=FSInputFile(path=video_path),
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
                file_id_cache.store_file_id(
                    url=url,
                    height=height,
                    width=width,
                    file_id=sent_message.video.file_id,
                )
                logger.info("[handle_download_result] file_id сохранён в кэш")

        # Удаляем сообщение ожидания
        if message_id:
            try:
                await bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"[handle_download_result] Ошибка отправки: {e}")
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_VIDEO_ERROR,
        )


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