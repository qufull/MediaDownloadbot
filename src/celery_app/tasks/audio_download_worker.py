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

logger = logging.getLogger(__name__)


async def async_download_audio(
        url: str,
        chat_id: int,
        service: str,
        audio_id: str,
        message_id: int,
        direct: bool = False,
        original_url: str = None,  # URL для получения media_info из кеша
) -> None:
    logger.info(f"[async_download_audio] Запуск: chat_id={chat_id}, service={service}, url={url}, direct={direct}")

    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = await bot.send_message(
            chat_id=chat_id,
            text=MessageTemplates.DOWNLOAD_STARTED
        )
        logger.debug(f"[async_download_audio] Сообщение о начале загрузки отправлено: message_id={message.message_id}")

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.RECORD_VOICE):
            if not direct:
                logger.info(
                    f"[async_download_audio] Скачивание аудио через downloader.download_audio() (audio_id={audio_id})")
                result = downloader.download_audio(
                    url=url,
                    audio_format_id=audio_id,
                    service=service,
                )
            else:
                logger.info("[async_download_audio] Скачивание аудио напрямую (download_direct_media)")
                result = downloader.download_direct_media(
                    url=url,
                    file_extension="mp3",
                )

        # Используем original_url для получения media_info, если он передан
        cache_url = original_url if original_url else url
        info = media_cache_storage.get_media(url=cache_url)
        logger.debug(f"[async_download_audio] Информация из кеша получена: {info}")

        # ── YouTube через massbots — отправляем file_id через api.telegram.org ──
        if isinstance(result, YoutubeDownloadResult):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            except Exception:
                pass

            if result.status != "success" or not result.file_id:
                await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)
                logger.error(f"[async_download_audio] massbots error: {result.context}")
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
                tg_url = f"https://api.telegram.org/bot{settings.telegram.token}/sendVideo"
                data = urllib.parse.urlencode({
                    "chat_id": chat_id,
                    "video": result.file_id,
                    "caption": caption,
                }).encode("utf-8")
                req = urllib.request.Request(tg_url, data=data, method="POST")
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok"):
                    logger.info("[async_download_audio] massbots audio sent via api.telegram.org")
                else:
                    logger.error(f"[async_download_audio] sendVideo failed: {body}")
                    await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)
            except Exception as e:
                logger.exception(f"[async_download_audio] sendVideo via api.telegram.org failed: {e}")
                await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)
            return

        # ── Обычный путь (yt-dlp) для остальных сервисов ──
        await handle_download_result(
            result=result,
            media_info=info,
            chat_id=chat_id,
            service=service,
            message_id=message.message_id,
        )

        logger.info(f"[async_download_audio] Завершено для chat_id={chat_id}, status={result.status}")

    except Exception as e:
        logger.exception(f"[async_download_audio] Ошибка при скачивании аудио: {e}")
        await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)
    finally:
        user_activity_queue.delete_download(chat_id=chat_id)
        logger.debug(f"[async_download_audio] Очередь очищена для chat_id={chat_id}")


async def handle_download_result(
        chat_id: int,
        service: str,
        message_id: int,
        media_info: dict,
        result: AbstractResultModel,
):
    """
    Обрабатывает результат загрузки: отправка медиа или сообщение об ошибке.
    """
    logger.info(f"[handle_download_result] Обработка результата: chat_id={chat_id}, status={result.status}")

    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"[handle_download_result] Не удалось удалить сообщение {message_id}: {e}")

    if result.status == "success":
        logger.info("[handle_download_result] Загрузка успешна, начинаю отправку аудио пользователю")

        message = await bot.send_message(chat_id=chat_id, text=MessageTemplates.SENDING_AUDIO)

        # Защита от отсутствия media_info в кеше
        if media_info and media_info.get("data"):
            url = media_info["data"].get("url", "")
            author_name = media_info["data"].get("author_name", "Unknown")
        else:
            url = ""
            author_name = "Unknown"
            logger.warning(f"[handle_download_result] media_info отсутствует в кеше для chat_id={chat_id}")

        caption = MessageTemplates.DOWNLOAD_AUDIO_CAPTION.format(
            service=service,
            url=url,
            botname=settings.telegram.name,
            author_name=author_name,
        )

        try:
            async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_VOICE):
                await bot.send_audio(
                    chat_id=chat_id,
                    caption=caption,
                    audio=FSInputFile(path=result.data.path)
                )
                logger.info(f"[handle_download_result] Аудио успешно отправлено пользователю (chat_id={chat_id})")
        except Exception as e:
            logger.exception(f"[handle_download_result] Ошибка при отправке аудио: {e}")
            await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)

        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
        except Exception as e:
            logger.warning(f"[handle_download_result] Не удалось удалить сообщение SENDING_AUDIO: {e}")

    else:
        logger.error(f"[handle_download_result] Ошибка загрузки: status={result.status}, chat_id={chat_id}")
        await bot.send_message(chat_id=chat_id, text=MessageTemplates.DOWNLOAD_AUDIO_ERROR)


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
        f"[download_audio] Celery-задача запущена: chat_id={chat_id}, service={service}, url={url}, direct={direct}")

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
        logger.info(f"[download_audio] Celery-задача завершена: chat_id={chat_id}")
    except Exception as e:
        logger.exception(f"[download_audio] Ошибка выполнения celery-задачи: {e}")